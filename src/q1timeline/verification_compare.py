"""Exhaustive RT trace and shared-clock checkpoint comparisons."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from q1timeline.analysis.alignment import (
    SIMULATION_ALIGNMENT_MODE, _anchor_candidates, _anchor_confidence_unknown, _anchor_time, align_timelines,
)
from q1timeline.execution.state import TimelineEvent
from q1timeline.external_inputs import resolve_trigger_sources
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.ast import numeric_defs, with_def_values
from q1timeline.q1asm.core_devices import decode_device_command, is_linq_alias
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.sequence import load_sequence_data
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.verification import _cases
from q1timeline.verification_annotations import annotations, with_assumed_parameters


def _report(kind, path, status, message, **details):
    check = dict(kind=kind, file=str(path), line=1, status=status, message=message, **details)
    return {"status": status, "checks": [check]}


@dataclass
class _Difference:
    message: str
    event: TimelineEvent
    observations: dict
    time_ns: int | float


def _read(path):
    source = path.read_text(encoding="utf-8-sig")
    program = parse_q1asm(source, file=str(path))
    ranges, _ = annotations(source, program)
    return program, ranges


def _intersect_range(domain, name, bounds):
    low, high = domain.get(name, bounds)
    low, high = max(low, bounds[0]), min(high, bounds[1])
    if low > high:
        raise ValueError(f"No value of {name} satisfies the shared assumptions.")
    domain[name] = (low, high)


def _bind(program, bindings, inputs):
    return with_def_values(program, {name: inputs[key] for name, key in bindings.items()})


def _missing_measurement(states, equivalent):
    for state in states:
        counts = {}
        for event in state.events:
            if event.kind not in {"acquisition_result", "acquisition_unavailable"}:
                continue
            meta = event.meta
            key = ("program" if equivalent else state.sequencer_id, meta.get("acquisition_index"), meta.get("bin_index"))
            counts[key] = counts.get(key, 0) + 1
            if event.kind == "acquisition_unavailable" and meta.get("source", {}).get("kind") == "missing":
                return (*key, counts[key])
    return None


def _acquisition_entries(extra, name=None):
    return [{"sequencer": name or target, "acquisition_index": acq, "bin_index": bin_index, "thresholded": bit}
            for (target, acq, bin_index, _), bit in extra]


def _problem(states):
    for state in states:
        execution = state.metadata["execution"]
        if execution["status"] != "completed":
            return f"{state.sequencer_id}: {execution.get('reason') or execution['status']}"
        if error := next((d for d in state.diagnostics if d.severity == "error" or d.category in {
            "analysis_incomplete", "acquisition_trigger_threshold_unknown", "trigger_network_unknown_epoch",
            "digital_edge_trigger_outcome_unknown",
        }), None):
            return f"{state.sequencer_id}: {error.message}"
        recorded = {event.id for event in state.events}
        if any(p.rt_t0 is None or p.rt_t1 is None or p.meta.get("rt_execution_event_id") not in recorded
               for p in state.rt_packets):
            return f"{state.sequencer_id} has an RT packet without a complete execution event."


def _search(kind, path, domain, measurements, run, compare, *, include_replay, max_cases, timeout_ms, scope, time_basis="local"):
    deadline = monotonic() + timeout_ms / 1000
    static_domain = dict(domain)
    names = {}
    checked = 0
    reason = None
    equivalent = kind == "equivalence"

    def result(status, message, **extra):
        return _report(kind, path, status, message, domain={name: list(bounds) for name, bounds in domain.items()},
                       checked_cases=checked, measurements=measurements, scope=scope, **extra)

    for base in _cases(static_domain):
        pending = [(base, [])]
        while pending:
            if monotonic() >= deadline:
                return result("unknown", f"Verification timed out after {checked} cases.")
            if checked >= max_cases:
                return result("unknown", f"Reached the {max_cases} case limit.")
            inputs, extra = pending.pop()
            try:
                states = run(inputs, extra, deadline)
            except TimeoutError:
                return result("unknown", f"Verification timed out after {checked} cases.")
            except ValueError as error:
                # An invalid binding prevents a proof but another input can still refute it.
                checked += 1
                reason = reason or str(error)
                continue
            if monotonic() >= deadline:
                return result("unknown", f"Verification timed out after {checked} cases.")
            missing = _missing_measurement(states, equivalent)
            if missing is not None:
                if missing not in names:
                    target, acq, bin_index, occurrence = missing
                    name = _bit_name(domain, len(measurements))
                    names[missing] = name
                    measurements[name] = {"sequencer": target, "acquisition_index": acq, "bin_index": bin_index,
                                          "occurrence": occurrence}
                    domain[name] = (0, 1)
                for bit in (1, 0):
                    pending.append(({**inputs, names[missing]: bit}, [*extra, (missing, bit)]))
                continue
            checked += 1
            if failure := _problem(states):
                reason = reason or failure
                continue
            try:
                difference = compare(states)
            except ValueError as error:
                reason = reason or str(error)
                continue
            if difference:
                event = difference.event
                witness = dict(inputs=inputs, file=event.source.file, line=event.source.line,
                               time_ns=difference.time_ns, time_basis=time_basis, event_id=event.id, observations=difference.observations)
                if include_replay:
                    mode = SIMULATION_ALIGNMENT_MODE if time_basis == "aligned" else "none"
                    if time_basis == "aligned":
                        for state in states:
                            # Programs with no RT packets have an empty RT trace. Their Q1 stop
                            # carries the stop code and defines time zero for this replay lane.
                            align_timelines([state], mode=mode if state.rt_packets else f"manual:{-_stop_time(_stop(state))}")
                    else:
                        align_timelines(states, mode=mode)
                    witness["timeline"] = timeline_ir_from_states(states, project={"alignment_mode": mode, "virtual": True})
                values = ", ".join(f"{name}={value}" for name, value in sorted(inputs.items()))
                message = f"{values}: {difference.message}" if values else difference.message
                return result("counterexample", message, counterexample=witness)
    if reason:
        return result("unknown", reason)
    cases = "the single input combination" if checked == 1 else f"all {checked} input combinations"
    return result("proved", f"{scope} for {cases}.")


def _bit_name(domain, index):
    while f"m{index}" in domain:
        index += 1
    return f"m{index}"


def _stop(state):
    stop = next((event for event in reversed(state.events) if event.kind == "stop"), None)
    if stop is None:
        # With no RT packets the simulator records only the Q1 stop.
        stop = next((event for event in reversed(state.events)
                     if event.kind == "q1_issue" and event.meta.get("op") == "stop"), None)
    if stop is None:
        raise ValueError(f"{state.sequencer_id} has no observed stop.")
    return stop


def _stop_time(event):
    # With no RT queue the stop code becomes observable when the Q1 issue completes.
    return event.t1.value if event.kind == "q1_issue" else event.t0.value


def _observation(event, time, op):
    return {"file": event.source.file, "line": event.source.line, "event_id": event.id,
            "time_ns": time, "op": op}


def _trace(state, relative):
    events = {event.id: event for event in state.events}
    origin = state.rt_packets[0].rt_t0.value if relative and state.rt_packets else 0
    transitions = {}
    for transition in state.metadata["simulation"].get("digital_output", {}).get("transitions", []):
        transitions.setdefault(transition["rt_packet_id"], []).append({
            "time_ticks": transition["time_ticks"] - origin * 128, "level": transition["level"],
        })
    for packet in state.rt_packets:
        meta = packet.meta
        event = events[meta["rt_execution_event_id"]]
        op = packet.op
        args = tuple(meta["operand_snapshot"])
        if meta.get("conditional_skipped"):
            op, args = "conditional_skip", ()
        elif is_linq_alias(op, len(args)):
            op, args = decode_device_command(op, args)
        elif op == "acquire_weighed":
            op = "acquire_weighted"
        before, after = packet.rt_t0.value - origin, packet.rt_t1.value - origin
        observable = {"op": op, "time_ns": before, "end_ns": after, "operands": list(args),
                      "applied_state": meta.get("applied_state", {}),
                      "digital_transitions": transitions.get(packet.id, []),
                      "netzero_reset": bool(meta.get("netzero_reset")), "set_time_ref": "time_reference_ticks" in meta}
        yield observable, event
    event = _stop(state)
    time = _stop_time(event) - origin if state.rt_packets or not relative else 0
    yield {"op": "stop", "time_ns": time, "stop_code": event.meta.get("stop_code")}, event


def _trace_difference(states, relative):
    for (a, ea), (b, eb) in zip(_trace(states[0], relative), _trace(states[1], relative)):
        if a == b:
            continue
        if a["op"] == b["op"] and a["time_ns"] != b["time_ns"]:
            message = f"{b['op']} starts at {a['time_ns']} ns before and {b['time_ns']} ns after"
        elif a["op"] == b["op"] and a.get("end_ns") != b.get("end_ns"):
            message = f"{b['op']} ends at {a['end_ns']} ns before and {b['end_ns']} ns after"
        elif a["op"] != b["op"]:
            message = f"RT command changed from {a['op']} to {b['op']}"
        else:
            field = next(key for key in a if a[key] != b[key])
            message = f"{b['op']} {field} changed from {a[field]} to {b[field]}"
        observations = {key: {**_observation(e, row["time_ns"], row["op"]), **row}
                        for key, e, row in (("before", ea, a), ("after", eb, b))}
        return _Difference(message, eb, observations, b["time_ns"])
    # A bare file defines no external trigger schedule. A single no-arrival
    # execution can refute equivalence, but cannot certify all trigger outcomes.
    for state in states:
        if (any(p.op == "set_latch_en" and p.meta["operand_snapshot"][0] for p in state.rt_packets)
                and any(p.meta.get("rt_condition_branch_id") for p in state.rt_packets)):
            raise ValueError("External trigger arrivals affecting set_cond have no input domain in a standalone file.")
    return None  # Every complete trace ends in exactly one stop.


def verify_equivalence(before: Path, after: Path, *, include_replay=False, max_steps=10000, startup_delay_ns=None,
                       timeout_ms=10000, max_cases=4096):
    """Compare RT packet streams relative to the first dequeue on the same inputs."""
    before, after = Path(before).resolve(), Path(after).resolve()
    try:
        sources = {"before": _read(before), "after": _read(after)}
        parameters, defs = {}, {}
        for program, ranges in sources.values():
            for name, bounds in ranges.items():
                _intersect_range(defs if name in program.defs else parameters, name, bounds)
        def_keys = {name: f"${name}" if name in parameters else name for name in defs}
        domain = {**parameters, **{def_keys[name]: bounds for name, bounds in defs.items()}}
        programs, bindings = {}, {}
        for side, (program, _) in sources.items():
            prepared = with_assumed_parameters(program, parameters)
            programs[side] = prepared
            # Only genuine free references acquired synthetic DEFs during preparation.
            bindings[side] = {name: name for name in prepared.defs.keys() - program.defs.keys()}
            bindings[side].update({name: def_keys[name] for name in numeric_defs(program) if name in defs})

        relative = startup_delay_ns is None
        clock = "from the first dequeue" if relative else f"on the experiment clock with RT startup at {startup_delay_ns} ns"
        ranges = ", ".join(f"{name}={low}..{high}" for name, (low, high) in domain.items())
        inputs = f"over {ranges} (other DEFs use source values)" if domain else "at the source DEF values"
        scope = f"RT traces match {clock} {inputs}"
        absent = [f"{name} not varied in {side}" for name in domain
                  for side, symbols in bindings.items() if name not in symbols.values()]
        if absent:
            scope += " (" + ", ".join(absent) + ")"

        def run(inputs, extra, deadline):
            states = []
            for name, program in programs.items():
                scenario = {"inputs": {"acquisitions": _acquisition_entries(extra, name)}}
                if startup_delay_ns is not None:
                    scenario["startup_delay_ns"] = startup_delay_ns
                states.extend(simulate_programs({name: _bind(program, bindings[name], inputs)}, max_steps=max_steps,
                                                simulation=scenario, deadline=deadline))
            return states

        return _search("equivalence", after, domain, {}, run, lambda states: _trace_difference(states, relative),
                       include_replay=include_replay, max_cases=max_cases, timeout_ms=timeout_ms,
                       scope=scope, time_basis="aligned" if relative else "local")
    except (OSError, UnicodeError, ValueError) as error:
        return _report("equivalence", after, "unknown", str(error))


def _checkpoint_labels(programs, mode):
    available = []
    for program in programs.values():
        available.append({label for label, pc in program.labels.items() if pc < len(program.instructions)
                          and get_instruction_spec(program.instructions[pc].op).emits_rt_packet})
    common = set.intersection(*available)
    if mode.startswith("label:"):
        label = mode.split(":", 1)[1].strip()
        if label not in common:
            raise ValueError(f"@{label} must mark an RT instruction on every sequencer.")
        return [label]
    return sorted(common)


def _alignment_difference(states, labels, mode, anchor_kinds, required_label):
    rows = []
    for state in states:
        events = {event.id: event for event in state.events}
        rows_by_line = {}
        for packet in state.rt_packets:
            event = events[packet.meta["rt_execution_event_id"]]
            rows_by_line.setdefault(packet.source.line, []).append((event, packet.rt_t0.value))
        stops = _stop(state)
        points = {f"@{label}": rows_by_line.get(state.instructions_by_pc[state.labels[label]].source.line, [])
                  for label in labels}
        if mode:
            anchor = next(iter(_anchor_candidates(state, mode, anchor_kinds, simulation=True)), None)
            if _anchor_confidence_unknown(mode, anchor):
                raise ValueError(f"{state.sequencer_id} lacks a concrete {mode} checkpoint.")
            points[mode] = [(anchor, _anchor_time(mode, anchor).value)]
        points["stop"] = [(stops, _stop_time(stops))]
        rows.append(points)
    for name in rows[0]:
        visits = [row[name] for row in rows]
        if name == required_label and not any(visits):
            raise ValueError(f"No sequencer reached the designated checkpoint {name}.")
        for occurrence in range(max(map(len, visits))):
            present = [(state.sequencer_id, events[occurrence]) for state, events in zip(states, visits)
                       if occurrence < len(events)]
            observed = {seq: _observation(event, time, name) for seq, (event, time) in present}
            if len(present) < len(states):
                missing = [s.sequencer_id for s in states if s.sequencer_id not in observed]
                return _Difference(f"{', '.join(missing)} missed {name} occurrence {occurrence + 1}",
                                   present[0][1][0], observed, present[0][1][1])
            early, late = min(present, key=lambda x: x[1][1]), max(present, key=lambda x: x[1][1])
            delta = late[1][1] - early[1][1]
            if delta:
                return _Difference(f"{late[0]} reaches {name} {delta} ns later than {early[0]}",
                                   late[1][0], observed, late[1][1])
    return None


def verify_alignment(project: Path, *, include_replay=False, max_steps=10000,
                     timeout_ms=10000, max_cases=4096):
    """Check project sequencers together on the simulator's single clock."""
    project = Path(project).resolve()
    try:
        config = load_project_config(project)
        if len(config.sequencers) < 2:
            raise ValueError("Alignment requires at least two project sequencers.")
        if error := next((d for d in config.diagnostics if d.severity == "error"), None):
            raise ValueError(error.message)
        programs, domain, memories, bindings, parameters = {}, {}, {}, {}, {}
        for seq in config.sequencers:
            programs[seq.id], ranges = _read(seq.file)
            bindings[seq.id] = {}
            for name, (low, high) in ranges.items():
                if name in programs[seq.id].defs:
                    key = f"{seq.id}.{name}"
                    bindings[seq.id][name] = key
                    domain[key] = (low, high)
                else:
                    _intersect_range(parameters, name, (low, high))
            if seq.sequence_json:
                memories[seq.id] = load_sequence_data(seq.sequence_json)
        domain.update(parameters)
        labels = _checkpoint_labels(programs, config.alignment_mode)
        mode = config.alignment_mode if config.alignment_mode_explicit else None
        required_label = f"@{labels[0]}" if mode and mode.startswith("label:") else None
        if mode and mode.startswith("manual:"):
            raise ValueError("Manual display offsets do not designate a checkpoint for alignment verification.")
        if mode == "none" or (mode and mode.startswith("label:")):
            mode = None
        scenario = deepcopy(config.simulation)
        scenario_inputs = scenario["inputs"]
        measurements, acquisitions, triggers = {}, {}, {}
        for index, entry in enumerate(scenario_inputs["acquisitions"]):
            name = _bit_name(domain, len(measurements))
            domain[name] = (0, 1)
            measurements[name] = {k: entry[k] for k in ("sequencer", "acquisition_index", "bin_index")}
            measurements[name]["input_index"] = index
            acquisitions[name] = index
        for entry in config.external_inputs["triggers"]:
            for time_ns in entry["times_ns"]:
                name = _bit_name(domain, len(measurements))
                domain[name] = (0, 1)
                measurements[name] = {"source": entry["id"], "rt_time_ns": time_ns, "targets": entry["targets"]}
                # Keep one shared arrival edge for every recipient, even when queues start separately.
                arrival = scenario.get("startup_delay_ns", 0) + time_ns
                measurements[name]["time_ns"] = arrival
                triggers[name] = (entry["id"], arrival)
        for receiver in config.external_inputs.get("feedback", []):
            scenario_inputs["feedback"].extend({"target": receiver["target"], **entry} for entry in receiver["arrivals"])

        def run(inputs, extra, deadline):
            case = deepcopy(scenario)
            params = {**config.params, **{name: inputs[name] for name in parameters}}
            for name, index in acquisitions.items():
                case["inputs"]["acquisitions"][index]["thresholded"] = inputs[name]
            sources = {s["id"]: s for s in resolve_trigger_sources(config.external_inputs, params)}
            for name, (source_id, arrival) in triggers.items():
                if inputs[name]:
                    entry = sources[source_id]
                    case["inputs"]["triggers"].extend(
                        {"target": target, "time_ns": arrival, "address": entry["resolved_address"]}
                        for target in entry["targets"])
            case["inputs"]["acquisitions"].extend(_acquisition_entries(extra))
            return simulate_programs({name: _bind(program, bindings[name], inputs) for name, program in programs.items()},
                                     params=params, sequences=memories, max_steps=max_steps, simulation=case, deadline=deadline,
                                     hardware_parameters={seq.id: seq.hardware_parameters for seq in config.sequencers},
                                     sequencer_metadata=config.sequencer_metadata())

        checkpoints = ", ".join([*(f"@{label}" for label in labels), *([mode] if mode else []), "stop"])

        def compare(states):
            return _alignment_difference(states, labels, mode, config.alignment_anchor_kinds, required_label)

        return _search("alignment", project, domain, measurements, run, compare,
                       include_replay=include_replay, max_cases=max_cases, timeout_ms=timeout_ms,
                       scope=f"Sequencers align at {checkpoints} in the configured project scenario")
    except (ConfigLoadError, OSError, UnicodeError, ValueError) as error:
        return _report("alignment", project, "unknown", str(error))
