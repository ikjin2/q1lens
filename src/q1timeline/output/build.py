"""Lower the RT packets and compact-loop evidence produced by Analysis."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from numbers import Integral, Real

from q1timeline.analysis.expr import Expr
from q1timeline.analysis.values import Concrete, Symbolic, value_to_json
from q1timeline.q1asm.core_rt import ONE_SHOTS, rt_dequeue_spacing_ns
from q1timeline.simulation.signals import validate_physical_config
from .model import Output, Repeat, Segment, UNITY, ZERO


class _Unavailable(ValueError):
    """An expected input or Analysis limitation that can be shown on the output lane."""


def _as_expr(raw, field):
    if isinstance(raw, Mapping) and raw.get("kind") in {"unknown", "runtime_dependent", "range"}:
        reason = raw.get("reason") or raw.get("source") or raw["display"]
        raise _Unavailable(f"{field} is unresolved ({reason})")
    if isinstance(raw, str) or isinstance(raw, Mapping) and raw.get("kind") == "symbolic" and "tree" not in raw:
        legacy = raw if isinstance(raw, str) else raw["expr"]
        try:
            return Expr.parse(legacy)
        except ValueError as error:
            raise _Unavailable(f"{field} uses an unsupported expression: {legacy}") from error
    return Expr.from_value_json(raw)


def _timing_expr(value, field):
    if isinstance(value, Concrete):
        return Expr.const(value.value)
    if isinstance(value, Symbolic) and value.tree is not None:
        return value.tree
    return _as_expr(value_to_json(value), field)


def _mul(a, b):
    return ZERO if a == ZERO or b == ZERO else a * b


def _delta(a, b):
    return ZERO if a == b else a - b


def _minimum(a, b):
    try:
        low, high = a.bounds({})
        left, right = b.bounds({})
        if high <= left:
            return a
        if right <= low:
            return b
    except (KeyError, ValueError):
        pass
    return a.minimum(b)


def _scaled(value, scale):
    if type(value) is not int and (type(value) is not float or not (value * scale).is_integer()):
        raise _Unavailable(f"Setting {value!r} is not representable in units of 1/{scale}")
    return Expr.const(int(value * scale))


@dataclass(frozen=True)
class _State:
    waveforms: tuple = (None, None)
    index: Expr = ZERO
    gain: tuple = (UNITY, UNITY)
    offset: tuple = (ZERO, ZERO)
    frequency: Expr = ZERO
    accumulator: Expr = ZERO
    phase_offset: Expr = ZERO

    def advance(self, duration):
        return replace(self, index=self.index + duration if any(w is not None for w in self.waveforms) else ZERO,
                       accumulator=self.accumulator + _mul(self.frequency, duration))

    def segment(self, start, end, event, mix):
        return Segment(start, end, self.waveforms, self.index, self.gain, self.offset,
                       self.frequency if mix else ZERO, self.accumulator + self.phase_offset if mix else ZERO, event)


@dataclass(frozen=True)
class _Command:
    duration: Expr
    play: tuple | None
    updates: dict
    source: str
    captures: bool


@dataclass(frozen=True)
class _Loop:
    event: object
    body: tuple


def _commands(state):
    events_by_id = {e.id: e for e in state.events}
    by_packet = defaultdict(list)
    for event in state.events:
        if (event.kind == "unknown_region" or event.meta.get("output_state_unknown")
                or event.meta.get("trigger_arrival_unknown") or event.meta.get("branch_comparison_path")):
            raise _Unavailable(f"Unresolved output at {event.source.file}:{event.source.line} ({event.kind})")
        if (event.kind != "q1_issue" and event.meta.get("loop_iteration_index", 0) == 0
                and (pid := event.meta.get("rt_packet_id"))):
            by_packet[pid].append(event)
    commands = []
    packet_index = {}
    simulated = state.metadata.get("execution", {}).get("mode") == "simulation"
    clock = 0
    for packet in state.rt_packets:
        events = by_packet[packet.id]
        applied = next((e for e in events if "applied_state" in e.meta), None)
        meta = applied.meta if applied else packet.meta
        updates = {name: (meta.get("applied_setter_event_ids", {}).get(name), value)
                   for name, value in meta.get("applied_state", {}).items()}
        if simulated:
            updates = {name: pair for name, pair in updates.items() if name in packet.meta.get("applied_updated_fields", ())}
            if packet.meta.get("phase_reset"):
                updates["phase_reset"] = (None, True)
        location = f"{packet.source.file}:{packet.source.line}"
        plays = {e.meta["path"]: (_as_expr(e.meta["waveform_index_expr"], f"Waveform index at {location}") if e.meta.get("waveform_index_expr")
                                 else e.meta["waveform_index"]) for e in events if e.kind == "play"}
        play = None
        skipped = packet.meta.get("conditional_skipped") or any(e.kind == "conditional_skip" for e in events)
        if packet.op == "play" and not skipped:
            if set(plays) != {0, 1} or any(index is None for index in plays.values()):
                raise _Unavailable(f"Unresolved waveform index at {packet.source.file}:{packet.source.line}")
            play = (plays[0], plays[1])
        if packet.op == "play_netzero":
            raise _Unavailable("play_netzero needs the configured compensation model")
        duration = _timing_expr(packet.duration, f"{packet.op} duration at {location}")
        source = packet.meta.get("rt_execution_event_id") or (events[0].id if events else packet.id)
        if simulated:
            # A barrier may wait between packet admission and committing its latches.
            edge = packet.meta.get("rt_execution_ns")
            finish = packet.rt_t1.value if isinstance(packet.rt_t1, Concrete) else None
            if type(edge) is not int or type(finish) is not int or not clock <= edge <= finish:
                raise _Unavailable(f"Unresolved or sub-nanosecond RT edges for {packet.id}")
            if edge > clock:
                commands.append(_Command(Expr.const(edge - clock), None, {}, source, False))
            duration = Expr.const(finish - edge)
            clock = finish
        else:
            if packet.meta.get("trigger_assumption"):
                end = _timing_expr(packet.rt_t1, f"Trigger end at {location}")
                start = _timing_expr(packet.rt_t0, f"Trigger start at {location}")
                duration = _delta(end, start)
            # A zero dwell requires dequeue spacing even after waiting for a trigger.
            if packet.meta.get("queue_duration_ns") == 0 or packet.duration == Concrete(0):
                duration += rt_dequeue_spacing_ns(0)
        packet_index[packet.id] = len(commands)
        commands.append(_Command(duration, play, updates, source,
                                 not skipped and "captured_phase_reset" in packet.meta))
    if simulated:
        stop = next((e for e in state.events if e.kind == "stop"), None)
        if stop is not None and isinstance(stop.t0, Concrete) and stop.t0.value > clock:
            commands.append(_Command(Expr.const(stop.t0.value - clock), None, {}, stop.id, False))
    # Replace each first iteration by its Repeat, innermost first. Indices stay in original packet space.
    loops = defaultdict(list)
    if state.execution_mode == "analysis":
        for event in state.events:
            if event.kind != "loop_block" or event.meta.get("loop_iteration_index", 0):
                continue
            if event.meta["count"] == "forever":
                raise _Unavailable("An unbounded loop has no finite output horizon")
            ids = event.meta["first_iteration_event_ids"]
            indices = {packet_index[p] for eid in ids if (p := events_by_id[eid].meta.get("rt_packet_id")) in packet_index}
            if indices:
                lo, hi = min(indices), max(indices) + 1
                loops[lo].append((hi, event))

    def nest(lo, hi, ignored):
        result = []
        while lo < hi:
            candidates = [(end, e) for end, e in loops.get(lo, ()) if e.id not in ignored and end <= hi]
            if candidates:
                end, e = max(candidates, key=lambda item: (item[0], item[1].meta["end_pc"]))
                result.append(_Loop(e, nest(lo, end, ignored | {e.id})))
                lo = end
            else:
                cmd = commands[lo]
                if (result and isinstance(result[-1], _Command) and cmd.play is None
                        and not cmd.captures and not cmd.updates):
                    previous = result[-1]
                    result[-1] = replace(previous, duration=previous.duration + cmd.duration)
                else:
                    result.append(cmd)
                lo += 1
        return tuple(result)
    return nest(0, len(commands), set()), events_by_id


class _Builder:
    def __init__(self, memory, events, mix):
        self.memory = memory
        self.events = events
        self.mix = mix
        self.lengths = {len(w) for w in memory.values()}
        self.last_loop_result = {}

    def interval(self, state, start, duration, source):
        # Each waveform end is a boundary even when I and Q have different lengths.
        segments = []
        last = ZERO
        waves = list(state.waveforms)
        lengths = self.lengths if any(isinstance(w, Expr) for w in waves) else {len(self.memory[w]) for w in waves if w is not None}
        for length in sorted(lengths):
            remaining = Expr.const(length) - state.index
            try:
                if remaining.bounds({})[1] <= 0:
                    remaining = ZERO
                elif remaining.bounds({})[0] < 0:
                    remaining = remaining.maximum(0)
            except KeyError:
                remaining = remaining.maximum(0)
            end = _minimum(duration, remaining)
            if end != last:
                pos = replace(state.advance(last), waveforms=tuple(waves))
                segments.append(pos.segment(start + last, start + end, source, self.mix))
            waves = [w if w is None or (max(lengths) if isinstance(w, Expr) else len(self.memory[w])) != length else None
                     for w in waves]
            last = end
        if last != duration:
            pos = replace(state.advance(last), waveforms=tuple(waves), index=ZERO)
            segments.append(pos.segment(start + last, start + duration, source, self.mix))
        return segments

    def block(self, commands, state, start, overrides=None):
        nodes = []
        cursor = start
        for cmd in commands:
            if isinstance(cmd, _Loop):
                # The first and steady laps of an outer loop can enter the very same child.
                previous = self.last_loop_result.get(id(cmd))
                if previous is None or previous[0] != state or previous[1] != cursor:
                    previous = (state, cursor, self.loop(cmd, state, cursor))
                    self.last_loop_result[id(cmd)] = previous
                children, state, elapsed = previous[2]
                nodes.extend(children)
            else:
                for name, (setter, raw) in cmd.updates.items():
                    value = (overrides or {}).get(setter, raw)
                    if name in ("awg_gain", "awg_offset"):
                        event = self.events.get(setter) or self.events[cmd.source]
                        field = f"{name} at {event.source.file}:{event.source.line}"
                        pair = tuple(v if isinstance(v, Expr) else _as_expr(v, field) for v in value)
                        pair = tuple(v.in_range(-32768, 32767) if v.free_symbols() else v for v in pair)
                        state = replace(state, **{"gain" if name == "awg_gain" else "offset": pair})
                    elif name == "phase_reset" and raw:
                        state = replace(state, accumulator=ZERO, phase_offset=ZERO)
                for field, attr in (("frequency", "frequency"), ("phase", "phase_offset"), ("phase_delta", "accumulator")):
                    if field in cmd.updates:
                        setter, value = cmd.updates[field]
                        event = self.events.get(setter) or self.events[cmd.source]
                        raw = _as_expr(value, f"{field} at {event.source.file}:{event.source.line}")
                        if raw.free_symbols():
                            raw = raw.in_range(-(1 << 31), (1 << 31) - 1) if field == "frequency" else raw.in_range(0, 1_000_000_000)
                        if field != "frequency":
                            raw = raw * 4
                        state = replace(state, **{attr: state.accumulator + raw if field == "phase_delta" else raw})
                if cmd.play is not None:
                    for w in cmd.play:
                        if not self.memory or not isinstance(w, Expr) and w not in self.memory:
                            raise _Unavailable(f"Missing waveform {w} for event {cmd.source}")
                    state = replace(state, waveforms=cmd.play, index=ZERO)
                elapsed = cmd.duration
                nodes.extend(self.interval(state, cursor, elapsed, cmd.source))
                state = state.advance(elapsed)
            cursor = cursor + elapsed
        return nodes, state, cursor

    def loop(self, loop, entry, start):
        meta = loop.event.meta
        if not meta.get("period_invariant") or meta.get("branch_layout_invariant") is False:
            raise _Unavailable(f"Output period or packet sequence varies in {meta['loop_id']}")
        changing_packets = (any(item["slope"] for item in meta.get("rt_packet_affine", {}).values())
                            or any(len(set(items)) > 1 for items in meta.get("rt_packet_durations", {}).values()))
        if changing_packets and any(isinstance(cmd, _Loop) or cmd.play or cmd.updates for cmd in loop.body):
            raise _Unavailable(f"Output packet boundaries vary in {meta['loop_id']}")
        source = meta["source_end"]
        location = f"{source['file']}:{source['line']}"
        count_input = meta.get("count_expr", meta["count"])
        try:
            count = _as_expr(count_input, f"Loop {meta['loop_id']} count at {location}")
        except _Unavailable as error:
            raise _Unavailable(f"{error}. Check the initial value and body writes for counter {meta['counter_reg']}.") from error
        if count == Expr.const(1 << 32):
            count = Expr.const(0).loop_count()
        period = _as_expr(meta["period"], f"Loop {meta['loop_id']} period at {location}")
        affine = meta.get("latched_affine", {})
        if set(meta.get("uncertain_setter_ids", [])) - affine.keys():
            raise _Unavailable(f"Unproved latch recurrence in {meta['loop_id']}")
        body_ids = set(meta["first_iteration_event_ids"])
        # A changing register used as a waveform index is not an invariant play.
        if meta.get("uncertain_waveform_ids"):
            raise _Unavailable(f"Waveform index varies in {meta['loop_id']}")

        name = "#" + meta["loop_id"]
        k = Expr.symbol(name)
        def overrides(index):
            result = {}
            for setter, pair in affine.items():
                for key, occurrence in ((setter, index), ("previous:" + setter, (index - 1).maximum(0))):
                    result[key] = tuple(Expr.const(item["intercept"]) + _mul(Expr.const(item["slope"]), occurrence)
                                        for item in pair)
            return result
        # After the last capture, pending writes take effect in the next iteration.
        steady = list(loop.body)
        pending = {}
        first_capture_skipped = None
        for eid in meta["first_iteration_event_ids"]:
            event = self.events[eid]
            if event.kind == "latched_state_pending" and event.meta["field"] in {
                "awg_gain", "awg_offset", "frequency", "phase", "phase_reset", "phase_delta",
            }:
                pending[event.meta["field"]] = (eid, event.meta["value"])
            elif event.kind == "q1_issue" and "captured_phase_reset" in event.meta:
                skipped = bool(event.meta.get("conditional_skipped"))
                if first_capture_skipped is None:
                    first_capture_skipped = skipped
                if skipped:
                    for field in ONE_SHOTS:
                        pending.pop(field, None)
                else:
                    pending.clear()
        # A skipped capture at the start of the next lap also consumes one-shots.
        if first_capture_skipped:
            for field in ONE_SHOTS:
                pending.pop(field, None)
        separate = False
        for i, cmd in enumerate(steady):
            if isinstance(cmd, _Loop):
                if pending:
                    raise _Unavailable("Pending latch crosses nested loop boundary")
                continue
            if not cmd.captures:
                continue
            repeated = {field: pair for field, pair in cmd.updates.items() if pair[0] in body_ids}
            carry_in = {field: ("previous:" + setter, value) for field, (setter, value) in pending.items()}
            updated = {**carry_in, **repeated}
            if updated != cmd.updates:
                other_writes = {f for c in steady if isinstance(c, _Command) for f in c.updates if c is not cmd}
                if any("phase_reset" in c.updates for c in steady if isinstance(c, _Command)):
                    other_writes.add("phase")
                separate = bool(pending or (cmd.updates.keys() - repeated.keys()) & (other_writes | {"phase_reset", "phase_delta"}))
                if separate:
                    steady[i] = replace(cmd, updates=updated)
            break
        first_nodes, first_end, _ = self.block(loop.body, entry, ZERO, overrides(ZERO))
        if count == Expr.const(1) or (not affine and not separate and first_end == entry):
            # The same commands starting from the same state produce the same body on every lap.
            return [Repeat(start, count, period, name, tuple(first_nodes), loop.event.id)], first_end, count * period
        # The body either overwrites a latched field or carries it. The oscillator
        # accumulates one fixed phase increment per steady lap, or resets each lap.
        second_nodes, second_end, _ = self.block(steady, first_end, ZERO, overrides(Expr.const(1)))
        if not affine and first_end == second_end:
            if first_nodes == second_nodes:
                nodes = [Repeat(start, count, period, name, tuple(first_nodes), loop.event.id)]
            else:
                nodes = [Repeat(start, Expr.const(1), period, name, tuple(first_nodes), loop.event.id),
                         Repeat(start + period, count - 1, period, name, tuple(second_nodes), loop.event.id)]
            return nodes, first_end, count * period
        has_play = any(isinstance(c, _Command) and c.play is not None for c in steady)
        nested = any(isinstance(c, _Loop) for c in steady)
        if nested and (entry != first_end or first_end != second_end):
            raise _Unavailable("Carried output state across nested compact loops is unresolved")
        third_end = self.block(steady, second_end, ZERO, overrides(Expr.const(2)))[1]
        resets = any(isinstance(c, _Command) and "phase_reset" in c.updates for c in steady)
        if entry.waveforms != first_end.waveforms and not (isinstance(steady[0], _Command) and steady[0].play):
            separate = True

        def initial(index):
            prev = self.block(steady, first_end, ZERO, overrides((index - 1).maximum(0)))[1]
            active = index.minimum(1)
            def pick(a, b):
                return a if a == b else a + _mul(active, _delta(b, a))
            lag = (index - 1).maximum(0)
            def carry(a, first, later):
                return pick(a, first) + _mul(lag.minimum(1), _delta(later, first))
            def pair(a, first, later):
                return tuple(carry(x, y, z) for x, y, z in zip(a, first, later))
            subsequent = (index - 2).maximum(0)
            drift = _delta(third_end.accumulator, second_end.accumulator)
            accumulator = (carry(entry.accumulator, first_end.accumulator, second_end.accumulator)
                           + _mul(subsequent.minimum(1) if resets else subsequent, drift))
            waves = first_end.waveforms if has_play else entry.waveforms
            advance = (prev.index if separate else pick(entry.index, prev.index)) if has_play else entry.index + _mul(index, period)
            return _State(waves, advance if waves != (None, None) else ZERO, pair(entry.gain, first_end.gain, prev.gain),
                          pair(entry.offset, first_end.offset, prev.offset), carry(entry.frequency, first_end.frequency, prev.frequency),
                          accumulator, carry(entry.phase_offset, first_end.phase_offset, prev.phase_offset))

        result = []
        if separate:
            first = Repeat(start, Expr.const(1), period, name, tuple(first_nodes), loop.event.id)
            result.append(first)
        index = k + 1 if separate else k
        steady_nodes, _, _ = self.block(steady, initial(index), ZERO, overrides(index))
        result.append(Repeat(start + period if separate else start, count - 1 if separate else count,
                             period, name, tuple(steady_nodes), loop.event.id))
        # With a separate first lap, a symbolic count may bind to one. Reconcile
        # one-shot effects by taking the actual state at the next iteration entry.
        final = initial(count)
        if has_play:
            final = replace(final, index=first_end.index, waveforms=first_end.waveforms)
        return result, final, count * period


def from_state(state, sequence) -> Output:
    """Construct a compact output model without rendering samples or replaying Q1ASM."""
    try:
        execution = state.metadata.get("execution", {})
        if execution.get("mode") in ("concrete", "simulation") and execution.get("status") != "completed":
            raise _Unavailable(f"Incomplete {execution['mode']} execution: {execution.get('reason') or execution.get('status')}")
        if any(event.meta.get("assumed_branch_path") in ("taken", "fallthrough", "not_taken", "both")
               or event.meta.get("branch_decision") == "assumed" for event in state.events):
            raise _Unavailable("Output depends on an assumed branch path")
        memory = {}
        if not isinstance(sequence, Mapping):
            raise _Unavailable("Sequence memory must be a mapping")
        entries = sequence.get("waveforms", {})
        if not isinstance(entries, (Mapping, list)):
            raise _Unavailable("Waveforms must be a mapping or a sequence of entries")
        for item in entries.values() if isinstance(entries, Mapping) else entries:
            if not isinstance(item, Mapping) or "data" not in item:
                continue  # Legacy names-only memory has no samples for this index.
            index, values = item.get("index"), item["data"]
            if isinstance(index, bool) or not isinstance(index, Integral) or not 0 <= index <= 1023 or index in memory:
                raise _Unavailable(f"Invalid or duplicate waveform index {index!r}")
            try:
                samples = tuple(values)
            except TypeError as error:
                raise _Unavailable(f"Waveform {index} requires an array of normalized samples") from error
            if (not samples or isinstance(values, (bytes, str, Mapping))
                    or any(isinstance(v, bool) or not isinstance(v, Real) or not -1 <= v <= 1 for v in samples)):
                raise _Unavailable(f"Waveform {index} requires normalized samples in [-1,1]")
            memory[int(index)] = tuple(map(float, samples))
        # Queue replay needs concrete times. The output builder can retain symbolic
        # times and use observed trigger arrivals, and checks each packet below.
        queue_warnings = {e.id for e in state.events if e.lane == "debug.underflow" and e.kind == "analysis_incomplete"
                          and (e.meta.get("analysis_scope") == "rt_queue" or
                               e.meta.get("reason") in {"unknown_rt_timing", "trigger_arrival_unknown"})}
        error = next((d for d in state.diagnostics if d.severity == "error" or d.category in {
            "unresolved_branch", "register_not_ready",
        } or (d.category == "analysis_incomplete" and queue_warnings.isdisjoint(d.related_events))), None)
        if error:
            raise _Unavailable(error.message)
        commands, events = _commands(state)
        if not any(e.kind == "stop" for e in state.events):
            raise _Unavailable("The execution did not reach stop; the final output state is unresolved")
        cfg = state.metadata.get("output_settings", sequence.get("output_settings", sequence.get("settings", {})))
        if not isinstance(cfg, Mapping):
            raise _Unavailable("Sequencer output settings must be a mapping")
        if execution.get("mode") == "simulation":
            signals = state.metadata.get("simulation", {}).get("signals", {})
            if signals.get("sample_period_ns", 1) != 1:
                raise _Unavailable("Compact output uses a 1 ns grid; see the configured simulation signal traces")
            cfg = {**signals.get("parameters", {}), **cfg}
        def pair(name, default):
            values = cfg.get(name, (default, default))
            if not isinstance(values, (list, tuple)) or len(values) != 2:
                raise _Unavailable(f"{name} requires two sequencer path values")
            return tuple(values)

        if type(cfg.get("nco_enabled", True)) is not bool:
            raise _Unavailable("nco_enabled must be a boolean")
        initial = _State(gain=tuple(_scaled(v, 32768) for v in pair("gain", 1)),
                         offset=tuple(_scaled(v, 32768) for v in pair("offset", 0)),
                         frequency=_scaled(cfg.get("nco_frequency_hz", 0), 4),
                         accumulator=_scaled(cfg.get("static_phase_steps", 0), 1) * 4,
                         phase_offset=_scaled(cfg.get("nco_phase_turns", 0), 4_000_000_000))
        builder = _Builder(memory, events, cfg.get("nco_enabled", True))
        nodes, end_state, end = builder.block(commands, initial, ZERO)
        source = commands[-1].event.id if commands and isinstance(commands[-1], _Loop) else commands[-1].source if commands else ""
        static_gain = pair("static_gain", 1)
        static_offset = pair("static_offset", 0)
        if any(type(v) not in (int, float) or not -1 <= v <= 1 for v in (*static_gain, *static_offset)):
            raise _Unavailable("Static gain and offset require two normalized values in [-1,1]")
        physical = {k: cfg[k] for k in ("fir", "iir", "dac_bits") if k in cfg}
        try:
            physical = validate_physical_config({"sequencers": {"seq": physical}})["sequencers"]["seq"]
        except ValueError as error:
            raise _Unavailable(str(error)) from error
        return Output(tuple(nodes), memory, end_state.segment(end, end, source, builder.mix),
                      static_gain=static_gain, static_offset=static_offset, fir=tuple(physical["fir"]),
                      iir_a=tuple(physical["iir"]["a"]), iir_b=tuple(physical["iir"]["b"]), dac_bits=physical["dac_bits"])
    except _Unavailable as error:
        return Output((), {}, None, (str(error),))
