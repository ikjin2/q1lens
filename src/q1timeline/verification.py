"""Verify source properties and locate the first reproducible RT queue failure."""
from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from math import prod
from pathlib import Path
from time import monotonic

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.api import _sequencer_id_from_file
from q1timeline.analysis.interpreter import diagnose_program_source, interpret_program
from q1timeline.analysis.smt import Query, register_query_error, verify_domain
from q1timeline.analysis.values import Concrete
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.ast import Program, numeric_defs, with_def_values
from q1timeline.q1asm.core_devices import ExternalInputs
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.q1asm.parser import parse_q1asm, parse_q1asm_file
from q1timeline.sequence import load_sequence_data
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.verification_annotations import AnnotationError, Assertion, MAX_DEF, OutputQuery, annotations, with_assumed_parameters


_RELATIONS = {"<": operator.lt, "<=": operator.le, "==": operator.eq,
              "!=": operator.ne, ">=": operator.ge, ">": operator.gt}


def _cases(domain):
    # itertools.product first copies each interval into a tuple. An implicit
    # word-sized domain must stay lazy even if the first value is a witness.
    axes = [(name, low, high - low + 1) for name, (low, high) in domain.items()]
    for index in range(prod(size for _, _, size in axes)):
        values = {}
        for name, low, size in reversed(axes):
            index, offset = divmod(index, size)
            values[name] = low + offset
        yield values


@dataclass
class _Scenario:
    seq: str
    programs: dict[str, Program]
    simulation: dict
    params: dict = field(default_factory=dict)
    memories: dict = field(default_factory=dict)
    hardware: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    receivers: dict[str, Callable[[dict], ExternalInputs]] = field(default_factory=dict)
    local: bool = True
    analytical_inputs: bool = False
    scheduled_inputs: bool = False
    modules: dict = field(default_factory=dict)
    alignment_mode: str = "none"
    simulation_alignment_mode: str = "none"
    alignment_anchor_kinds: tuple = ()
    free_inputs: set = field(default_factory=set)
    output_proof: object = None

    @property
    def program(self):
        return self.programs[self.seq]

    def case_params(self, inputs):
        return {**self.params, **{k: v for k, v in inputs.items() if k in self.free_inputs}}

    def receiver_inputs(self, params, *, seq=None):
        receiver = self.receivers.get(self.seq if seq is None else seq)
        return receiver(params) if receiver else None

    def simulate(self, inputs, max_steps, deadline):
        programs = {**self.programs, self.seq: with_def_values(self.program, inputs)}
        params = self.case_params(inputs)
        self.receiver_inputs(params)
        for program in programs.values():
            _validate_source(program, params)
        return simulate_programs(programs, max_steps=max_steps, simulation=self.simulation, deadline=deadline,
                                 params=params, sequences=self.memories, hardware_parameters=self.hardware,
                                 sequencer_metadata=self.metadata)


def _scenario(program, project, sequencer, startup):
    if project is None:
        seq = sequencer or _sequencer_id_from_file(program.file)
        return _Scenario(seq, {seq: program}, {"startup_delay_ns": startup} if startup is not None else {})
    config = load_project_config(project)
    for diagnostic in config.diagnostics:
        if diagnostic.severity == "error":
            raise ValueError(diagnostic.message)
    targets = [seq for seq in config.sequencers if seq.file.resolve() == Path(program.file)
               and (sequencer is None or seq.id == sequencer)]
    if len(targets) != 1:
        raise ValueError("Select a sequencer instance of this source file from the project.")
    target, = targets
    programs = {seq.id: program if seq.id == target.id else parse_q1asm_file(seq.file.resolve())
                for seq in config.sequencers}
    simulation = dict(config.simulation)
    if startup is not None:
        simulation["startup_delay_ns"] = startup
    # The symbolic model is for a local Q1 and 32-entry queue. Projects with
    # configured device effects use the actual project scenario for each case.
    local = (len(programs) == 1 and not target.hardware_parameters and not target.sequence_json
             and not any(config.external_inputs.values()) and not any(simulation.get("inputs", {}).values())
             and not any(simulation.get(key) for key in ("routes", "trigger_routes", "trigger_networks",
                                                          "trigger_truth_tables", "digital_routes", "physical", "digital"))
             and simulation.get("queue_capacity", 32) == 32)
    return _Scenario(target.id, programs, simulation, config.params,
                     {seq.id: config.output_sequence(seq, load_sequence_data(seq.sequence_json) if seq.sequence_json else None)
                      for seq in config.sequencers},
                     {seq.id: seq.hardware_parameters for seq in config.sequencers}, config.sequencer_metadata(),
                     {seq.id: partial(config.concrete_inputs, seq) for seq in config.sequencers}, local,
                     config.analysis.execution_mode != "simulation" and any(config.external_inputs.values()),
                     config.analysis.execution_mode == "simulation",
                     modules=config.modules, alignment_mode=config.alignment_mode,
                     simulation_alignment_mode=config.alignment_for_execution("simulation"),
                     alignment_anchor_kinds=config.alignment_anchor_kinds)


def _focus(states, state, event, inputs, include_replay, *, modules=None):
    witness = {"inputs": inputs, "file": event.source.file, "line": event.source.line,
               "sequencer": state.sequencer_id, "event_id": event.id, "time_ns": event.t0.value}
    if include_replay:
        align_timelines(states, mode="none")
        witness["timeline"] = timeline_ir_from_states(states, project={"alignment_mode": "none", "virtual": True},
                                                      modules=modules)
    return witness


def _execution_problem(state):
    execution = state.metadata["execution"]
    if execution["status"] != "completed":
        return execution.get("reason", execution["status"])
    return next((d.message for d in state.diagnostics if d.severity == "error" or d.category == "analysis_incomplete"), None)


def _validate_source(program, params):
    for diagnostic in diagnose_program_source(program, params=params, strict_q1asm=False):
        if diagnostic.severity == "error":
            raise ValueError(diagnostic.message)


def _simulation_case(scenario, inputs, max_steps, include_replay, deadline):
    try:
        states = scenario.simulate(inputs, max_steps, deadline)
    except (ValueError, TimeoutError) as exc:
        return "unknown", str(exc)
    state = next(state for state in states if state.sequencer_id == scenario.seq)
    for peer in states:
        if peer is not state and (reason := _execution_problem(peer)):
            return "unknown", f"{peer.sequencer_id}: {reason}"
    for event in state.events:
        if event.kind == "underflow_warning" and event.meta.get("simulated"):
            return "counterexample", _focus(states, state, event, inputs, include_replay)
    if reason := _execution_problem(state):
        return "unknown", reason
    return "proved", None


def _describe(domain):
    return ", ".join(f"{key}={low}" if low == high else f"{key}={low}..{high}"
                     for key, (low, high) in domain.items()) or "the declared inputs"


def _assignments(inputs):
    return ", ".join(f"{key}={value}" for key, value in inputs.items()) or "the declared inputs"


def _underflow(scenario, domain, axis, line, *, max_steps, max_paths, max_cases, deadline, include_replay):
    startup = scenario.simulation.get("startup_delay_ns")
    timing = "buffered RT startup" if startup is None else f"RT startup {startup} ns"
    check = {"kind": "underflow", "file": scenario.program.file, "line": line, "domain": domain,
             "startup_delay_ns": startup}
    if scenario.analytical_inputs and any(
            item.op in {"set_cond", "wait_trigger", "fb_pop_data", "fb_pull_data"}
            for program in scenario.programs.values() for item in program.instructions):
        return {**check, "status": "unknown",
                "message": "Physical RT queue timing requires simulation.inputs for this receiver schedule."}
    low, high = domain[axis] if axis is not None else (0, 0)
    first_unchecked = low
    remaining = max_cases
    unresolved_case = None

    def search(left, right):
        nonlocal remaining, first_unchecked, unresolved_case
        portion = {axis: (left, right), **{k: v for k, v in domain.items() if k != axis}} if axis else domain
        # A proof covers an entire interval. A candidate is accepted only if
        # the configured simulator reaches the actual failed dequeue.
        if scenario.local:
            query = Query("underflow", value=startup or 0, buffered_start=startup is None)
            result = verify_domain(scenario.program, domain=portion, query=query,
                                   max_steps=max_steps, max_paths=max_paths, deadline=deadline, params=scenario.params)
            if result.status == "unreachable":
                return "proved", None
            if result.status == "reachable":
                status, witness = _simulation_case(scenario, result.inputs, max_steps, include_replay, deadline)
                if status == "counterexample" and witness["time_ns"] == result.register_value:
                    return status, witness
                if status == "unknown":
                    return status, witness
                return "unknown", "The configured simulation did not reproduce the predicted underflow."
            if result.reason and ("limit" in result.reason or "timed out" in result.reason):
                return "unknown", result.reason
        for inputs in _cases(portion):
            if axis and unresolved_case is None:
                first_unchecked = inputs[axis]
            if monotonic() >= deadline or remaining <= 0:
                return "unknown", "Verification timed out." if monotonic() >= deadline else "Case limit reached."
            remaining -= 1
            status, detail = _simulation_case(scenario, inputs, max_steps, include_replay, deadline)
            if status == "counterexample":
                return status, detail
            if status == "unknown":
                unresolved_case = unresolved_case or detail
        if unresolved_case is not None:
            return "unknown", unresolved_case
        return "proved", None

    # Start near the lower endpoint so loops can give a useful boundary even
    # when their full 32-bit input space is too expensive to traverse.
    upper = min(high, low + 63) if scenario.local else high
    best = None
    reason = None
    while first_unchecked <= high:
        if monotonic() >= deadline:
            reason = "Verification timed out."
            break
        status, detail = search(first_unchecked, upper)
        if status == "unknown":
            reason = detail
            break
        if status == "proved":
            first_unchecked = upper + 1
            if best is not None and first_unchecked == best["inputs"].get(axis, 0):
                break
            upper = (first_unchecked + best["inputs"][axis] - 1) // 2 if best else high
            continue
        best = detail
        candidate = best["inputs"].get(axis, 0)
        if candidate == first_unchecked:
            break
        if unresolved_case is not None:
            reason = unresolved_case
            break
        upper = (first_unchecked + candidate - 1) // 2
    if best:
        candidate = best["inputs"].get(axis, 0)
        message = f"RT underflow at {best['time_ns']} ns for {_assignments(best['inputs'])}"
        if axis and first_unchecked == candidate:
            check["first_failure"] = {"name": axis, "value": candidate}
            if candidate > low:
                message = f"{axis}={low}..{candidate - 1} safe. First " + message
        if reason:
            message += f". First failing value unconfirmed ({reason})"
        check.update(status="counterexample", counterexample=best, message=f"{message} ({timing}).")
    elif reason:
        check.update(status="unknown", message=f"{reason} ({timing}).")
    else:
        check.update(status="proved", message=f"No RT underflow for {_describe(domain)} ({timing}).")
    if axis and first_unchecked > low:
        check["safe_prefix"] = {"name": axis, "min": low, "max": first_unchecked - 1}
        if reason and not best:
            check["message"] += f" Verified safe for {axis}={low}..{first_unchecked - 1}."
    return check


def _observations(state, assertion):
    query = assertion.query
    issues = [event for event in state.events if event.kind == "q1_issue"]
    if query.pc is None:
        if not issues or _execution_problem(state):
            return
        if query.kind == "register":
            word = state.registers.get(query.register)
            if any(w.register == query.register for w in state.pending_register_writes):
                word = None
            value = word.value & MAX_DEF if isinstance(word, Concrete) else None
        elif query.kind == "stop":
            value = state.metadata["execution"].get("stop_code")
        else:
            clock = state.q1_time_ns if query.kind == "q1_time" else state.rt_time_ns
            value = clock.value if isinstance(clock, Concrete) else None
        yield issues[-1], value
        return
    packets = {packet.id: packet for packet in state.rt_packets}
    writes = {write.event_id for write in (*state.register_history, *state.pending_register_writes)
              if write.register == query.register}
    pending = False
    rt = q1 = 0
    for issue in issues:
        if issue.meta.get("compiled_pc", issue.meta.get("source_pc")) == query.pc:
            if query.kind == "register":
                snapshot = issue.meta.get("register_snapshot", {}).get(query.register, {})
                value = snapshot["value"] & MAX_DEF if not pending and snapshot.get("kind") == "concrete" else None
            else:
                value = rt if query.kind == "rt_time" else q1
            yield issue, value
        q1 = issue.t1.value if isinstance(issue.t1, Concrete) else None
        pending = issue.id in writes and issue.meta["op"] != "loop"
        packet = packets.get(issue.meta.get("rt_packet_id"))
        if packet:
            rt = packet.meta.get("next_dequeue_not_before_ns",
                                 packet.rt_t1.value if isinstance(packet.rt_t1, Concrete) else None)


def _assertion(scenario, domain, assertion, *, max_steps, max_paths, max_cases, deadline, include_replay):
    check = {"kind": "assertion", "file": scenario.program.file, "line": assertion.line,
             "assertion": assertion.text, "domain": domain}
    if monotonic() >= deadline:
        return {**check, "status": "unknown", "message": "Verification timed out."}
    query = assertion.query
    if isinstance(query, OutputQuery):
        from q1timeline.output.properties import check_output

        # The symbolic output proof covers the whole interval at once.
        if scenario.receivers:
            try:
                for endpoint in (0, 1):
                    scenario.receiver_inputs(scenario.case_params({k: bounds[endpoint] for k, bounds in domain.items()}))
            except ValueError as exc:
                return {**check, "status": "unknown", "message": str(exc)}
        return {**check, **check_output(scenario, domain, query, max_steps=max_steps, max_paths=max_paths, deadline=deadline,
                                        include_replay=include_replay)}
    if reason := register_query_error(query):
        return {**check, "status": "unknown", "message": reason}
    cases = None
    prefix = False
    if scenario.local:
        try:
            result = verify_domain(scenario.program, domain=domain, query=query, max_steps=max_steps,
                                   max_paths=max_paths, deadline=deadline, params=scenario.params)
        except ValueError as exc:
            return {**check, "status": "unknown", "message": str(exc)}
        if result.status == "proved":
            return {**check, "status": "proved", "message": result.reason or f"{assertion.text} holds for {_describe(domain)}."}
        if result.status == "counterexample":
            cases = [result.inputs]
            max_steps = result.steps
            prefix = query.pc is not None
        elif result.reason and any(term in result.reason for term in ("limit", "timed out", "not ready", "uninitialized")):
            return {**check, "status": "unknown", "message": result.reason}
    # For modeled external inputs, Concrete can exhaust a finite annotation
    # domain using the same configured receiver state as a single DEF run.
    scheduled = scenario.scheduled_inputs and not scenario.local and any(
            item.op in {"wait_sync", "wait_trigger", "set_cond"} or item.op.startswith("fb_")
            for item in scenario.program.instructions)
    if scheduled and query.kind in {"rt_time", "q1_time"}:
        return {**check, "status": "unknown", "message": "This analytical clock depends on scheduled project inputs."}
    visited = False
    unresolved = None
    for n, inputs in enumerate(cases if cases is not None else _cases(domain)):
        if monotonic() >= deadline or n >= max_cases:
            reason = "Verification timed out." if monotonic() >= deadline else "Case limit reached."
            return {**check, "status": "unknown", "message": reason}
        try:
            if scheduled:
                states = scenario.simulate(inputs, max_steps, deadline)
                for participant in states:
                    if reason := _execution_problem(participant):
                        raise ValueError(reason)
                state = next(state for state in states if state.sequencer_id == scenario.seq)
            else:
                bound = with_def_values(scenario.program, inputs)
                params = scenario.case_params(inputs)
                _validate_source(bound, params)
                external = scenario.receiver_inputs(params)
                state = interpret_program(bound, sequencer_id=scenario.seq, execution_mode="concrete", params=params,
                                          external=external, max_steps=max_steps, deadline=deadline)
                states = [state]
        except TimeoutError as exc:
            return {**check, "status": "unknown", "message": str(exc)}
        except ValueError as exc:
            unresolved = unresolved or str(exc)
            continue
        for event, observed in _observations(state, assertion):
            visited = True
            if type(observed) is not int:
                unresolved = unresolved or f"{assertion.subject} has no readable value on some visits."
                continue
            if not _RELATIONS[query.op](observed, query.value):
                witness = _focus(states, state, event, inputs, include_replay)
                witness["value"] = observed
                if query.kind == "register":
                    witness["register"] = query.register
                return {**check, "status": "counterexample", "counterexample": witness,
                        "message": f"{assertion.subject}={observed} for {_assignments(inputs)}."}
        if reason := _execution_problem(state):
            if not (prefix and state.metadata["execution"]["status"] == "step_limit"):
                unresolved = unresolved or reason
        if cases is not None:
            return {**check, "status": "unknown", "message": unresolved or "Concrete replay did not reproduce the violation."}
    if unresolved:
        return {**check, "status": "unknown", "message": unresolved}
    if query.pc is not None and not visited:
        return {**check, "status": "proved", "message": "The assertion label is unreachable for every input in the domain."}
    return {**check, "status": "proved", "message": f"{assertion.text} holds for {_describe(domain)}."}


def verify_file(source: str | Path, *, project=None, sequencer=None, startup_delay_ns=None, max_steps=10000,
                max_paths=256, max_cases=4096, timeout_ms=10000, include_replay=False) -> dict:
    source = Path(source).resolve()
    deadline = monotonic() + timeout_ms / 1000
    checks = []
    line = 1
    try:
        if min(max_steps, max_paths, max_cases, timeout_ms) < 1:
            raise ValueError("Verification limits must be positive.")
        if startup_delay_ns is not None and startup_delay_ns < 0:
            raise ValueError("RT startup delay must be non-negative.")
        text = source.read_text(encoding="utf-8-sig")
        program = parse_q1asm(text, file=str(source))
        for diagnostic in program.diagnostics:
            if diagnostic.severity == "error":
                line = diagnostic.source.line if diagnostic.source else 1
                raise ValueError(diagnostic.message)
        domains, assertions = annotations(text, program)
        free_inputs = domains.keys() - program.defs.keys()
        program = with_assumed_parameters(program, domains)
        scenario = _scenario(program, project, sequencer, startup_delay_ns)
        scenario.free_inputs = free_inputs
        if not free_inputs:
            scenario.receiver_inputs(scenario.params)
        has_rt = any(get_instruction_spec(item.op).emits_rt_packet for item in program.instructions)
        options = dict(max_steps=max_steps, max_paths=max_paths, max_cases=max_cases, include_replay=include_replay)
        if not has_rt and not assertions:
            assertions = [Assertion(1, "Reaches stop", "q1_time", Query("q1_time", ">=", 0))]
        for assertion in assertions:
            line = assertion.line
            checks.append(_assertion(scenario, domains, assertion, deadline=deadline, **options))
        if has_rt:
            for axis in numeric_defs(program) or [None]:
                domain = dict(domains)
                if axis is not None:
                    value = program.defs[axis].value
                    domain.setdefault(axis, (min(1, value), MAX_DEF))
                    line = program.def_sources[axis].line
                else:
                    line = 1
                checks.append(_underflow(scenario, domain, axis, line, deadline=deadline, **options))
        for check in checks:
            check["sequencer"] = scenario.seq
            check["fixed_defs"] = {key: arg.value for key, arg in numeric_defs(program).items() if key not in check["domain"]}
            if check["fixed_defs"]:
                check["message"] += f" Fixed DEFs: {_assignments(check['fixed_defs'])}."
    except (ValueError, OSError, ConfigLoadError) as exc:
        checks.append({"kind": "verification", "status": "unknown", "file": str(source),
                       "line": exc.line if isinstance(exc, AnnotationError) else line, "message": str(exc), "domain": {}})
    except ModuleNotFoundError as exc:
        if exc.name != "z3":
            raise
        checks.append({"kind": "verification", "status": "unknown", "file": str(source), "line": line,
                       "message": "Install the current Q1Lens Python package to enable verification.", "domain": {}})
    statuses = {check["status"] for check in checks}
    return {"status": "counterexample" if "counterexample" in statuses else "unknown" if "unknown" in statuses else "proved",
            "checks": checks}
