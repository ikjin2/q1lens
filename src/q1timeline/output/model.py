"""Piecewise sequencer output on the one-nanosecond waveform-memory grid."""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from html import escape
from operator import mul

from q1timeline.analysis.expr import Expr
from q1timeline.simulation.signals import _phase_turns, _quantize, _rotation

ZERO = Expr.const(0)
UNITY = Expr.const(32768)
DAC_ASSUMPTION = "The floating-point DAC model clips and rounds to nearest even. Gateware rounding is unverified."


def _json(expr):
    if expr.op == "const":
        return {"kind": "concrete", "value": expr.value, "display": str(expr)}
    return {"kind": "symbolic", "expr": str(expr), "display": expr.display(), "tree": expr.to_json()}


@dataclass(frozen=True)
class Segment:
    start: Expr
    end: Expr
    waveforms: tuple[int | Expr | None, int | Expr | None]
    sample_offset: Expr
    gain: tuple[Expr, Expr]
    offset: tuple[Expr, Expr]
    frequency: Expr
    phase: Expr  # phase at start, in 1 / (4 * 10**9) turns
    source_event: str

    def _expressions(self):
        return (self.start, self.end, self.sample_offset, *self.gain, *self.offset, self.frequency, self.phase,
                *(wave for wave in self.waveforms if isinstance(wave, Expr)))

    def to_json(self):
        return {
            "kind": "segment", "start": _json(self.start), "end": _json(self.end),
            "waveforms": [_json(w) if isinstance(w, Expr) else w for w in self.waveforms],
            "sample_offset": _json(self.sample_offset),
            "gain": list(map(_json, self.gain)), "offset": list(map(_json, self.offset)),
            "frequency": _json(self.frequency), "phase": _json(self.phase), "source_event": self.source_event,
        }


@dataclass(frozen=True)
class Repeat:
    start: Expr
    count: Expr
    period: Expr
    iteration: str
    body: tuple[Segment | Repeat, ...]
    source_event: str

    @cached_property
    def _body_symbols(self) -> frozenset[str]:
        return _free_symbols(self.body)

    def to_json(self):
        return {"kind": "repeat", "start": _json(self.start), "count": _json(self.count),
                "period": _json(self.period), "iteration": self.iteration, "source_event": self.source_event,
                "body": [node.to_json() for node in self.body]}


def _free_symbols(nodes) -> frozenset[str]:
    result = set()
    seen = set()

    def visit(expr):
        if id(expr) in seen:
            return
        seen.add(id(expr))
        if expr.op == "symbol":
            result.add(expr.value)
        for arg in expr.args:
            visit(arg)

    for node in nodes:
        if isinstance(node, Repeat):
            result.update(node._body_symbols - {node.iteration})
            operands = (node.start, node.count, node.period)
        else:
            operands = node._expressions()
        for operand in operands:
            visit(operand)
    return frozenset(result)


@dataclass(frozen=True)
class _WindowIndex:
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    params: dict[str, int]
    tail: tuple[int, int] | None

    @classmethod
    def ordered(cls, intervals, params, tail):
        starts, ends = [], []
        for start, end in intervals:
            if end < start or (ends and start < ends[-1]):
                return None
            starts.append(start)
            ends.append(end)
        return cls(tuple(starts), tuple(ends), params, tail)


@dataclass(frozen=True)
class Output:
    segments: tuple[Segment | Repeat, ...]
    waveforms: dict[int, tuple[float, ...]]
    tail: Segment | None
    issues: tuple[str, ...] = ()
    params: Mapping[str, int] = field(default_factory=dict, repr=False)
    static_gain: tuple[float, float] = (1.0, 1.0)
    static_offset: tuple[float, float] = (0.0, 0.0)
    fir: tuple[float, ...] = (1.0,)
    iir_b: tuple[float, ...] = (1.0,)
    iir_a: tuple[float, ...] = (1.0,)
    dac_bits: int = 16

    @property
    def filtered(self):
        return any(coefficients != (1.0,) for coefficients in (self.fir, self.iir_b, self.iir_a))

    @property
    def duration(self):
        return self.tail.start if self.tail else ZERO

    @property
    def waveform_end(self):
        """End of finite waveform data, including memory that continues after stop."""
        if self.tail is None or self.tail.waveforms == (None, None):
            return self.duration
        def length(w):
            if w is None:
                return ZERO
            if isinstance(w, int):
                return Expr.const(len(self.waveforms[w]))
            # On the bound index domain this step function selects the uploaded length.
            terms = []
            previous = 0
            for index, data in sorted(self.waveforms.items()):
                if len(data) != previous:
                    step = (w - index + 1).maximum(0).minimum(1)
                    terms.append((len(data) - previous) * step)
                previous = len(data)
            while len(terms) > 1:
                terms = [terms[i] + terms[i + 1] if i + 1 < len(terms) else terms[i] for i in range(0, len(terms), 2)]
            return terms[0] if terms else ZERO
        i, q = map(length, self.tail.waveforms)
        return self.duration + (i.maximum(q) - self.tail.sample_offset).maximum(0)

    def bind(self, params: Mapping[str, int]):
        if not isinstance(params, Mapping) or any(not isinstance(k, str) or type(v) is not int for k, v in params.items()):
            raise ValueError("Output parameters must map symbol names to integer Q1 words")
        supplied = {k.removeprefix("$"): v for k, v in params.items()}
        unknown = supplied.keys() - self.parameter_names
        if unknown:
            raise ValueError(f"Unknown output parameter(s): {', '.join(sorted(unknown))}")
        values = {**self.params, **supplied}
        bound = replace(self, params=values)
        # Successive top-level packet boundaries share dwell terms under this binding.
        terms = {}
        def time_value(expr):
            if expr.op != "+" or not expr.args:
                return expr.evaluate(values)
            total = 0
            for term in expr.args:
                key = id(term)
                if key not in terms:
                    terms[key] = term.evaluate(values)
                total += terms[key]
            return total

        # Validate interval endpoints including affine ramps, without enumerating a loop.
        def validate(nodes, scope, windows=None):
            for node in nodes:
                if isinstance(node, Repeat):
                    node.start.evaluate(scope)
                    count = node.count.evaluate(scope)
                    node.period.evaluate(scope)
                    if count:
                        if node.iteration in node._body_symbols:
                            for k in {0, count - 1}:
                                validate(node.body, {**scope, node.iteration: k})
                        else:
                            validate(node.body, scope)
                else:
                    expressions = node._expressions()
                    if windows is not None:
                        windows.append((time_value(node.start), time_value(node.end)))
                        expressions = expressions[2:]
                    for expr in expressions:
                        expr.evaluate(scope)
                    bound._waves(node, scope)
        try:
            windows = [] if (all(isinstance(n, Segment) for n in self.segments)
                             and any(n.start.op != "const" or n.end.op != "const" for n in self.segments)
                             and all(type(v) is int for v in values.values())) else None
            validate(bound.segments, values, windows)
            tail_window = [] if windows is not None else None
            if bound.tail:
                validate((bound.tail,), values, tail_window)
        except KeyError as error:
            raise ValueError(f"Missing output parameter {error.args[0]}") from error
        if windows:
            index = _WindowIndex.ordered(windows, values.copy(), tail_window[0] if tail_window else None)
            object.__setattr__(bound, "_bound_window_index", index)
        return bound

    @cached_property
    def parameter_names(self) -> frozenset[str]:
        """External symbols accepted by bind, including previously bound symbols."""
        return _free_symbols((*self.segments, self.tail) if self.tail else self.segments)

    def _waves(self, node, scope):
        result = []
        for w in node.waveforms:
            index = w.evaluate(scope) if isinstance(w, Expr) else w
            if index is not None and index not in self.waveforms:
                raise ValueError(f"Missing waveform {index} for event {node.source_event}")
            result.append(self.waveforms[index] if index is not None else ())
        return result

    @cached_property
    def _fixed_ordered_segments(self) -> bool:
        previous_end = 0
        for node in self.segments:
            if not isinstance(node, Segment) or node.start.op != "const" or node.end.op != "const":
                return False
            if not previous_end <= node.start.value <= node.end.value:
                return False
            previous_end = node.end.value
        return True

    def samples(self, t0: int, t1: int, *, stage: str = "dac", dac_bits: int | None = None,
                quantizer: Callable[[float, int], float] = _quantize) -> tuple[list[float], list[float]]:
        """Return I/Q on [t0,t1). Feedback filters stream their required zero-state history."""
        if self.issues:
            raise ValueError("; ".join(self.issues))
        if type(t0) is not int or type(t1) is not int or not 0 <= t0 <= t1:
            raise ValueError("Expected 0 <= t0 <= t1 in integer ns")
        if stage not in ("waveform", "gain_offset", "nco", "fir", "filtered", "dac"):
            raise ValueError(f"Unknown output stage: {stage}")
        if dac_bits is None:
            dac_bits = self.dac_bits
        if type(dac_bits) is not int or not 2 <= dac_bits <= 24:
            raise ValueError("dac_bits must be an integer in 2..24")
        if self.filtered and stage in ("fir", "filtered", "dac"):
            return self._filtered_samples(t0, t1, stage, dac_bits, quantizer)
        if stage in ("fir", "filtered"):
            stage = "nco"
        result = ([0.0] * (t1 - t0), [0.0] * (t1 - t0))

        def draw(node, origin, scope, *, tail=False, bounds=None):
            start = origin + node.start.evaluate(scope) if bounds is None else bounds[0]
            if isinstance(node, Repeat):
                period, count = node.period.evaluate(scope), node.count.evaluate(scope)
                if period > 0:
                    for k in range(max(0, (t0 - start) // period), min(count, (t1 - start + period - 1) // period)):
                        for child in node.body:
                            draw(child, start + k * period, {**scope, node.iteration: k})
                return
            lo = max(t0, start)
            hi = t1 if tail else min(t1, origin + node.end.evaluate(scope) if bounds is None else bounds[1])
            if hi <= lo:
                return
            index = node.sample_offset.evaluate(scope)
            wave_i, wave_q = self._waves(node, scope)
            length_i, length_q = len(wave_i), len(wave_q)
            gains = [g.evaluate(scope) / 32768 * static for g, static in zip(node.gain, self.static_gain)]
            offsets = [o.evaluate(scope) / 32768 + static for o, static in zip(node.offset, self.static_offset)]
            phase, frequency = node.phase.evaluate(scope), node.frequency.evaluate(scope)
            first = index + lo - start
            length = hi - lo
            if stage == "waveform" and 0 <= first and first + length <= min(length_i, length_q):
                result[0][lo - t0:hi - t0] = wave_i[first:first + length]
                result[1][lo - t0:hi - t0] = wave_q[first:first + length]
                return
            # Outside waveform memory, a stationary offset produces the same sample on every tick.
            constant = ((first >= max(length_i, length_q) or first + length <= 0)
                        and (stage in ("waveform", "gain_offset") or not frequency)
                        and (stage != "dac" or quantizer is _quantize))
            for time in range(lo, lo + 1 if constant else hi):
                i = index + time - start
                a = wave_i[i] if 0 <= i < length_i else 0.0
                b = wave_q[i] if 0 <= i < length_q else 0.0
                if stage != "waveform":
                    a, b = a * gains[0] + offsets[0], b * gains[1] + offsets[1]
                if stage in ("nco", "dac") and (phase or frequency):
                    turns = _phase_turns(phase + frequency * (time - start))
                    modulated = complex(a, b) * _rotation(turns)
                    a, b = modulated.real, modulated.imag
                if stage == "dac":
                    a, b = quantizer(a, dac_bits), quantizer(b, dac_bits)
                result[0][time - t0], result[1][time - t0] = a, b
            if constant and length > 1:
                result[0][lo - t0:hi - t0] = [a] * length
                result[1][lo - t0:hi - t0] = [b] * length
        try:
            nodes = self.segments
            tail_bounds = None
            index = getattr(self, "_bound_window_index", None)
            # A custom quantizer can change parameters between samples; a caller can also rebind them in place.
            if index is not None and quantizer is _quantize and all(self.params.get(k) is v for k, v in index.params.items()):
                tail_bounds = index.tail
                for i in range(bisect_right(index.ends, t0), bisect_left(index.starts, t1)):
                    draw(nodes[i], 0, self.params, bounds=(index.starts[i], index.ends[i]))
            else:
                if self._fixed_ordered_segments:
                    first = bisect_right(nodes, t0, key=lambda node: node.end.value)
                    last = bisect_left(nodes, t1, lo=first, key=lambda node: node.start.value)
                    nodes = nodes[first:last]
                for node in nodes:
                    draw(node, 0, self.params)
            if self.tail:
                draw(self.tail, 0, self.params, tail=True, bounds=tail_bounds)
        except KeyError as error:
            raise ValueError(f"Missing output parameter {error.args[0]}") from error
        return result

    def _filtered_samples(self, t0, t1, stage, bits, quantizer):
        result = ([], [])
        if t0 == t1:
            return result
        # FIR and feed-forward taps need only a finite lookbehind. Feedback
        # depends on previous outputs, so stream from time zero in small blocks.
        history = len(self.fir) - 1
        if stage != "fir":
            history += len(self.iir_b) - 1
        begin = 0 if stage != "fir" and any(self.iir_a[1:]) else max(0, t0 - history)
        raw = deque(maxlen=len(self.fir))
        inputs = deque(maxlen=len(self.iir_b))
        outputs = deque(maxlen=len(self.iir_a) - 1)
        for block in range(begin, t1, 4096):
            paths = self.samples(block, min(block + 4096, t1), stage="nco")
            for time, (i, q) in enumerate(zip(*paths), block):
                raw.appendleft(complex(i, q))
                sample = sum(map(mul, self.fir, raw))
                if stage != "fir":
                    inputs.appendleft(sample)
                    sample = ((sum(map(mul, self.iir_b, inputs))
                               - sum(map(mul, self.iir_a[1:], outputs))) / self.iir_a[0])
                    outputs.appendleft(sample)
                if not math.isfinite(sample.real) or not math.isfinite(sample.imag):
                    raise ValueError("Non-finite digital filter output")
                if time >= t0:
                    i, q = sample.real, sample.imag
                    result[0].append(quantizer(i, bits) if stage == "dac" else i)
                    result[1].append(quantizer(q, bits) if stage == "dac" else q)
        return result

    def to_json(self):
        return {"status": "unavailable" if self.issues else "modeled", "issues": list(self.issues),
                "segments": [node.to_json() for node in self.segments], "duration": _json(self.duration),
                "waveform_end": _json(self.waveform_end), "tail": self.tail.to_json() if self.tail else None,
                "static_gain": list(self.static_gain), "static_offset": list(self.static_offset),
                "fir": list(self.fir), "iir": {"a": list(self.iir_a), "b": list(self.iir_b)}, "dac_bits": self.dac_bits,
                "dac_assumption": DAC_ASSUMPTION}

    def _repr_html_(self):
        def scaled(expr):
            text = expr.display()
            return f"({text})/32768" if " " in text else f"{text}/32768"

        def rows(nodes, indent=""):
            for node in nodes:
                if isinstance(node, Repeat):
                    yield (indent + f"Repeat × {node.count.display()}", node.start.display(),
                           f"period {node.period.display()} ns", node.iteration)
                    yield from rows(node.body, indent + "↳ ")
                else:
                    formulas = []
                    for path, wave in enumerate(node.waveforms):
                        terms = []
                        if wave is not None:
                            sample = Expr.symbol("t") + (node.sample_offset - node.start)
                            wave_label = wave.display() if isinstance(wave, Expr) else str(wave)
                            factors = [] if node.gain[path] == UNITY else [scaled(node.gain[path])]
                            if self.static_gain[path] != 1:
                                factors.append(f"({self.static_gain[path]:g})")
                            factors.append(f"wf{wave_label}[{sample.display()}]")
                            terms.append(" · ".join(factors))
                        if node.offset[path] != ZERO:
                            terms.append(scaled(node.offset[path]))
                        if self.static_offset[path]:
                            terms.append(f"{self.static_offset[path]:g}")
                        formulas.append(" + ".join(terms).replace(" + -", " - ") or "0")
                    yield (indent + f"[{node.start.display()}, {node.end.display()})", *formulas, node.source_event)
        body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows(self.segments))
        issues = "".join(f"<p>{escape(issue)}</p>" for issue in self.issues)
        return ("<table><thead><tr><th>Interval (ns)</th><th>Path I</th><th>Path Q</th><th>Event / index</th>"
                f"</tr></thead><tbody>{body}</tbody></table>{issues}<small>{escape(DAC_ASSUMPTION)}</small>")
