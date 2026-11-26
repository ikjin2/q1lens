"""Bounded views of sequencer and port output for the Hardware outputs rack.

A scene holds every modeled sequencer of a project, its routing, and one value for each
free parameter. Values the project does not supply are assumed and reported as such.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

from q1timeline.analysis.expr import Expr

from .model import Output, Repeat

EXACT_SAMPLES = 32_768    # Longer windows show the range each column covers instead of every sample.
MAX_BINS = 2000
MAX_INTERVALS = 400
MAX_ITERATIONS = 24       # More visible iterations than this collapse to one labeled body.
WAVEFORM_BUDGET = 400_000  # Samples of waveform memory embedded for live sampling.
FILTER_HORIZON = 300_000  # Feedback filters stream from time zero; cap that cost.

_ROLE_ORDER = ("count", "time", "waveform", "gain", "offset", "frequency", "phase", "sample")
_ROLE_VALUES = {
    "count": (3, 2, 1), "time": (100, 40, 8, 4, 1000), "waveform": (0, 1), "gain": (16384, 8192, 1, 0),
    "offset": (4096, 0), "frequency": (40_000_000, 0), "phase": (0,), "sample": (0,),
}


def parameter_roles(model: Output) -> dict[str, list[str]]:
    """Name what each free parameter controls, e.g. a wait length or a gain."""
    roles: dict[str, list[str]] = {}

    def note(expr, role):
        for name in expr.free_symbols():
            if name in model.parameter_names and role not in roles.setdefault(name, []):
                roles[name].append(role)

    def visit(nodes):
        for node in nodes:
            if isinstance(node, Repeat):
                note(node.count, "count")
                note(node.start, "time")
                note(node.period, "time")
                visit(node.body)
                continue
            note(node.start, "time")
            note(node.end, "time")
            note(node.sample_offset, "sample")
            for path in (0, 1):
                note(node.gain[path], "gain")
                note(node.offset[path], "offset")
                if isinstance(node.waveforms[path], Expr):
                    note(node.waveforms[path], "waveform")
            note(node.frequency, "frequency")
            note(node.phase, "phase")

    visit((*model.segments, model.tail) if model.tail else model.segments)
    return {name: sorted(found, key=_ROLE_ORDER.index) for name, found in roles.items()}


def assume_parameters(models: Mapping[str, Output], known: Mapping[str, int]):
    """Choose a drawable value for each parameter the project leaves open."""
    roles: dict[str, list[str]] = {}
    for model in models.values():
        for name, found in parameter_roles(model).items():
            merged = roles.setdefault(name, [])
            merged.extend(role for role in found if role not in merged)
    names = sorted(name for name in roles if name not in known)
    options = {name: _ROLE_VALUES[min(roles[name], key=_ROLE_ORDER.index)] for name in names}
    choice = dict.fromkeys(names, 0)
    values = {}
    for _ in range(32):
        values = {name: options[name][choice[name]] for name in names}
        failed = None
        for model in models.values():
            if model.issues:
                continue
            try:
                model.bind({k: v for k, v in {**known, **values}.items() if k in model.parameter_names})
            except ValueError as error:
                failed = next((name for name in names if choice[name] + 1 < len(options[name])
                               and re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", str(error))), None)
                break
        if failed is None:
            break
        choice[failed] += 1
    return values, roles


def _number(value: float) -> str:
    return f"{value:.4g}"


def _scaled(expr: Expr, divisor: int) -> str:
    text = _symbols(expr.display())
    return f"({text})/{divisor}" if " " in text else f"{text}/{divisor}"


def _symbols(text: str) -> str:
    # The builder names a loop index #L0, #L1, ... Show it as an ordinary index variable.
    return re.sub(r"#L(\d+)", lambda match: "k" if match.group(1) == "0" else f"k{match.group(1)}", text)


def _path_label(model: Output, node, path: int) -> str:
    terms = []
    wave = node.waveforms[path]
    if wave is not None:
        name = f"wf{_symbols(wave.display()) if isinstance(wave, Expr) else wave}"
        if node.sample_offset.op != "const" or node.sample_offset.value:
            name += f"[τ + {_symbols(node.sample_offset.display())}]"
        gain, static = node.gain[path], model.static_gain[path]
        if gain.op == "const":
            factor = gain.value / 32768 * static
            if factor:
                terms.append(name if factor == 1 else f"{_number(factor)} × {name}")
        else:
            terms.append(f"{_scaled(gain, 32768)}{'' if static == 1 else f' × {static:g}'} × {name}")
    offset, static = node.offset[path], model.static_offset[path]
    if offset.op == "const":
        if offset.value / 32768 + static:
            terms.append(_number(offset.value / 32768 + static))
    else:
        terms.append(_scaled(offset, 32768))
        if static:
            terms.append(_number(static))
    return " + ".join(terms).replace("+ -", "− ") or "0"


def _envelope(values, edges):
    low, high = [], []
    base = edges[0]
    for a, b in zip(edges, edges[1:]):
        part = values[a - base:max(b, a + 1) - base]
        low.append(round(min(part), 5))
        high.append(round(max(part), 5))
    return low, high


class Scene:
    """Bound output models with routing, ready to be sampled window by window."""

    def __init__(self, models: Mapping[str, Output], ports: list[dict], offsets: Mapping[str, int],
                 project: Mapping[str, int] | None = None, trial: Mapping[str, int] | None = None):
        self.base = dict(models)
        self.ports = ports
        self.offsets = {seq: offsets.get(seq, 0) for seq in self.base}
        self._slices: dict[tuple[int, int, int], tuple[float, float]] = {}
        self.project = {k: v for k, v in (project or {}).items() if type(v) is int}
        self.rebind(trial or {})

    def rebind(self, trial: Mapping[str, int]) -> None:
        names = set().union(*(model.parameter_names for model in self.base.values())) if self.base else set()
        trial = {k.removeprefix("$"): v for k, v in trial.items()}
        if any(type(v) is not int for v in trial.values()) or trial.keys() - names:
            raise ValueError("Trial values must be integers for parameters of this output")
        known = {**{k: v for k, v in self.project.items() if k in names}, **trial}
        assumed, roles = assume_parameters(self.base, known)
        values = {**known, **assumed}
        self.assumed = frozenset(assumed)
        self.models: dict[str, Output] = {}
        self.issues: dict[str, str] = {}
        for seq, model in self.base.items():
            if model.issues:
                self.issues[seq] = "; ".join(model.issues)
                continue
            try:
                self.models[seq] = model.bind({k: v for k, v in values.items() if k in model.parameter_names})
            except ValueError as error:
                self.issues[seq] = str(error)
        self.parameters = [
            {"name": name, "value": values.get(name), "roles": roles.get(name, []),
             "source": "trial" if name in trial else "assumed" if name in assumed else "project"}
            for name in sorted(names)
        ]

    def _end(self, seq: str) -> int:
        model = self.models[seq]
        return max(model.waveform_end.evaluate(model.params), model.duration.evaluate(model.params))

    def extent(self) -> dict[str, int]:
        spans = [(self.offsets[seq], self.offsets[seq] + self._end(seq)) for seq in self.models]
        if not spans:
            return {"t0": 0, "t1": 1}
        return {"t0": min(lo for lo, _ in spans), "t1": max(max(hi for _, hi in spans), min(lo for lo, _ in spans) + 1)}

    def _stage(self, seq: str, end: int) -> str:
        model = self.models[seq]
        return "nco" if model.filtered and any(model.iir_a[1:]) and end > FILTER_HORIZON else "filtered"

    def _pair(self, seq: str, a: int, b: int):
        """I and Q on the shared axis; a sequencer is silent before its own time zero."""
        origin = self.offsets[seq]
        lo = max(a - origin, 0)
        hi = b - origin
        if hi <= lo:
            return [0.0] * (b - a), [0.0] * (b - a)
        i, q = self.models[seq].samples(lo, hi, stage=self._stage(seq, hi))
        pad = [0.0] * (lo - (a - origin))
        return pad + list(i), pad + list(q)

    def _slice_range(self, wave, left: int, right: int) -> tuple[float, float]:
        key = (id(wave), left, right)
        if key not in self._slices:
            part = wave[left:right]
            self._slices[key] = (min(part), max(part))
        return self._slices[key]

    def _range(self, seq: str, a: int, b: int):
        """Range of I and Q over local [a, b), from the interval structure instead of every sample.

        A loop contributes its first and last visible iterations, which bound a ramp that changes
        linearly per iteration. With the NCO running, the range is the envelope magnitude. Digital
        filters are not applied; the caller reports this view as a range, not as samples.
        """
        model = self.models[seq]
        low, high = [math.inf, math.inf], [-math.inf, -math.inf]

        def add(path, lo, hi):
            low[path] = min(low[path], lo)
            high[path] = max(high[path], hi)

        if a < 0:
            add(0, 0.0, 0.0)
            add(1, 0.0, 0.0)
        a = max(a, 0)

        def segment(node, origin, scope, end=None):
            start = origin + node.start.evaluate(scope)
            lo, hi = max(a, start), min(b, origin + node.end.evaluate(scope) if end is None else end)
            if hi <= lo:
                return
            first = node.sample_offset.evaluate(scope) + lo - start
            count = hi - lo
            bounds = []
            for path, wave in enumerate(model._waves(node, scope)):
                left, right = max(0, first), min(len(wave), first + count)
                least, most = self._slice_range(wave, left, right) if right > left else (0.0, 0.0)
                if first < 0 or first + count > len(wave):
                    least, most = min(least, 0.0), max(most, 0.0)
                gain = node.gain[path].evaluate(scope) / 32768 * model.static_gain[path]
                offset = node.offset[path].evaluate(scope) / 32768 + model.static_offset[path]
                ends = (gain * least + offset, gain * most + offset)
                bounds.append((min(ends), max(ends)))
            if node.phase.evaluate(scope) or node.frequency.evaluate(scope):
                radius = math.hypot(max(map(abs, bounds[0])), max(map(abs, bounds[1])))
                bounds = [(-radius, radius)] * 2
            for path in (0, 1):
                add(path, *bounds[path])

        def walk(nodes, origin, scope):
            for node in nodes:
                if not isinstance(node, Repeat):
                    segment(node, origin, scope)
                    continue
                start = origin + node.start.evaluate(scope)
                period, count = node.period.evaluate(scope), node.count.evaluate(scope)
                if period <= 0 or start + count * period <= a or start >= b:
                    continue
                first = max(0, (a - start) // period)
                last = min(count, -(-(b - start) // period))
                chosen = range(first, last) if last - first <= 4 else (first, first + 1, last - 2, last - 1)
                for k in chosen:
                    walk(node.body, start + k * period, {**scope, node.iteration: k})

        walk(model.segments, 0, model.params)
        if model.tail and b > model.duration.evaluate(model.params):
            segment(model.tail, 0, model.params, end=b)
        return [(low[path] if low[path] != math.inf else 0.0, high[path] if high[path] != -math.inf else 0.0)
                for path in (0, 1)]

    def _layout(self, seq: str, lo: int, hi: int) -> dict:
        model = self.models[seq]
        intervals, repeats = [], []
        state = {"left": MAX_INTERVALS, "truncated": False}

        def assumed(*expressions):
            names = set().union(*(expr.free_symbols() for expr in expressions))
            return sorted(names & self.assumed)

        def segment(node, origin, scope, end=None):
            start = origin + node.start.evaluate(scope)
            end = origin + node.end.evaluate(scope) if end is None else end
            if end <= lo or start >= hi or end <= start:
                return
            if state["left"] <= 0:
                state["truncated"] = True
                return
            state["left"] -= 1
            entry = {"t0": start, "t1": end, "labels": [_path_label(model, node, path) for path in (0, 1)],
                     "event": node.source_event, "assumed": assumed(*node._expressions())}
            if node.frequency.op != "const" or node.frequency.value:
                frequency = node.frequency
                entry["nco"] = (f"{_number(frequency.value / 4)} Hz" if frequency.op == "const"
                                else f"{_scaled(frequency, 4)} Hz")
            intervals.append(entry)

        def walk(nodes, origin, scope, depth):
            for node in nodes:
                if not isinstance(node, Repeat):
                    segment(node, origin, scope)
                    continue
                start = origin + node.start.evaluate(scope)
                period, count = node.period.evaluate(scope), node.count.evaluate(scope)
                if period <= 0 or start + count * period <= lo or start >= hi:
                    continue
                first = max(0, (lo - start) // period)
                last = min(count, -(-(hi - start) // period))
                collapsed = last - first > MAX_ITERATIONS
                # A loop counter of zero wraps to 2**32 iterations; the count expression spells that out.
                wraps = re.fullmatch(r"loop_count\((.+), 0 → 2³²\)", node.count.display())
                repeats.append({
                    "t0": start, "t1": start + count * period, "period": period, "count": count,
                    "count_label": _symbols(wraps.group(1) if wraps else node.count.display()),
                    "wraps_at_zero": bool(wraps), "period_label": _symbols(node.period.display()),
                    "index": _symbols(node.iteration), "depth": depth, "collapsed": collapsed,
                    "shown": first if collapsed else None, "event": node.source_event,
                    "assumed": assumed(node.start, node.count, node.period),
                })
                for k in ([first] if collapsed else range(first, last)):
                    walk(node.body, start + k * period, {**scope, node.iteration: k}, depth + 1)

        walk(model.segments, 0, model.params, 0)
        duration = model.duration.evaluate(model.params)
        end = self._end(seq)
        if model.tail and end > duration:
            segment(model.tail, 0, model.params, end=end)
        return {"intervals": intervals, "repeats": repeats, "truncated": state["truncated"],
                "end": duration, "waveform_end": end}

    def window(self, t0: int, t1: int, bins: int = 720) -> dict:
        if type(t0) is not int or type(t1) is not int or t1 <= t0 or type(bins) is not int or bins < 1:
            raise ValueError("Expected an integer-ns window with t0 < t1 and a positive bin count")
        span = t1 - t0
        bins = min(bins, span, MAX_BINS)
        exact = span <= EXACT_SAMPLES
        edges = [t0 + span * j // bins for j in range(bins + 1)]
        blocks = [(t0, t1)] if exact else list(zip(edges, edges[1:]))
        needed = list(self.models)
        series: dict[tuple, tuple[list, list]] = {}

        def record(key, values, a, b):
            low, high = series.setdefault(key, ([], []))
            if exact:
                lows, highs = _envelope(values, edges)
                low.extend(lows)
                high.extend(highs)
            else:
                low.append(round(values[0], 5))
                high.append(round(values[1], 5))

        clip_bins: dict[int, list[int]] = {}
        port_issue: dict[int, str] = {}
        for number, port in enumerate(self.ports):
            missing = [seq for seq in port["routes"] if seq not in self.models]
            if missing:
                port_issue[number] = f"{missing[0]}: {self.issues.get(missing[0], 'no output model')}"
            elif len(port["routes"]) > 1 and not port.get("aligned", True):
                port_issue[number] = "Exact alignment is required to mix this port's sequencers."
        failed: dict[str, str] = {}
        for index, (a, b) in enumerate(blocks):
            pairs = {}
            for seq in needed:
                if seq in failed:
                    continue
                try:
                    # Either every sample of the block, or one (least, most) pair per path.
                    pairs[seq] = self._pair(seq, a, b) if exact else self._range(
                        seq, a - self.offsets[seq], b - self.offsets[seq])
                except ValueError as error:
                    failed[seq] = str(error)
                    continue
                record(("seq", seq, 0), pairs[seq][0], a, b)
                record(("seq", seq, 1), pairs[seq][1], a, b)
            for number, port in enumerate(self.ports):
                if number in port_issue or any(seq not in pairs for seq in port["routes"]):
                    continue
                empty = [0.0] * (b - a) if exact else [0.0, 0.0]
                if port["kind"] == "rf":
                    for path in (0, 1):
                        total = [math.fsum(column) for column in zip(*(pairs[seq][path] for seq in port["routes"]))]
                        record(("port", number, path), total or empty, a, b)
                    continue
                columns = [pairs[seq][int(path == "Q")] for seq, path in port["routes"].items()]
                dc = port.get("offset", 0.0)
                # Summing per-path ranges bounds the mixed output; with one contributor it is exact.
                total = [dc + math.fsum(column) for column in zip(*columns)] if columns else [dc + v for v in empty]
                if any(abs(v) > 1 for v in total):
                    if exact:
                        base = edges[0]
                        clip_bins.setdefault(number, []).extend(
                            j for j, (lo, hi) in enumerate(zip(edges, edges[1:]))
                            if any(abs(v) > 1 for v in total[lo - base:max(hi, lo + 1) - base]))
                    else:
                        clip_bins.setdefault(number, []).append(index)
                record(("port", number, 0), [min(1.0, max(-1.0, v)) for v in total], a, b)
        for seq, message in failed.items():
            self.issues.setdefault(seq, message)

        def trace(key):
            low, high = series.get(key, ([], []))
            return {"min": low, "max": high}

        ports = []
        for number, port in enumerate(self.ports):
            lane = {"module": port["module"], "port": port["port"], "kind": port["kind"],
                    "offset": port.get("offset", 0.0), "routes": dict(port["routes"])}
            broken = next((seq for seq in port["routes"] if seq in failed), None)
            if number in port_issue or broken:
                lane["issue"] = port_issue.get(number) or f"{broken}: {failed[broken]}"
            elif port["kind"] == "rf":
                lane["traces"] = {"i": trace(("port", number, 0)), "q": trace(("port", number, 1))}
            else:
                lane["traces"] = {"sum": trace(("port", number, 0))}
                if number in clip_bins:
                    lane["clip_bins"] = clip_bins[number]
            ports.append(lane)
        sequencers = {}
        for seq in self.base:
            if seq in self.issues or seq not in self.models:
                sequencers[seq] = {"issue": self.issues.get(seq, "No output model")}
                continue
            origin = self.offsets[seq]
            entry = self._layout(seq, t0 - origin, t1 - origin)
            entry.update(time_offset=origin, traces={"i": trace(("seq", seq, 0)), "q": trace(("seq", seq, 1))},
                         stage=self._stage(seq, t1 - origin))
            sequencers[seq] = entry
        result = {"t0": t0, "t1": t1, "bins": bins, "exact": exact, "extent": self.extent(),
                  "parameters": self.parameters, "ports": ports, "sequencers": sequencers}
        return result


def embedded_waveforms(models: Mapping[str, Output]) -> dict[str, dict]:
    """Waveform memory each restored model needs, within one shared size budget."""
    used: dict[str, dict[int, tuple[float, ...]]] = {}
    for seq, model in models.items():
        indices: set[int] | None = set()

        def visit(nodes):
            nonlocal indices
            for node in nodes:
                if isinstance(node, Repeat):
                    visit(node.body)
                elif indices is not None:
                    for wave in node.waveforms:
                        if isinstance(wave, Expr):
                            indices = None
                            return
                        if wave is not None:
                            indices.add(wave)

        visit((*model.segments, model.tail) if model.tail else model.segments)
        used[seq] = {index: data for index, data in model.waveforms.items() if indices is None or index in indices}
    total = sum(len(data) for memory in used.values() for data in memory.values())
    if total > WAVEFORM_BUDGET:
        return {}
    return {seq: {str(index): list(data) for index, data in memory.items()} for seq, memory in used.items()}


def scene_from_output(output: Mapping, trial: Mapping[str, int] | None = None) -> Scene:
    """Restore the scene serialized in a TimelineIR `output` section."""
    if not isinstance(output, Mapping) or not isinstance(output.get("sequencers"), Mapping):
        raise ValueError("Expected a TimelineIR output section")
    models = {}
    for seq, lane in output["sequencers"].items():
        data = lane.get("symbolic") or lane
        waveforms = lane.get("waveforms")
        model = Output.from_json(data, waveforms)
        if waveforms is None and not model.issues:
            model = Output((), {}, None, ("Waveform memory is too large to sample here. Refresh the analysis to redraw.",))
        models[seq] = model
    return Scene(models, list(output.get("ports", [])), output.get("time_offsets", {}),
                 {item["name"]: item["value"] for item in output.get("parameters", []) if item.get("source") == "project"},
                 trial)
