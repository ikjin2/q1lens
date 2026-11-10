"""Bounded, deterministic digital simulation using Q1Lens instruction semantics.

Queue and synchronization rules follow the Qblox sequencer/Q1ASM documentation.
Startup delay, link latency, trigger thresholds and analog inputs are explicit
scenario assumptions, not hardware calibration or a firmware compatibility claim.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import heapq
from typing import Any

from q1timeline.analysis import interpreter as core
from q1timeline.analysis.acquisition_windows import (
    QRM_MODULE_TYPES, QRM_SCOPE_SAMPLES, qrm_scope_key, resolve_qrm_scope_settings, scope_trigger_paths,
)
from q1timeline.analysis.queue_timing import (
    ASSUMED_INITIAL_BUFFER_NS, DISABLED_SYNC_REASON, POSSIBLE_INITIAL_BUFFER_NS, synchronization_participants,
)
from q1timeline.analysis.trigger_counters import TriggerCounters
from q1timeline.analysis.values import Concrete, Unknown, resolve_arg_value, resolve_def_values, symbol_table_from_params, value_to_json
from q1timeline.diagnostic_catalog import RT_QUEUE_UNDERFLOW_DESCRIPTION
from q1timeline.diagnostics import Diagnostic
from q1timeline.feedback_fifo import feedback_pop_limitation, take_feedback
from q1timeline.q1asm.ast import Instr, Program, SourceLocation
from q1timeline.q1asm.instruction_table import get_instruction_spec, rt_duration_arg_for_instruction
from q1timeline.q1asm.linq_commands import (
    LINQ_CONFIGURATION_STREAMS, MOCK_LIMITATION, PACKED_LINQ_CONFIGS, PACKED_LINQ_OPERATIONS,
    UnsupportedLinqCommand, decode_linq_command, linq_configuration_limitation,
)


_SUPPORTED = core._CONCRETE_OPS | {
    "set_cond", "wait_trigger", "set_latch_en", "latch_rst", "fb_com_data", "fb_cmd",
    "fb_pop_data", "fb_pull_data", "fb_acq_tb_id", "fb_acq_iq_id",
    "fb_acq_tb_valid", "fb_acq_iq_shift", "acquire_weighed", "acquire_weighted",
    "mulu16", "muls16",
    "play_netzero", "reset_netzero",
    "set_time_ref", "acquire_timetags", "acquire_digital", "acquire_ttl", "upd_thres",
    "fb_com_cfg", "fb_com_extra", "fb_acq_tb_cfg", "fb_acq_tb_extra",
    "fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id",
}
_Q1_ISSUABLE = _SUPPORTED | {"fb_acq_tb_mock"}
_QTM_SCOPE_CAPACITY = 2048
_TRIGGER_GRID_TICKS = 28 * 128
_TRIGGER_PROPAGATION_TICKS = 212 * 128
_TRIGGER_SPACING_TICKS = 252 * 128
_DIGITAL_EDGE_PHASE = 2.5
_POST_RT_TRIGGER_PHASE = 2.875
_POST_RT_POSSIBLE_TRIGGER_PHASE = 2.90625
_TRIGGER_WAIT_CHECK_PHASE = 2.921875
_TerminalEdge = tuple[Concrete, SourceLocation, str]


def _network_launch_tick(request_ticks: int, epoch_ticks: int) -> int:
    relative = max(0, request_ticks - epoch_ticks)
    return epoch_ticks + (relative + _TRIGGER_GRID_TICKS - 1) // _TRIGGER_GRID_TICKS * _TRIGGER_GRID_TICKS


@dataclass
class _Command:
    instruction: Instr
    args: list[int]
    packet_index: int
    duration: int
    settings: dict[str, Any]
    updated_fields: list[str]
    condition: tuple[int, int, int, int]
    setter_ids: dict[str, str] = field(default_factory=dict)
    phase_reset: bool = False
    netzero_reset: bool = False
    time_ref: bool = False
    phase_revision: int = 0
    normalized_op: str | None = None
    linq_error: tuple[str, str] | None = None

    @property
    def op(self) -> str:
        return self.normalized_op or self.instruction.op


@dataclass
class _PossibleTriggerArrival:
    address: int
    latest_ticks: int
    counted: bool = False
    threshold_case: tuple[str, int] | None = None
    guaranteed_in_case: bool = False
    truth_round: tuple[str, tuple[int | None, ...]] | None = None
    source_event_ids: tuple[str, ...] = ()
    source_rt_packet_ids: tuple[str, ...] = ()

    @property
    def from_acquisition(self) -> bool:
        return self.threshold_case is not None or self.truth_round is not None


@dataclass
class _TruthTriggerBounds:
    round_id: str
    outputs: tuple[int | None, ...]
    arrivals: dict[int, tuple[int, int]] = field(default_factory=dict)


@dataclass
class _TriggerEdgeWindow:
    event: core.TimelineEvent
    start_ticks: int
    end_ticks: int | None = None
    count_limit: int | None = None
    fixed_arrivals: list[tuple[str, _TriggerArrivalInterval]] = field(default_factory=list)


@dataclass
class _NetworkEdgeWindow:
    source: _TriggerEdgeWindow
    epoch_ticks: int | None

    def launch_bounds(self) -> tuple[int, int | None]:
        start, end = self.source.start_ticks, self.source.end_ticks
        epoch = self.epoch_ticks
        first = start if epoch is None else _network_launch_tick(start, epoch)
        if end is None:
            return first, None
        if end <= start:
            return first, first - 1
        # The source close is exclusive. An unresolved epoch leaves one grid phase of uncertainty.
        last = end + _TRIGGER_GRID_TICKS - 2 if epoch is None else _network_launch_tick(end - 1, epoch)
        return first, last


@dataclass(eq=False)
class _TriggerArrivalInterval:
    address: int
    start_ticks: int
    end_ticks: Callable[[], int | None]
    source_event_id: str
    after_rt: bool
    count_limit: int | None = None
    full_latch_epoch: int | None = None
    latch_end_ticks: int | None = None

    def contains(self, ticks: int) -> bool:
        end = self.end_ticks()
        return self.start_ticks <= ticks and (end is None or ticks < end)


@dataclass
class _EdgeTriggerCounts:
    upper: int | None = 0
    intervals: dict[_TriggerArrivalInterval, None] = field(default_factory=dict)


@dataclass
class _AdmittedQ1Issue:
    instruction: Instr | None = None
    planned_end_ns: int | float = 0
    interior_pc: int | None = None
    event_id: str | None = None
    latch_events: tuple[core.TimelineEvent, ...] = ()
    latch_state_before: core.LatchedState | None = None
    phase_reset_before: bool = False
    setter_ids_before: dict[str, str] | None = None
    future_latch_event_ids: tuple[str, ...] = ()


@dataclass
class _Sequencer:
    state: core.AnalysisState
    symbols: dict
    queue: deque = field(default_factory=deque)
    reserved: int = 0
    q1_done: bool = False
    admitted_q1_issue: _AdmittedQ1Issue = field(default_factory=_AdmittedQ1Issue)
    stop_instruction: Instr | None = None
    stop_issue_id: str | None = None
    rt_started: bool = False
    rt_done: bool = False
    rt_guard: bool = True
    rt_empty_wait: bool = False
    rt_prefill_since: int | None = None
    rt_prefill_ns: int = 0
    failed: bool = False
    waiting: str | None = None
    waiting_since: int = 0
    feedback: list[dict] = field(default_factory=list)
    trigger_tokens: dict[int, deque] = field(default_factory=dict)
    trigger_counters: TriggerCounters = field(default_factory=TriggerCounters)
    uncertain_trigger_counts: dict[int, int] = field(default_factory=dict)
    uncertain_trigger_evidence: dict[int, _PossibleTriggerArrival] = field(default_factory=dict)
    truth_trigger_rounds: dict[int, _TruthTriggerBounds] = field(default_factory=dict)
    edge_trigger_intervals: list[_TriggerArrivalInterval] = field(default_factory=list)
    edge_trigger_counts: dict[int, _EdgeTriggerCounts] = field(default_factory=dict)
    trigger_latch_epoch: int = 0
    uncertain_trigger_windows: deque[_PossibleTriggerArrival] = field(default_factory=deque)
    uncertain_trigger_tokens: dict[int, _PossibleTriggerArrival] = field(default_factory=dict)
    threshold_trigger_cases: dict[tuple[str, int], list[list[int]]] = field(default_factory=dict)
    trigger_network_transmissions: list[dict] = field(default_factory=list)
    qtm_trigger_transmissions: list[dict] = field(default_factory=list)
    qtm_trigger_rounds: list[dict] = field(default_factory=list)
    ttl_trigger_transmissions: list[dict] = field(default_factory=list)
    condition: tuple[int, int, int, int] = (0, 0, 0, 4)
    condition_branch_id: str | None = None
    condition_issue_id: str | None = None
    condition_controls: dict[str, core.TimelineEvent] = field(default_factory=dict)
    rt_settings: dict[str, Any] = field(default_factory=dict)
    rt_setter_ids: dict[str, str] = field(default_factory=dict)
    rt_wait: tuple | None = None
    phase_revision: int = 0
    rt_phase_revision: int = 0
    netzero_reset: bool = False
    time_ref_pending: bool = False
    time_ref_ticks: int | None = None
    digital_level: int | None = None
    digital_input_event: str | None = None
    digital_output_level: int = 0
    digital_output_transitions: list[dict] = field(default_factory=list)
    timetag_window: dict | None = None
    timetag_boundary_ticks: int | None = None
    pending_timetag_closes: int = 0
    last_timetag_window: tuple[int, dict | None, bool] | None = None
    qtm_scope_capture: dict | None = None
    qtm_scope_captures: list[dict] = field(default_factory=list)
    qtm_scope_entries: list[dict] = field(default_factory=list)
    qtm_scope_dropped: int = 0
    qrm_scope_captures: list[dict] = field(default_factory=list)
    ttl_gate: dict | None = None
    count_thresholds: dict[int, int] = field(default_factory=dict)
    digital_bins: dict[tuple, dict] = field(default_factory=dict)
    acquisition: dict | None = None
    acquisitions: list[dict] = field(default_factory=list)
    signal_pending: int = 0
    netzero_results: list[dict] = field(default_factory=list)
    netzero_pending: set[tuple[str, int]] = field(default_factory=set)
    tb_id: int = 0
    iq_id: int = 0
    tb_valid: int = 1
    tb_cfg: tuple[int, int, int] = (0, 0, 0)
    com_cfg: tuple[int, int, int] = (0, 0, 0)
    tb_extra: tuple[int, int] = (0, 0)
    com_extra: tuple[int, int] = (0, 0)
    qtm_streams: dict[str, int] = field(default_factory=dict)
    iq_shift: int = 0
    setter_ids: dict[str, str] = field(default_factory=dict)
    branch_occurrences: dict[int, int] = field(default_factory=dict)

    @property
    def blocking_rt_wait(self) -> tuple | None:
        if self.rt_wait is not None and (self.rt_wait[0] == "sync_wait" or self.waiting in {None, "queue_full"}):
            return self.rt_wait
        return None


@dataclass
class _NetworkRequest:
    seq: _Sequencer
    event: core.TimelineEvent
    provenance: dict
    record: dict
    threshold_case: tuple[str, int] | None = None
    truth_round_id: str | None = None
    launch: core.TimelineEvent | None = None

    @property
    def optional(self) -> bool:
        return self.threshold_case is not None or self.truth_round_id is not None


def simulate_programs(
    programs: dict[str, Program], *, params: dict | None = None, max_steps: int = 10000,
    simulation: dict | None = None, sequences: dict[str, dict] | None = None,
    strict_q1asm: bool = False,
    branch_choices: Mapping[tuple[str, int, int], bool] | None = None,
    prepared_programs: Mapping[str, core.PreparedDefProgram] | None = None,
    branch_assumptions: Mapping[str, str] | None = None,
    hardware_parameters: Mapping[str, Mapping[str, int | str | bool]] | None = None,
    sequencer_metadata: Mapping[str, Mapping[str, int | str | None]] | None = None,
) -> list[core.AnalysisState]:
    """Run a scenario, optionally selecting a conditional branch occurrence.

    Choice keys are (sequencer_id, source_pc, 1-based occurrence). A choice
    changes only the selected path; natural operands, flags and loop writes
    are retained. Choices that execution never reaches raise ValueError.
    """
    from q1timeline.simulation.config import validate_simulation
    from q1timeline.simulation.signals import SignalModel
    from q1timeline.project import validate_params_json_mapping

    if not isinstance(programs, Mapping) or any(not isinstance(name, str) or not name.strip() or not isinstance(program, Program) for name, program in programs.items()):
        raise ValueError("programs must map nonempty sequencer names to parsed Program objects")
    if params is not None and (not isinstance(params, Mapping) or any(not isinstance(name, str) for name in params)):
        raise ValueError("params must be a mapping with string keys")
    if sequences is not None and (not isinstance(sequences, Mapping) or any(not isinstance(memory, Mapping) for memory in sequences.values())):
        raise ValueError("sequences must map sequencer names to sequence-memory mappings")
    if sequences is not None and set(sequences) - set(programs):
        raise ValueError("sequences contains an unknown sequencer")
    if hardware_parameters is not None and (not isinstance(hardware_parameters, Mapping)
            or any(name not in programs or not isinstance(settings, Mapping)
                   for name, settings in hardware_parameters.items())):
        raise ValueError("hardware_parameters must map known sequencers to parameter mappings")
    for name, settings in (hardware_parameters or {}).items():
        if "sync_en" in settings and type(settings["sync_en"]) is not bool:
            raise ValueError(f"hardware_parameters.{name}.sync_en must be boolean")
        if "integration_length_acq" in settings:
            length = settings["integration_length_acq"]
            if type(length) is not int or length <= 0:
                raise ValueError(f"hardware_parameters.{name}.integration_length_acq must be a positive integer")
    if sequencer_metadata is not None and (not isinstance(sequencer_metadata, Mapping)
            or any(name not in programs or not isinstance(settings, Mapping)
                   for name, settings in sequencer_metadata.items())):
        raise ValueError("sequencer_metadata must map known sequencers to identity mappings")
    for name, identity in (sequencer_metadata or {}).items():
        for key, expected in (("module", str), ("module_type", str), ("sequencer_index", int)):
            if identity.get(key) is not None and type(identity[key]) is not expected:
                raise ValueError(f"sequencer_metadata.{name}.{key} must be {expected.__name__} or null")
    validate_params_json_mapping(dict(params or {}))
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    if branch_choices is not None and not isinstance(branch_choices, Mapping):
        raise ValueError("branch_choices must map (sequencer, pc, occurrence) tuples to booleans")
    choices = dict(branch_choices or {})
    for key, taken in choices.items():
        if (not isinstance(key, tuple) or len(key) != 3 or not isinstance(key[0], str)
                or type(key[1]) is not int or type(key[2]) is not int or key[2] < 1 or type(taken) is not bool):
            raise ValueError("branch_choices keys require a sequencer, integer PC and positive occurrence; values must be booleans")
        name, pc, _occurrence = key
        instruction = next((item for item in programs[name].instructions if item.pc == pc), None) if name in programs else None
        if instruction is None or instruction.op == "jmp" or get_instruction_spec(instruction.op).category != "branch":
            raise ValueError(f"Branch choice {key!r} does not identify a conditional branch")
    config = validate_simulation(simulation, sequencer_ids=set(programs))
    memories = {name: (sequences or {}).get(name, {}) for name in programs}
    assumptions = core._normalise_branch_assumptions(branch_assumptions)

    def run(paths: Mapping[str, str], *, comparison: bool = False) -> tuple[list[core.AnalysisState], dict[str, _TerminalEdge]]:
        signals = SignalModel(config["physical"], memories) if config["physical"] else None
        engine = _Runtime(programs, params or {}, max_steps, config, signals, strict_q1asm, choices,
                          prepared_programs or {}, paths, memories, hardware_parameters or {}, sequencer_metadata or {})
        states = engine.run()
        unused = set(choices) - engine.applied_branch_choices
        # A comparison can end before a Q1 choice reached by the recorded run.
        if unused and not comparison:
            raise ValueError(f"Branch choices were not reached: {sorted(unused)!r}")
        return states, engine.terminal_edges

    states, _ = run(assumptions)
    controls = {event.meta["branch_id"]: event for state in states for event in state.events
                if event.meta.get("rt_condition_control")}
    comparison_ids = [branch_id for branch_id in controls if assumptions.get(branch_id) == "both"]
    if comparison_ids:
        recorded_events = [list(state.events) for state in states]
        for branch_id in comparison_ids:
            execute, execute_edges = run({**assumptions, branch_id: "taken"}, comparison=True)
            skip, skip_edges = run({**assumptions, branch_id: "fallthrough"}, comparison=True)
            _append_condition_comparison(states, recorded_events, execute, skip, branch_id,
                                         terminal_edges={"taken": execute_edges, "fallthrough": skip_edges})
    for branch_id, control in controls.items():
        path = assumptions.get(branch_id, "collapsed")
        if path == "both":
            control.meta["comparison_paths"] = ["taken", "fallthrough"]
        control.meta["assumed_branch_path"] = path
    return states


def _same_comparison_prefix_event(recorded: core.TimelineEvent, alternate: core.TimelineEvent) -> bool:
    if recorded == alternate:
        return True
    window = recorded.meta.get("digital_gate_window")
    alternate_window = alternate.meta.get("digital_gate_window")
    if window is None or alternate_window is None:
        return False
    # A later close or cutoff should not duplicate an already shared opener.
    comparison_window = {**window, "end_ticks": alternate_window["end_ticks"], "status": alternate_window["status"]}
    return replace(recorded, meta={**recorded.meta, "digital_gate_window": comparison_window}) == alternate


def _append_condition_comparison(
    states: list[core.AnalysisState], recorded_events: list[list[core.TimelineEvent]],
    execute: list[core.AnalysisState], skip: list[core.AnalysisState], branch_id: str,
    *, terminal_edges: Mapping[str, Mapping[str, _TerminalEdge]] | None = None,
) -> None:
    # Keep the input-driven run for Hardware and compare the suffix from the first differing event.
    tails: dict[str, list[core.TimelineEvent]] = {"taken": [], "fallthrough": []}
    shared_event_ids: set[str] = set()
    terminal_evidence: dict[str, dict[str, list[str]]] = {"taken": {}, "fallthrough": {}}
    for recorded, executed, skipped in zip(recorded_events, execute, skip):
        common = zip(recorded, executed.events, skipped.events)
        prefix = min(len(recorded), len(executed.events), len(skipped.events))
        for index, (actual, true_event, false_event) in enumerate(common):
            if not _same_comparison_prefix_event(actual, true_event) or not _same_comparison_prefix_event(actual, false_event):
                prefix = index
                break
        shared_event_ids.update(event.id for event in recorded[:prefix])
        for path, state in (("taken", executed), ("fallthrough", skipped)):
            tail = state.events[prefix:]
            terminal = (terminal_edges or {}).get(path, {}).get(state.sequencer_id)
            if terminal is not None:
                edge, source, category = terminal
                execution = state.metadata["execution"]
                diagnostic = next((item for item in reversed(state.diagnostics) if item.category == category), None)
                if diagnostic is not None and diagnostic.related_events:
                    terminal_evidence[path][state.sequencer_id] = diagnostic.related_events
                terminal_kind = {"illegal_instruction": "illegal", "rt_queue_underflow": "underflow_warning",
                                 "feedback_queue_overflow": "feedback_rejected"}.get(category)
                reason = execution["reason"]
                if terminal_kind in {"underflow_warning", "feedback_rejected"}:
                    # The marker already displays its edge in the viewer's time basis.
                    reason = reason.removesuffix(f" at {edge.value} ns.")
                outcome = {"status": execution["status"], "reason": reason}
                if category == "simulation_time_limit" and diagnostic is not None and "display_message" in diagnostic.details:
                    outcome.update(reason=diagnostic.details["display_message"], raw_reason=reason,
                                   time_limit_local_ns=diagnostic.details["time_limit_local_ns"])
                for index, event in enumerate(tail):
                    if event.kind == terminal_kind and event.t0 == edge:
                        tail[index] = replace(event, meta={**event.meta, "comparison_terminal": outcome})
                        break
                else:
                    tail.append(core.TimelineEvent(
                        id=f"{state.sequencer_id}:terminal", sequencer_id=state.sequencer_id, lane="rt.simulation",
                        kind="branch_terminal", t0=edge, t1=edge, duration=Concrete(0),
                        label=f"Execution {outcome['status'].replace('_', ' ')}", confidence="exact", source=source,
                        meta={"comparison_terminal": outcome},
                    ))
            tails[path].extend(tail)
    by_sequencer = {state.sequencer_id: state for state in states}
    for path, rerun in (("taken", execute), ("fallthrough", skip)):
        tail = tails[path]
        suffix = f":condition-{path}:{branch_id}"
        event_ids = {event.id: event.id + suffix for event in tail}
        # The RT execution event owns packet identity even when the Q1 issue
        # is shared by both outcomes. A late result may refer to a prefix packet.
        packet_ids = {packet.id: packet.id + suffix for state in rerun for packet in state.rt_packets
                      if packet.meta.get("rt_execution_event_id", packet.meta.get("q1_issue_event_id")) in event_ids}
        references = {**dict.fromkeys(_CONDITION_EVENT_REFERENCES, event_ids),
                      **dict.fromkeys(_CONDITION_PACKET_REFERENCES, packet_ids)}
        for event in tail:
            meta = _remap_condition_references(event.meta, references)
            if "comparison_terminal" in meta:
                evidence = dict.fromkeys(event_ids.get(source_id, source_id)
                                         for source_id in terminal_evidence[path].get(event.sequencer_id, ())
                                         if source_id != event.id and (source_id in event_ids or source_id in shared_event_ids))
                if evidence:
                    meta["comparison_terminal"]["source_event_ids"] = list(evidence)
            meta.update(branch_comparison_kind="rt_condition", branch_comparison_branch_id=branch_id,
                        branch_comparison_path=path)
            by_sequencer[event.sequencer_id].events.append(replace(
                event, id=event_ids[event.id], lane=f"branch_compare.{path}.{event.lane}", meta=meta,
                confidence="assumed" if event.confidence == "exact" else event.confidence,
            ))


_CONDITION_EVENT_REFERENCES = frozenset({
    "event_id", "event_ids", "source_event_id", "source_event_ids", "input_event_id", "input_event_ids",
    "request_event_id", "launch_event_id", "conflicting_request_event_id", "arrival_event_id",
    "trigger_event_id", "q1_issue_event_id", "rt_condition_issue_event_id", "rt_execution_event_id",
    "setter_event_id", "applied_setter_event_ids", "captured_setter_event_ids", "committed_setter_event_ids",
    "reference_event_id", "time_source_event_id", "interrupted_by_event_id", "overwritten_by_event_id",
    "acquire_event_id", "acquisition_event_id", "open_event_id", "close_event_id", "disable_event_id",
})
_CONDITION_PACKET_REFERENCES = frozenset({
    "rt_packet_id", "source_packet_id", "source_rt_packet_id", "source_rt_packet_ids",
    "open_packet_id", "close_packet_id", "disable_packet_id",
})


def _remap_condition_references(value: Any, references: Mapping[str, Mapping[str, str]],
                                ids: Mapping[str, str] | None = None) -> Any:
    if isinstance(value, dict):
        return {key: _remap_condition_references(item, references, ids if ids is not None else references.get(key))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_condition_references(item, references, ids) for item in value]
    return ids.get(value, value) if ids is not None and isinstance(value, str) else value


class _Runtime:
    def __init__(self, programs: dict[str, Program], params: dict, max_steps: int, config: dict, signals: Any, strict_q1asm: bool,
                 branch_choices: dict[tuple[str, int, int], bool], prepared_programs: Mapping[str, core.PreparedDefProgram],
                 branch_assumptions: Mapping[str, str], memories: Mapping[str, dict],
                 hardware_parameters: Mapping[str, Mapping[str, int | str | bool]],
                 sequencer_metadata: Mapping[str, Mapping[str, int | str | None]]):
        self.config, self.signals, self.max_steps = config, signals, max_steps
        self.memories = memories
        self.branch_choices = branch_choices
        self.branch_assumptions = branch_assumptions
        self.prepared_trace = bool(prepared_programs)
        self.network_diagnostic_packets: list[tuple[core.AnalysisState, int, tuple[str, ...]]] = []
        self.applied_branch_choices: set[tuple[str, int, int]] = set()
        self.capacity = config["queue_capacity"]
        self.feedback_capacity = config["feedback_queue_capacity"]
        self.startup = config.get("startup_delay_ns")
        self.max_time = config["max_time_ns"]
        self.now_ticks = 0
        self.now_phase = 0.0
        self.terminal_edges: dict[str, _TerminalEdge] = {}
        self.serial = 0
        self.events: list[tuple] = []
        self.sequencers: dict[str, _Sequencer] = {}
        self.sync_participants = synchronization_participants(programs, hardware_parameters)
        self.sync_waiters: dict[str, tuple] = {}
        self.synq_epoch_ticks: int | None = None
        self.trigger_launches: dict[tuple[str, int], list[_NetworkRequest]] = {}
        self.recent_trigger_launches = {network: deque() for network in config["trigger_networks"]}
        self.unresolved_launches = {network: deque() for network in config["trigger_networks"]}
        self.network_edge_windows: dict[str, dict[str, _NetworkEdgeWindow]] = {network: {} for network in config["trigger_networks"]}
        self.ttl_trigger_routes: dict[str, dict[int, list[dict]]] = {}
        for route in config["trigger_routes"]:
            if route.get("source_kind") == "ttl_edge":
                self.ttl_trigger_routes.setdefault(route["source"], {}).setdefault(route["address"], []).append(route)
        self.rt_start_triggers: dict[str, list[dict]] = {}
        self.digital_routes: dict[str, list[dict]] = {}
        self.digital_sources: dict[str, str] = {}
        for route in config["digital_routes"]:
            self.digital_routes.setdefault(route["source"], []).append(route)
            self.digital_sources[route["target"]] = route["source"]
        self.timetag_channels = {(settings["module"], settings["channel"]): name
                                 for name, settings in config["digital"].items() if "channel" in settings}
        self.qtm_truth_channels = {module: set().union(*(rule["when"] for rule in rules))
                                   for module, rules in config["trigger_truth_tables"].items()}
        self.qtm_truth_pending: dict[tuple[str, int], dict] = {}
        self.qrm_scope_latest: dict[tuple[str, str], tuple[_Sequencer, dict]] = {}
        self.qrm_scope_buffers: dict[tuple[str, str], dict] = {}
        self.used_acquisition_inputs: set[int] = set()
        self.used_digital_inputs: set[int] = set()
        self.tb_groups: dict[tuple, list[dict]] = {}
        for name, program in programs.items():
            prepared = prepared_programs.get(name)
            state = core.AnalysisState(
                sequencer_id=name, labels=program.labels,
                instructions_by_pc=prepared.instructions_by_pc if prepared else {instruction.pc: instruction for instruction in program.instructions},
                register_aliases=prepared.register_aliases if prepared else core._register_aliases(program.defs), execution_mode="concrete",
                strict_q1asm=strict_q1asm,
                record_trace=prepared is None,
                bound_instructions=prepared.bindings if prepared else {},
            )
            if name in hardware_parameters:
                state.metadata["hardware_parameters"] = dict(hardware_parameters[name])
            identity = sequencer_metadata.get(name, {})
            state.metadata.update({key: identity[key] for key in ("module", "module_type", "sequencer_index") if key in identity})
            state.diagnostics.extend(program.diagnostics)
            if strict_q1asm:
                core._diagnose_strict_q1asm_placeholder_defs(state, program.defs)
            symbols = dict(prepared.param_symbols) if prepared else symbol_table_from_params(params)
            defs = {key: arg for key, arg in program.defs.items() if arg.kind != "reg"}
            state.compiled_addresses = (prepared.compiled_addresses if prepared
                                        else core.compile_addresses(state.instructions_by_pc, state.labels))
            resolved = resolve_def_values(defs, symbols, state.diagnostics, labels=core._address_labels(state))
            state.symbol_origins = core._symbol_origins(params, symbols, defs, resolved)
            symbols.update(resolved)
            core._diagnose_program(state, symbols, program, prepared=prepared)
            state.metadata["execution"] = {
                "mode": "simulation", "status": "completed", "steps": 0, "max_steps": max_steps,
                "timing_model": "event_driven", "address_model": core._address_model(state),
                "sync_participants": sorted(self.sync_participants),
                "assumptions": [
                    "Q1 prefills the RT queue before it starts, with an assumed 1 ms window and a tentative 2 ms upper limit."
                    if self.startup is None else f"RT startup delay {self.startup} ns is an explicit scenario parameter.",
                    "Same-time events run in input, enqueue/completion, RT, digital window gates, digital output and routed input, window results, edges and samples, then Q1 order; remaining ties use scheduling order.",
                    "Sequencers participate in wait_sync unless sync_en is false. Synchronization transport latency is zero.",
                    "Trigger counters start disabled and use per-address hardware settings, defaulting to threshold one without inversion.",
                    "wait_trigger accepts scenario arrivals at or after its RT start; earlier arrivals only affect enabled counters.",
                    "Addresses include the documented jlt, three-operand jge, and loop expansions.",
                ],
            }
            seq = self.sequencers[name] = _Sequencer(
                state, symbols, trigger_counters=TriggerCounters(state.metadata.get("hardware_parameters", {})),
            )
            thresholds = config["digital"].get(name, {}).get("count_thresholds", {})
            seq.count_thresholds = {index: thresholds[key] for index, key in enumerate(("low", "high")) if key in thresholds}
            errors = [item for item in state.diagnostics
                      if item.severity == "error" and not item.details.get("source_line_skipped")]
            if errors:
                status = "error" if any(item.category != "unresolved_symbol" for item in errors) else "blocked"
                self.fail(seq, status, errors[0].message)
            else:
                self.schedule(0, 3, name, "q1")
                if self.startup is not None:
                    specs = (binding.spec if (binding := state.bound_instructions.get(pc)) is not None
                             else get_instruction_spec(instruction.op)
                             for pc, instruction in state.instructions_by_pc.items())
                    if any(spec.emits_rt_packet or spec.category == "unknown" for spec in specs):
                        self.schedule(self.startup, 2, name, "rt")
        scope_keys = {qrm_scope_key(seq.state) for seq in self.sequencers.values()
                      if seq.state.metadata.get("module_type") in QRM_MODULE_TYPES}
        self.qrm_scope_settings, scope_conflicts = resolve_qrm_scope_settings(
            seq.state for seq in self.sequencers.values() if qrm_scope_key(seq.state) in scope_keys
        )
        if scope_conflicts:
            key = min(scope_conflicts)
            field = min(scope_conflicts[key])
            raise ValueError(f"Conflicting {field} for QRM scope module {key[1]!r}")
        for kind in ("feedback", "triggers"):
            for index, item in enumerate(config.get("inputs", {}).get(kind, [])):
                data = {**item, "input_index": index}
                if "rt_time_ns" in item:
                    self.rt_start_triggers.setdefault(item["target"], []).append(data)
                else:
                    self.schedule(item["time_ns"], 0, item["target"], kind, data)
        for name, trace in config["inputs"].get("digital", {}).items():
            self.sequencers[name].digital_level = trace["initial_level"]
            for index, transition in enumerate(trace["transitions"]):
                self.schedule_ticks(transition["time_ticks"], 0, name, "digital_transition", {**transition, "input_index": index})
        for name in self.digital_sources:
            self.sequencers[name].digital_level = 0

    @property
    def now(self) -> int | float:
        return self.now_ticks // 128 if self.now_ticks % 128 == 0 else self.now_ticks / 128

    def schedule(self, time: int | float, phase: int | float, name: str, action: str, data: Any = None) -> None:
        self.schedule_ticks(int(time * 128), phase, name, action, data)

    def schedule_ticks(self, ticks: int, phase: int | float, name: str, action: str, data: Any = None) -> None:
        self.serial += 1
        heapq.heappush(self.events, (ticks, phase, self.serial, name, action, data))

    def source(self, seq: _Sequencer) -> SourceLocation:
        instruction = seq.state.instructions_by_pc.get(seq.state.pc)
        if instruction is None and seq.state.instructions_by_pc:
            instruction = next(reversed(seq.state.instructions_by_pc.values()))
        return instruction.source if instruction else SourceLocation("<simulation>", 1, 1, "")

    def emit(self, seq: _Sequencer, kind: str, time: int, *, end: int | None = None,
             instruction: Instr | None = None, source: SourceLocation | None = None,
             meta: dict | None = None, lane: str = "rt.simulation") -> core.TimelineEvent:
        end = time if end is None else end
        return core._emit_event(
            seq.state, lane=lane, kind=kind, t0=Concrete(time), t1=Concrete(end),
            duration=Concrete(end-time), label=kind.replace("_", " "), confidence="exact",
            source=instruction.source if instruction else source or self.source(seq), meta=meta,
        )

    def fail(self, seq: _Sequencer, status: str, reason: str, instruction: Instr | None = None, *,
             category: str | None = None, terminal_time_ns: int | float | None = None,
             link_pending_rt_wait: bool = False) -> None:
        if seq.failed:
            return
        edge = Concrete(self.now if terminal_time_ns is None else terminal_time_ns)
        if status == "error":
            self.clip_q1_issue(seq, edge.value)
        waiting_rt = seq.rt_wait[1].instruction if seq.rt_wait is not None and seq.waiting is None else None
        self.close_failed_trigger_edge_windows(seq)
        pending_rt_wait = self.close_waits(seq)
        core._end_concrete_execution(seq.state, status, reason, instruction)
        diagnostic = seq.state.diagnostics[-1]
        category = category or diagnostic.category
        evidence = [pending_rt_wait.id] if link_pending_rt_wait and pending_rt_wait is not None else diagnostic.related_events
        seq.state.diagnostics[-1] = replace(diagnostic, category=category, source=diagnostic.source or self.source(seq),
                                             related_events=evidence)
        source = diagnostic.source or (waiting_rt.source if waiting_rt else self.source(seq))
        self.terminal_edges[seq.state.sequencer_id] = (edge, source, category)
        seq.failed = True

    def stop_at_time_limit(self, seq: _Sequencer) -> None:
        state = seq.state
        q1_issue = self.clip_q1_issue(seq, self.now)
        wait = seq.blocking_rt_wait
        instruction = wait[1].instruction if wait is not None else None
        packet = self.clip_active_rt_packet(state) if wait is None else None
        event_id = None
        if packet is not None:
            instruction = next((item for item in state.instructions_by_pc.values() if item.source == packet.source), None)
            event_id = packet.meta.get("rt_execution_event_id")
        q1_blocker = q1_issue if wait is None and packet is None else None
        if q1_blocker is not None:
            instruction = q1_blocker.instruction
            event_id = q1_blocker.event_id
        elif wait is None and packet is None and seq.waiting is None:
            instruction, event_id = self.deferred_signal_blocker(seq)
        self.fail(seq, "step_limit", f"Reached simulation time limit {self.max_time} ns.", instruction,
                  category="simulation_time_limit", link_pending_rt_wait=wait is not None)
        diagnostic = state.diagnostics[-1]
        details = {**diagnostic.details, "display_message": "Reached simulation time limit.", "time_limit_local_ns": self.max_time}
        if q1_blocker is not None and state.compiled_addresses is not None and q1_blocker.interior_pc is not None:
            state.metadata["execution"]["compiled_pc"] = details["compiled_pc"] = q1_blocker.interior_pc
        state.diagnostics[-1] = replace(diagnostic, details=details)
        self.link_retained_event(state, event_id)

    def deferred_signal_blocker(self, seq: _Sequencer) -> tuple[Instr | None, str | None]:
        state = seq.state
        if seq.acquisition is not None:
            command = seq.acquisition["command"]
            packet = state.rt_packets[command.packet_index]
            return command.instruction, packet.meta.get("rt_execution_event_id")
        scope = self.qrm_scope_latest.get(qrm_scope_key(state))
        if scope is not None and scope[0] is seq and scope[1]["window"]["status"] == "pending":
            capture = scope[1]
            packet_id, event_id = capture["rt_packet_id"], capture["acquire_event_id"]
        else:
            result = next((item for item in reversed(seq.netzero_results)
                           if item["status"] == "modeled"
                           and (item["rt_packet_id"], item["output"]) in seq.netzero_pending), None)
            if result is None:
                return None, None
            packet_id, event_id = result["rt_packet_id"], result["event_id"]
        packet = next((item for item in reversed(state.rt_packets) if item.id == packet_id), None)
        instruction = (next((item for item in state.instructions_by_pc.values() if item.source == packet.source), None)
                       if packet is not None else None)
        return instruction, event_id

    @staticmethod
    def plan_unobserved_latches(issue: _AdmittedQ1Issue, cutoff: int | float) -> list[dict[str, Any]]:
        future = [event for event in issue.latch_events if event.t0.value > cutoff]
        issue.future_latch_event_ids = tuple(event.id for event in future)
        return [{"field": event.meta["field"], "value": event.meta["value"], "ready_local_ns": event.t0.value}
                for event in future]

    def clip_q1_issue(self, seq: _Sequencer, cutoff: int | float) -> _AdmittedQ1Issue | None:
        issue = seq.admitted_q1_issue
        if issue.instruction is None or issue.planned_end_ns <= cutoff:
            return None
        state = seq.state
        state.q1_time_ns = Concrete(cutoff)
        planned_latches = self.plan_unobserved_latches(issue, cutoff) if issue.latch_events else []
        if planned_latches:
            if issue.latch_state_before is not None:
                state.latched_state = issue.latch_state_before
                state.pending_phase_reset = issue.phase_reset_before
            if issue.setter_ids_before is not None:
                seq.setter_ids = issue.setter_ids_before
        if issue.event_id is None:
            return issue
        for index in range(len(state.events) - 1, -1, -1):
            event = state.events[index]
            if event.id != issue.event_id:
                continue
            meta = {**event.meta, "planned_q1_end_ns": issue.planned_end_ns, "observation_cutoff_ns": cutoff}
            if planned_latches:
                meta["planned_latch_changes"] = planned_latches
                if issue.instruction.op == "set_cond":
                    meta.pop("branch_id", None)
                    meta.pop("branch_decision", None)
            ready = {write.register: write.meta["scheduled_ready_ns"] for write in state.pending_register_writes
                     if write.event_id == event.id and "scheduled_ready_ns" in write.meta}
            if ready:
                meta["pending_register_ready_ns"] = ready
            state.events[index] = replace(event, t1=state.q1_time_ns, duration=Concrete(cutoff - event.t0.value), meta=meta)
            break
        return issue

    def close_waits(self, seq: _Sequencer) -> core.TimelineEvent | None:
        if seq.waiting is not None:
            if seq.state.record_trace:
                self.emit(seq, "q1_stall", seq.waiting_since, end=self.now,
                          meta={"reason": seq.waiting, "pending": True}, lane="debug.q1_stall")
            seq.waiting = None
        pending_rt_wait = None
        if seq.rt_wait is not None:
            if seq.state.record_trace:
                kind, command, started = seq.rt_wait
                packet = seq.state.rt_packets[command.packet_index]
                meta = {"pending": True, "rt_packet_id": packet.id,
                        "q1_issue_event_id": packet.meta.get("q1_issue_event_id")}
                # Reaching an armed wait means its condition already permitted execution.
                if condition_issue_id := packet.meta.get("rt_condition_issue_event_id"):
                    meta.update(rt_condition_decision=True, rt_condition_issue_event_id=condition_issue_id,
                                rt_condition_branch_id=packet.meta["rt_condition_branch_id"])
                pending_rt_wait = self.emit(seq, kind, started, end=self.now, instruction=command.instruction, meta=meta)
            seq.rt_wait = None
            self.sync_waiters.pop(seq.state.sequencer_id, None)
        return pending_rt_wait

    def run(self) -> list[core.AnalysisState]:
        while self.events:
            ticks, phase, _serial, name, action, data = heapq.heappop(self.events)
            time = ticks // 128 if ticks % 128 == 0 else ticks / 128
            seq = self.sequencers[name]
            if action == "qrm_scope_end" and self.qrm_scope_latest[qrm_scope_key(seq.state)][1] is not data:
                continue
            if action == "qtm_scope_horizon" and seq.qtm_scope_capture is not data:
                continue
            if action in {"trigger_network_arrival", "trigger_network_uncertain"}:
                request, route = data
                optional = request.optional
                if seq.failed or (seq.rt_done and (optional or route["status"] == "unknown_possible_conflict")):
                    if not optional:
                        seq.signal_pending -= 1
                    if route["status"] in {"scheduled", "scheduled_optional"}:
                        route["status"] = "receiver_unavailable"
                    continue
            if action not in {"qtm_truth_round", "trigger_network_launch", "tb_combine"} and (
                    seq.failed or seq.q1_done and seq.rt_done and seq.acquisition is None and not seq.signal_pending):
                continue
            if action == "rt_prefill_deadline" and seq.rt_started:
                continue
            if time > self.max_time:
                self.now_ticks, self.now_phase = self.max_time * 128, _DIGITAL_EDGE_PHASE
                for active in self.sequencers.values():
                    if not active.failed and not (active.q1_done and active.rt_done and active.acquisition is None and not active.signal_pending):
                        self.stop_at_time_limit(active)
                break
            self.now_ticks, self.now_phase = ticks, phase
            if action == "q1":
                self.q1(seq)
            elif action == "enqueue":
                seq.reserved -= 1
                seq.queue.append(data)
                self.wake_empty_rt(seq)
                if seq.state.record_trace:
                    self.emit(seq, "rt_queue_enqueue", time, instruction=data.instruction, meta={
                        "depth": len(seq.queue), "capacity": self.capacity,
                        "rt_packet_id": seq.state.rt_packets[data.packet_index].id,
                    }, lane="debug.rt_queue")
                if self.startup is None and not seq.rt_started:
                    if len(seq.queue) == self.capacity:
                        self.start_buffered_rt(seq)
                    elif seq.rt_prefill_since is None:
                        seq.rt_prefill_since = time
                        self.schedule(time + POSSIBLE_INITIAL_BUFFER_NS, 2, name, "rt_prefill_deadline")
            elif action == "q1_stop":
                seq.q1_done = True
                self.start_buffered_rt(seq)
                self.wake_empty_rt(seq)
            elif action == "illegal_fault":
                self.illegal_fault(seq, *data)
            elif action == "register_ready":
                if data in seq.state.pending_register_writes:
                    seq.state.pending_register_writes.remove(data)
                    core._apply_register_write(seq.state, data, time=Concrete(time))
            elif action == "rt":
                self.rt(seq)
            elif action == "rt_prefill_deadline":
                self.start_buffered_rt(seq)
            elif action == "feedback":
                self.feedback(seq, data)
            elif action == "triggers":
                self.trigger(seq, data)
            elif action == "generated_trigger_arrival":
                seq.signal_pending -= 1
                self.trigger(seq, data)
            elif action == "trigger_network_launch":
                self.launch_trigger_requests(data)
            elif action == "trigger_network_arrival":
                self.deliver_network_trigger(seq, *data)
            elif action == "trigger_network_uncertain":
                if not data[0].optional:
                    seq.signal_pending -= 1
                self.uncertain_network_trigger(seq, *data)
            elif action == "trigger_uncertain":
                self.uncertain_trigger(seq, data)
            elif action == "trigger_interval":
                self.observe_trigger_interval(seq, *data)
            elif action == "trigger_interval_complete":
                self.complete_trigger_interval(seq, data)
            elif action == "trigger_wait_check":
                self.check_trigger_wait(seq)
            elif action == "acquisition":
                if seq.acquisition is data:
                    self.finish_acquisition(seq, data)
                    if seq.failed:
                        self.close_rt_refusal(seq)
            elif action == "qrm_scope_end":
                seq.signal_pending -= 1
                self.finish_qrm_scope(seq, data)
            elif action == "qtm_scope_horizon":
                self.end_qtm_scope(seq, "horizon")
            elif action == "signal_end":
                seq.signal_pending -= 1
                seq.netzero_pending.remove((data["rt_packet_id"], data["output"]))
                self.emit(seq, "netzero_complete", time, meta=data, lane="rt.signal")
            elif action == "timetag_gate":
                command, event, captured = data
                if command.args[2]:
                    seq.signal_pending -= 1
                else:
                    seq.pending_timetag_closes -= 1
                self.digital_gate(seq, command, event, captured)
                if seq.failed:
                    packet = seq.state.rt_packets[command.packet_index]
                    self.link_retained_event(seq.state, packet.meta.get("rt_execution_event_id"))
                    self.close_rt_refusal(seq)
            elif action == "timetag_complete":
                seq.signal_pending -= 1
                self.finish_digital_gate(seq, *data)
            elif action == "qtm_truth_round":
                self.finish_qtm_truth_round(data)
            elif action == "digital_output":
                seq.signal_pending -= 1
                self.digital_output(seq, *data)
            elif action in {"digital_transition", "digital_arrival"}:
                if action == "digital_arrival":
                    seq.signal_pending -= 1
                seq.digital_level = data["level"]
                event = self.emit(seq, "digital_transition", time, source=SourceLocation("<simulation>", 1, 1, ""),
                                  meta=data, lane="rt.input")
                seq.digital_input_event = event.id
                if (not seq.rt_done or seq.pending_timetag_closes
                        or data["level"] and (seq.qtm_scope_capture or {}).get("mode") == "timetags"):
                    # Effective gates commit before this phase, preserving [open, close).
                    self.schedule_ticks(ticks, _DIGITAL_EDGE_PHASE, name, "digital_edge", {**data, "source_event_id": event.id})
            elif action == "digital_edge":
                self.digital_edge(seq, data)
            elif action == "digital_sample":
                self.sample_digital(seq, data)
            elif action == "tb_combine":
                self.combine_tb(data)
        missing_sync = ", ".join(sorted(self.sync_participants - self.sync_waiters.keys()))
        for owner, capture in self.qrm_scope_latest.values():
            window = capture["window"]
            if window["status"] == "pending":
                edge = self.terminal_edges.get(owner.state.sequencer_id)
                if edge is not None:
                    end = max(edge[0].value, window["start_ns"])
                    window.update(end_ns=end, effective_duration_ns=end - window["start_ns"])
                window["status"] = "incomplete"
                capture.update(sample_status="unavailable", reason="Execution stopped before scope capture completion.")
        for seq in self.sequencers.values():
            edge = self.terminal_edges.get(seq.state.sequencer_id)
            capture_end = edge[0].value if edge is not None else (seq.state.rt_time_ns.value if seq.rt_done else self.now)
            if seq.qtm_scope_capture is not None:
                end_ticks = int(capture_end * 128)
                self.end_qtm_scope(seq, "incomplete", end_ticks=max(end_ticks, seq.qtm_scope_capture["start_ticks"]))
            for attribute in ("timetag_window", "ttl_gate"):
                window = getattr(seq, attribute)
                if window is not None:
                    ended = max(capture_end, window["start_ticks"] / 128)
                    end_ticks = int(ended * 128)
                    window["observation"].update(end_ticks=end_ticks, status="incomplete")
                    command = window["command"]
                    _, event = self.record_digital(
                        seq, command, {"status": "incomplete", "reason": "Acquisition gate was not closed.",
                                       "count": len(window["edges"]) if seq.digital_level is not None else None,
                                       "start_ticks": window["start_ticks"], "end_ticks": end_ticks,
                                       "edge_times_ticks": [edge["time_ticks"] for edge in window["edges"]]}, time_ns=ended,
                    )
                    if seq.failed:
                        continue
                    self.fail(seq, "blocked", "Digital acquisition gate was not closed.", command.instruction,
                              terminal_time_ns=ended)
                    evidence_id = event.id
                    if not seq.state.record_trace:
                        packet = seq.state.rt_packets[command.packet_index]
                        anchors = (packet.meta.get("rt_execution_event_id"), packet.meta.get("q1_issue_event_id"))
                        retained = {candidate.id for candidate in seq.state.events}
                        evidence_id = next((anchor for anchor in anchors if anchor in retained), None)
                    if evidence_id is not None:
                        diagnostic = seq.state.diagnostics[-1]
                        seq.state.diagnostics[-1] = replace(diagnostic, related_events=[evidence_id])
            if not seq.failed and not (seq.q1_done and seq.rt_done and seq.acquisition is None and not seq.signal_pending):
                blocker = seq.waiting or "execution"
                instruction = None
                wait = seq.blocking_rt_wait
                if wait is not None:
                    blocker, command, _started = wait
                    instruction = command.instruction
                reason = (f"No matching wait_sync barrier for: {missing_sync}." if blocker == "sync_wait"
                          else f"No scheduled input can resume {blocker}.")
                self.fail(seq, "blocked", reason, instruction, link_pending_rt_wait=instruction is not None)
        netzero_cutoffs = {(name, packet_id, output): edge[0].value
                          for name, edge in self.terminal_edges.items()
                          for packet_id, output in self.sequencers[name].netzero_pending}
        try:
            signals = self.signals.export(self.now, netzero_cutoffs=netzero_cutoffs) if self.signals is not None else {}
        except ValueError as error:
            signals = {}
            for seq in self.sequencers.values():
                self.fail(seq, "error", f"Signal model: {error}")
        for name, seq in self.sequencers.items():
            signal_summary = signals.get(name, {})
            terminal = self.terminal_edges.get(name)
            if terminal is not None:
                edge, _, category = terminal
                execution = seq.state.metadata["execution"]
                diagnostic = next((item for item in reversed(seq.state.diagnostics) if item.category == category), None)
                display_reason = diagnostic.details.get("display_message") if diagnostic is not None else None
                if display_reason is None:
                    display_reason = execution["reason"]
                    if category in {"rt_queue_underflow", "feedback_queue_overflow"}:
                        display_reason = display_reason.removesuffix(f" at {edge.value} ns.")
                execution["terminal"] = {"local_time_ns": edge.value, "category": category, "display_reason": display_reason}
            if seq.netzero_pending and terminal is not None:
                self.update_netzero_cutoff(seq, signal_summary.get("netzero", {}).get("results", []))
            if seq.acquisition is not None:
                self.close_acquisition_window(seq.acquisition, terminal[0].value if terminal else self.now, "incomplete")
                seq.acquisition = None
            seq.state.stopped = True
            seq.state.metadata["simulation"] = {
                "queue_capacity": self.capacity, "feedback_queue_capacity": self.feedback_capacity,
                "max_time_ns": self.max_time,
                "routes": self.config.get("routes", []), "trigger_routes": self.config.get("trigger_routes", []),
                "acquisition_iq_scale": self.config.get("acquisition_iq_scale", {}),
                "end_time_ns": self.now, "acquisitions": seq.acquisitions, "signals": signal_summary,
                "physical_enabled": self.signals is not None,
                "netzero": seq.netzero_results,
                "digital_bins": list(seq.digital_bins.values()), "clock_tick_ns": 1 / 128,
            }
            if "digital" in seq.rt_settings or name in self.digital_routes:
                seq.state.metadata["simulation"]["digital_output"] = {
                    "initial_level": 0, "transitions": seq.digital_output_transitions,
                    "routes": self.digital_routes.get(name, []),
                }
            if seq.qrm_scope_captures:
                key = qrm_scope_key(seq.state)
                seq.state.metadata["simulation"]["qrm_scope"] = {"captures": seq.qrm_scope_captures}
                if self.qrm_scope_latest[key][0] is seq:
                    seq.state.metadata["simulation"]["qrm_scope"]["buffer"] = self.qrm_scope_buffers.get(key)
            if seq.qtm_trigger_transmissions or seq.qtm_trigger_rounds:
                seq.state.metadata["simulation"]["qtm_triggers"] = {
                    "transmissions": seq.qtm_trigger_transmissions, "truth_rounds": seq.qtm_trigger_rounds,
                }
            if seq.ttl_trigger_transmissions:
                seq.state.metadata["simulation"]["ttl_trigger_transmissions"] = seq.ttl_trigger_transmissions
            if seq.trigger_network_transmissions:
                for transmission in seq.trigger_network_transmissions:
                    if transmission["status"] == "pending":
                        transmission["status"] = "incomplete"
                    for route in transmission["routes"]:
                        if route["status"] in {"pending", "scheduled", "scheduled_optional"}:
                            route["status"] = "incomplete"
                seq.state.metadata["simulation"]["trigger_network_transmissions"] = seq.trigger_network_transmissions
            scope_mode = self.config["digital"].get(name, {}).get("scope_mode")
            if scope_mode is not None:
                seq.state.metadata["simulation"]["qtm_scope"] = {
                    "mode": scope_mode, "entries": seq.qtm_scope_entries, "captures": seq.qtm_scope_captures,
                    "capacity": _QTM_SCOPE_CAPACITY, "dropped_entries": seq.qtm_scope_dropped,
                    "truncated": seq.qtm_scope_dropped > 0 or any(item["status"] == "capacity" for item in seq.qtm_scope_captures),
                    "clock_tick_ns": 1 / 128,
                }
            if self.startup is None:
                seq.state.metadata["simulation"]["initial_buffer"] = {
                    "assumed_ns": ASSUMED_INITIAL_BUFFER_NS, "possible_ns": POSSIBLE_INITIAL_BUFFER_NS,
                    "prefill_ns": seq.rt_prefill_ns,
                }
            else:
                seq.state.metadata["simulation"]["startup_delay_ns"] = self.startup
        self.resolve_network_diagnostic_links()
        # Trace IDs are allocated from the event list length until finalization ends.
        for seq in self.sequencers.values():
            issue = seq.admitted_q1_issue
            if issue.future_latch_event_ids:
                seq.state.events[:] = [event for event in seq.state.events if event.id not in issue.future_latch_event_ids]
        return [seq.state for seq in self.sequencers.values()]

    @staticmethod
    def update_netzero_cutoff(seq: _Sequencer, results: list[dict]) -> None:
        clipped = {(result["rt_packet_id"], result["output"]): result
                   for result in results if result["status"] == "incomplete"}
        if not clipped:
            return
        by_event_id = {}
        for summary in seq.netzero_results:
            if summary["status"] != "modeled":
                continue
            result = clipped.get((summary["rt_packet_id"], summary["output"]))
            if result is not None:
                summary.update(result)
                by_event_id[summary["event_id"]] = result
        for index, event in enumerate(seq.state.events):
            result = by_event_id.get(event.id)
            if result is not None:
                end = result["end_ns"]
                seq.state.events[index] = replace(event, t1=Concrete(end), duration=Concrete(end - event.t0.value),
                                                  meta={**event.meta, **result})

    def resume_q1(self, seq: _Sequencer) -> None:
        if seq.waiting is None or seq.failed:
            return
        if seq.state.record_trace:
            self.emit(seq, "q1_stall", seq.waiting_since, end=self.now, meta={"reason": seq.waiting}, lane="debug.q1_stall")
        seq.waiting = None
        self.schedule(self.now, 3, seq.state.sequencer_id, "q1")

    @staticmethod
    def link_retained_event(state: core.AnalysisState, event_id: str | None) -> None:
        diagnostic = state.diagnostics[-1]
        if (event_id is not None and event_id not in diagnostic.related_events
                and any(event.id == event_id for event in state.events)):
            state.diagnostics[-1] = replace(diagnostic, related_events=[*diagnostic.related_events, event_id])

    def clip_rt_and_link(self, state: core.AnalysisState, *, attempt: core.RtPacket | None = None) -> None:
        packet = self.clip_active_rt_packet(state) or attempt
        if packet is not None:
            self.link_retained_event(state, packet.meta.get("rt_execution_event_id"))

    def close_rt_refusal(self, seq: _Sequencer, *, attempt: core.RtPacket | None = None) -> None:
        # fail() already closes active Q1 issues for error outcomes.
        if seq.state.metadata["execution"]["status"] == "blocked":
            self.clip_q1_issue(seq, self.now)
        self.clip_rt_and_link(seq.state, attempt=attempt)

    def stop_on_q1_validation(self, seq: _Sequencer, instruction: Instr,
                              refusal: tuple[str, str] | None = None) -> None:
        state = seq.state
        if refusal is not None:
            status, reason = refusal
            core._end_concrete_execution(state, status, reason, instruction)
        self.clip_rt_and_link(state)
        self.close_failed_trigger_edge_windows(seq)
        self.close_waits(seq)
        self.terminal_edges[state.sequencer_id] = (Concrete(self.now), instruction.source, state.diagnostics[-1].category)
        seq.failed = True

    def q1(self, seq: _Sequencer) -> None:
        state = seq.state
        if seq.q1_done:
            return
        state.q1_time_ns = Concrete(self.now)
        instruction = core._execution_instruction(state)
        if instruction is None:
            if state.pc == len(state.instructions_by_pc) and any(
                item.details.get("source_line_skipped") for item in state.diagnostics
            ):
                seq.q1_done = True
                self.start_buffered_rt(seq)
                return
            self.fail(seq, "error", "Execution reached a PC without an instruction or stop.")
            self.clip_rt_and_link(state)
            return
        if state.metadata["execution"]["steps"] >= self.max_steps:
            self.fail(seq, "step_limit", f"Reached the {self.max_steps} instruction limit.", instruction)
            self.clip_rt_and_link(state)
            return
        binding = state.bound_instructions.get(instruction.pc) if state.interior_pc is None else None
        spec, invalid, diagnostic_start = core._validate_instruction(state, instruction, seq.symbols, binding)
        scope = core._scope(seq.symbols, state)
        duration_error = None if invalid else core._invalid_rt_duration(instruction, spec, scope)
        ready = core._concrete_instruction_ready(
            state, instruction, seq.symbols, spec, invalid, diagnostic_start,
            supported_ops=_Q1_ISSUABLE, rt_duration_error=duration_error,
        )
        choice = None
        if ready and spec.category == "branch":
            occurrence = seq.branch_occurrences.get(instruction.pc, 0) + 1
            seq.branch_occurrences[instruction.pc] = occurrence
            key = (state.sequencer_id, instruction.pc, occurrence)
            choice = self.branch_choices.get(key)
            if choice is not None:
                self.applied_branch_choices.add(key)
                state.metadata.setdefault("branches", {}).setdefault("assumptions", []).append({
                    "branch_id": f"{state.sequencer_id}:simulation:{instruction.pc}:{occurrence}",
                    "assumed_branch_path": "taken" if choice else "fallthrough", "assumption_source": "user",
                })
            ready = core._concrete_branch_destination_ready(state, instruction, scope, choice)
        if not ready:
            self.stop_on_q1_validation(seq, instruction)
            return
        linq_meta = {}
        linq_error = None
        normalized_op = None
        semantic_args = None
        if spec.emits_rt_packet:
            operands = [resolve_arg_value(arg, scope).value for arg in instruction.args]
            duration_index = rt_duration_arg_for_instruction(spec, instruction)
            rt_duration = operands[duration_index] if duration_index is not None else 0
            alias = instruction.op == "fb_cmd" or instruction.op in PACKED_LINQ_OPERATIONS and len(operands) == 2
            semantic_args = operands
            if alias:
                linq_meta["op"] = instruction.op
                if instruction.op == "fb_cmd":
                    linq_meta["linq_command_id"] = operands[0]
                try:
                    normalized_op, semantic_args = decode_linq_command(instruction.op, operands)
                    linq_meta.update(normalized_op=normalized_op, normalized_operand_snapshot=semantic_args)
                except UnsupportedLinqCommand as error:
                    linq_error = ("blocked", str(error))
                except ValueError as error:
                    linq_error = ("error", str(error))
            semantic_op = normalized_op or instruction.op
            for index, role, lower, upper in core._official_operand_ranges(semantic_op, len(semantic_args)):
                if linq_error or index >= len(semantic_args):
                    continue
                value = semantic_args[index]
                if not lower <= value <= upper:
                    reason = f"{semantic_op} resolved {role} {value} is outside {lower}..{upper}."
                    if not alias:
                        self.stop_on_q1_validation(seq, instruction, ("error", reason))
                        return
                    linq_error = ("error", reason)
            if semantic_op == "fb_acq_tb_mock":
                if not 0 <= rt_duration <= core._RT_DURATION_MAX:
                    reason = f"{instruction.op} fourth operand {rt_duration} is outside 0..{core._RT_DURATION_MAX}."
                    self.stop_on_q1_validation(seq, instruction, ("error", reason))
                    return
                if linq_error is None:
                    linq_error = ("blocked", MOCK_LIMITATION)
        if spec.emits_rt_packet and len(seq.queue) + seq.reserved >= self.capacity:
            seq.waiting, seq.waiting_since = "queue_full", self.now
            return
        feedback = None
        if instruction.op in {"fb_pop_data", "fb_pull_data"}:
            requested_id = None
            if instruction.op == "fb_pop_data":
                id_value = resolve_arg_value(instruction.args[0], scope)
                if not isinstance(id_value, Concrete):
                    self.stop_on_q1_validation(seq, instruction, ("blocked", "Feedback ID is unknown."))
                    return
                requested_id = id_value.value
                limitation = feedback_pop_limitation(requested_id)
                if limitation is not None:
                    self.stop_on_q1_validation(seq, instruction, ("blocked", limitation))
                    return
            feedback, discarded = take_feedback(seq.feedback, requested_id)
            if discarded and state.record_trace:
                self.emit(seq, "feedback_discard", self.now, instruction=instruction,
                    meta={"requested_id": requested_id, "input_event_ids": [item["event_id"] for item in discarded]}, lane="rt.feedback")
            if feedback is None:
                seq.waiting, seq.waiting_since = "feedback_input", self.now
                self.start_buffered_rt(seq)
                return
        if instruction.op == "set_cond":
            resolved_condition = tuple(resolve_arg_value(arg, scope) for arg in instruction.args)
            # Readiness permits unknown mask/operator only when disabled.
            condition_values = tuple(value.value if isinstance(value, Concrete) else 0 for value in resolved_condition)
            if condition_values[0] and condition_values[2] > 5:
                self.stop_on_q1_validation(seq, instruction, ("blocked", "Conditional operators 6 and 7 have no supported semantics."))
                return
        core._begin_execution_step(state)
        register_metadata = None
        before = None
        if state.record_trace:
            register_metadata = core._register_issue_metadata(state, instruction, scope)
            before = dict(state.alu_flags) if state.alu_flags is not None else None
            state.current_resolved_args = core._resolved_args_for_instruction(instruction, scope, state)
        event_start = len(state.events)
        pending_start = len(state.pending_register_writes)
        duration = binding.duration_ns if binding is not None else None
        if normalized_op == "upd_thres":
            duration = get_instruction_spec(normalized_op).q1_time_model(instruction)
        elif duration is None:
            duration = spec.q1_time_model(core._instruction_for_q1_timing(instruction, state, seq.symbols))
        state.q1_time_ns = Concrete(self.now + duration)
        packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}" if spec.emits_rt_packet else None
        future_latch = spec.category == "latched" and state.q1_time_ns.value > self.max_time
        latch_state_before = None
        phase_reset_before = state.pending_phase_reset
        setter_ids_before = None
        if instruction.op in {"mulu16", "muls16"}:
            left, right = [resolve_arg_value(arg, scope).value & 0xFFFF for arg in instruction.args[:2]]
            if instruction.op == "muls16":
                left = left - 0x10000 if left & 0x8000 else left
                right = right - 0x10000 if right & 0x8000 else right
            value = Concrete((left * right) & 0xFFFFFFFF)
            register = core._register_name(instruction.args[2], state)
            provenance = core._binary_provenance(state, instruction, instruction.args[0], instruction.args[1], register, value)
            core._set_alu_flags(state, instruction, core._logic_flags(value.value))
            core._schedule_register_write(state, instruction, register, value, provenance)
        elif spec.category == "classical":
            core._execute_classical(state, instruction, seq.symbols)
        elif spec.category == "latched":
            setter_ids_before = seq.setter_ids.copy()
            if instruction.op in {"reset_netzero", "set_time_ref"}:
                field_name = "netzero_reset" if instruction.op == "reset_netzero" else "time_ref"
                if not future_latch:
                    if instruction.op == "reset_netzero":
                        seq.netzero_reset = True
                    else:
                        seq.time_ref_pending = True
                self.emit(seq, "latched_state_pending", state.q1_time_ns.value, instruction=instruction,
                          meta={"field": field_name, "value": True}, lane="state.pending")
            elif instruction.op == "set_cond":
                if not future_latch:
                    seq.condition = condition_values
                self.emit(seq, "latched_state_pending", state.q1_time_ns.value, instruction=instruction,
                          meta={"field": "condition", "value": core._plain_value(resolved_condition)}, lane="state.pending")
            else:
                old_rt = state.rt_time_ns
                observed_latches = state.latched_state
                copied_latches = replace(observed_latches, pending_since=observed_latches.pending_since.copy(),
                                         pending_event_ids=observed_latches.pending_event_ids.copy())
                if future_latch:
                    state.latched_state = copied_latches
                    latch_state_before = observed_latches
                else:
                    latch_state_before = copied_latches
                state.rt_time_ns = state.q1_time_ns
                core._execute_latched(state, instruction, seq.symbols)
                state.rt_time_ns = old_rt
                if future_latch:
                    state.latched_state = observed_latches
                    state.pending_phase_reset = phase_reset_before
                elif instruction.op == "set_ph":
                    seq.phase_revision += 1
        elif feedback is not None:
            values = [(instruction.args[1], feedback["value"])]
            if instruction.op == "fb_pull_data":
                values.insert(0, (instruction.args[0], feedback["id"]))
            for argument, result in values:
                register = core._register_name(argument, state)
                value = Concrete(result & 0xFFFFFFFF)
                provenance = {}
                if state.record_trace:
                    provenance = core._register_provenance(state, register, expression=f"feedback id {feedback['id']}", value=value,
                        steps=[core._provenance_step(instruction, f"feedback id {feedback['id']}", value)])
                    provenance["input_event_id"] = feedback["event_id"]
                core._schedule_register_write(state, instruction, register, value, provenance)
        issue = None
        if state.record_trace or spec.emits_rt_packet or (instruction.op == "stop" and state.latched_state.pending_since):
            issue = core._emit_q1_issue(
                state, instruction, Concrete(self.now), state.q1_time_ns, packet_id,
                register_metadata=register_metadata,
            )
            issue.meta.update(linq_meta)
        if instruction.op == "set_cond" and not future_latch:
            seq.condition_branch_id = seq.condition_issue_id = None
            if seq.condition[0]:
                occurrence = seq.branch_occurrences.get(instruction.pc, 0) + 1
                seq.branch_occurrences[instruction.pc] = occurrence
                seq.condition_branch_id = f"{state.sequencer_id}:set_cond:{instruction.pc}:{occurrence}"
                if issue is not None:
                    seq.condition_issue_id = issue.id
                    seq.condition_controls[issue.id] = issue
                    issue.meta.update(branch_id=seq.condition_branch_id, branch_decision="runtime_dependent")
        latch_events = ()
        for event in state.events[event_start:]:
            if event.kind == "latched_state_pending":
                latch_events += (event,)
                if not future_latch:
                    seq.setter_ids[event.meta["field"]] = event.id
                    if issue is not None:
                        event.meta["q1_issue_event_id"] = issue.id
        if spec.category == "branch":
            core._execute_concrete_branch(state, instruction, issue, seq.symbols, q1_t0=Concrete(self.now),
                                          defer_register_write=True, forced_decision=choice)
            if issue is not None:
                issue.meta["branch_occurrence"] = occurrence
        if state.record_trace:
            issue.meta.update(source_pc=instruction.pc, execution_index=state.q1_issue_index,
                              flags_before=before, flags_after=dict(state.alu_flags) if state.alu_flags is not None else None)
            if feedback is not None:
                issue.meta["input_event_ids"] = [feedback["event_id"]]
        for pending in state.pending_register_writes[pending_start:]:
            ready_time = state.q1_time_ns.value + (0 if instruction.op == "loop" else 4)
            pending.meta["scheduled_ready_ns"] = ready_time
            self.schedule(ready_time, 1, state.sequencer_id, "register_ready", pending)
        admitted = seq.admitted_q1_issue
        admitted.instruction = instruction
        admitted.planned_end_ns = state.q1_time_ns.value
        admitted.interior_pc = state.interior_pc
        admitted.event_id = issue.id if issue is not None else None
        if latch_events or admitted.latch_events:
            admitted.latch_events = latch_events
            admitted.latch_state_before = latch_state_before
            admitted.phase_reset_before = phase_reset_before
            admitted.setter_ids_before = setter_ids_before
            admitted.future_latch_event_ids = ()
        if future_latch and latch_events:
            planned = self.plan_unobserved_latches(admitted, self.max_time)
            if issue is not None:
                issue.meta["planned_latch_changes"] = planned
        state.current_resolved_args = []
        state.executed_pcs.add(state.pc)
        core._advance_execution(state)
        state.q1_issue_index += 1
        state.metadata["execution"]["steps"] += 1
        if spec.emits_rt_packet:
            applies_latched_state = spec.applies_latched_state or normalized_op == "upd_thres"
            phase_reset = applies_latched_state and state.pending_phase_reset is True
            updates = core._consume_applied_state(state) if applies_latched_state else {}
            settings = self.latched_snapshot(state) if applies_latched_state else {}
            settings.update({field: updates[field] for field in ("phase_delta", "phase_reset") if field in updates})
            packet = core.RtPacket(packet_id, state.sequencer_id, instruction.source, instruction.op, Concrete(rt_duration),
                                   Concrete(self.now), state.q1_time_ns, None, None, "exact",
                                   {"operand_snapshot": operands, "applied_state": settings,
                                    "q1_issue_event_id": issue.id, **linq_meta})
            if seq.condition_branch_id:
                packet.meta.update(rt_condition_branch_id=seq.condition_branch_id,
                                   rt_condition_issue_event_id=seq.condition_issue_id)
            if applies_latched_state:
                packet.meta["captured_phase_reset"] = phase_reset
                packet.meta["captured_netzero_reset"] = seq.netzero_reset
                packet.meta["captured_time_ref"] = seq.time_ref_pending
            state.rt_packets.append(packet)
            command = _Command(instruction, semantic_args, len(state.rt_packets)-1, rt_duration, settings, list(updates), seq.condition,
                               {key: seq.setter_ids[key] for key in settings if key in seq.setter_ids},
                               phase_reset,
                               seq.netzero_reset if applies_latched_state else False,
                               seq.time_ref_pending if applies_latched_state else False,
                               phase_revision=seq.phase_revision, normalized_op=normalized_op, linq_error=linq_error)
            if applies_latched_state:
                seq.netzero_reset = False
                seq.time_ref_pending = False
            seq.reserved += 1
            self.schedule(state.q1_time_ns.value, 1, state.sequencer_id, "enqueue", command)
        if instruction.op == "illegal":
            self.schedule(state.q1_time_ns.value, 1, state.sequencer_id, "illegal_fault", (instruction, issue))
        elif instruction.op == "stop":
            seq.stop_instruction = instruction
            code = core._record_stop_code(state, instruction, scope)
            if issue is not None:
                issue.meta["stop_code"] = code
                seq.stop_issue_id = issue.id
            self.schedule(state.q1_time_ns.value, 1, state.sequencer_id, "q1_stop")
        else:
            self.schedule(state.q1_time_ns.value, 3, state.sequencer_id, "q1")

    def illegal_fault(self, seq: _Sequencer, instruction: Instr, issue: core.TimelineEvent | None) -> None:
        state = seq.state
        edge = Concrete(self.now)
        self.close_failed_trigger_edge_windows(seq)
        self.close_waits(seq)
        core._record_illegal_instruction(state, instruction, issue)
        for command in seq.queue:
            state.rt_packets[command.packet_index].meta["discarded_by_fault"] = True
        seq.queue.clear()
        for index, packet in enumerate(state.rt_packets):
            if isinstance(packet.rt_t1, Concrete) and packet.rt_t1.value > self.now:
                self.clip_rt_packet(state, index, interrupted_by_fault=True)
        state.rt_time_ns = edge
        self.terminal_edges[state.sequencer_id] = (edge, instruction.source, "illegal_instruction")
        seq.q1_done = seq.rt_done = seq.failed = True

    def clip_active_rt_packet(self, state: core.AnalysisState) -> core.RtPacket | None:
        for index in range(len(state.rt_packets) - 1, -1, -1):
            packet = state.rt_packets[index]
            if (isinstance(packet.rt_t0, Concrete) and isinstance(packet.rt_t1, Concrete)
                    and packet.rt_t0.value <= self.now < packet.rt_t1.value):
                self.clip_rt_packet(state, index, observation_cutoff_ns=self.now, planned_rt_end_ns=packet.rt_t1.value)
                state.rt_time_ns = Concrete(self.now)
                return packet
        return None

    def clip_rt_packet(self, state: core.AnalysisState, packet_index: int, **metadata: Any) -> None:
        packet = state.rt_packets[packet_index]
        edge = Concrete(self.now)
        state.rt_packets[packet_index] = replace(packet, rt_t1=edge, duration=Concrete(self.now - packet.rt_t0.value),
                                                 meta={**packet.meta, **metadata})
        for index, event in enumerate(state.events):
            if (event.meta.get("rt_packet_id") == packet.id and event.lane != "rt.signal"
                    and isinstance(event.t1, Concrete) and event.t1.value > self.now):
                state.events[index] = replace(event, t1=edge, duration=Concrete(self.now - event.t0.value),
                                              meta={**event.meta, **metadata})

    @staticmethod
    def latched_snapshot(state: core.AnalysisState) -> dict:
        names = {"marker": "marker", "awg_gain": "awg_gain", "awg_offset": "awg_offset", "frequency": "frequency",
                 "phase": "phase", "digital": "digital", "scope_enable": "scope_enable"}
        return {key: core._plain_value(getattr(state.latched_state, attr)) for key, attr in names.items()
                if getattr(state.latched_state, attr) is not None}

    def start_buffered_rt(self, seq: _Sequencer) -> None:
        if seq.q1_done and not seq.state.rt_packets:
            seq.rt_done = True
            return
        if self.startup is not None or seq.rt_started:
            return
        if not seq.queue:
            seq.rt_done = seq.q1_done
            return
        first_enqueue = seq.rt_prefill_since if seq.rt_prefill_since is not None else self.now
        seq.rt_prefill_ns = self.now - first_enqueue
        if seq.rt_prefill_ns > ASSUMED_INITIAL_BUFFER_NS:
            seq.state.diagnostics.append(Diagnostic(
                severity="info", category="initial_rt_buffer_estimate", source=seq.queue[0].instruction.source,
                message="The simulated RT queue prefill uses the tentative window above 1 ms, up to 2 ms.",
            ))
        seq.rt_started = True
        self.schedule(self.now, 2, seq.state.sequencer_id, "rt")

    def underflow(self, seq: _Sequencer) -> None:
        marker = None
        if seq.state.record_trace:
            marker = self.emit(seq, "underflow_warning", self.now, meta={
                "simulated": True, "depth": 0, "queue_capacity": self.capacity,
                "diagnostic_message": RT_QUEUE_UNDERFLOW_DESCRIPTION,
            })
        self.fail(seq, "error", f"RT queue underflow at {self.now} ns.", category="rt_queue_underflow")
        if marker is not None:
            seq.state.diagnostics[-1] = replace(seq.state.diagnostics[-1], related_events=[marker.id])

    def wake_empty_rt(self, seq: _Sequencer) -> None:
        # Only the failed dequeue sets this flag, after the zero packet's 4 ns.
        if seq.rt_empty_wait:
            seq.rt_empty_wait = False
            self.schedule(self.now, 2, seq.state.sequencer_id, "rt")

    def rt(self, seq: _Sequencer) -> None:
        if seq.rt_done or seq.rt_wait is not None:
            return
        state = seq.state
        if not seq.queue:
            if seq.q1_done and seq.reserved == 0:
                seq.rt_done = True
                self.close_trigger_edge_window(seq.ttl_gate)
                if not seq.pending_timetag_closes:
                    self.close_trigger_edge_window(seq.timetag_window)
                state.rt_time_ns = Concrete(self.now)
                if state.record_trace:
                    stop_meta = {"stop_code": state.metadata["execution"]["stop_code"],
                                 "q1_issue_event_id": seq.stop_issue_id} if seq.stop_instruction is not None else None
                    self.emit(seq, "stop", self.now, lane="rt.control", instruction=seq.stop_instruction, meta=stop_meta)
                try:
                    if self.signals is not None:
                        self.signals.apply(state.sequencer_id, self.now, "stop", [], {**seq.rt_settings, "_updated_fields": []})
                except ValueError as error:
                    self.fail(seq, "error", f"Signal model: {error}")
            elif seq.rt_guard:
                self.underflow(seq)
            else:
                seq.rt_empty_wait = True
            return
        triggers = self.rt_start_triggers.pop(state.sequencer_id, [])
        if triggers:
            for trigger in triggers:
                self.schedule(self.now + trigger["rt_time_ns"], 0, state.sequencer_id, "triggers", trigger)
            # Same-time trigger arrivals run before the first RT command.
            self.schedule(self.now, 2, state.sequencer_id, "rt")
            return
        command = seq.queue.popleft()
        if state.record_trace or command.packet_index == 0:
            self.emit(seq, "rt_queue_dequeue", self.now, instruction=command.instruction,
                      meta={"depth": len(seq.queue), "capacity": self.capacity, "rt_packet_id": state.rt_packets[command.packet_index].id}, lane="debug.rt_queue")
        if seq.waiting == "queue_full":
            self.resume_q1(seq)
        condition = command.condition
        condition_meta = state.rt_packets[command.packet_index].meta
        condition_branch_id = condition_meta.get("rt_condition_branch_id")
        control = seq.condition_controls.get(condition_meta.get("rt_condition_issue_event_id"))
        if control is not None:
            control.meta["rt_condition_control"] = True
        choice = self.branch_assumptions.get(condition_branch_id)
        taken = True
        if condition[0]:
            taken = (choice == "taken" if choice in {"taken", "fallthrough"} else
                     self.decide_trigger_condition(seq, condition[1], condition[2]))
        if taken is None:
            acquisition_addresses = seq.truth_trigger_rounds.keys() | {address for _, address in seq.threshold_trigger_cases}
            acquisition = any(condition[1] & (1 << (address - 1)) for address in acquisition_addresses)
            edge_sources = [interval.source_event_id for address, counts in seq.edge_trigger_counts.items()
                            if condition[1] & (1 << (address - 1)) for interval in counts.intervals]
            if edge_sources:
                self.block_trigger_interval(seq, command, edge_sources, "change this set_cond decision")
            else:
                evidence = tuple(arrival for address, arrival in seq.uncertain_trigger_evidence.items()
                                 if condition[1] & (1 << (address - 1)) and seq.uncertain_trigger_counts.get(address, 0) > 0)
                self.block_uncertain_trigger(seq, command, acquisition, "change this set_cond decision", evidence)
        elif not taken:
            self.finish_rt(seq, command, self.now, condition[3], skipped=True)
        elif command.instruction.op == "wait_sync":
            if state.sequencer_id not in self.sync_participants:
                self.fail(seq, "blocked", f"{DISABLED_SYNC_REASON}.", command.instruction)
                self.clip_q1_issue(seq, self.now)
                return
            seq.rt_wait = ("sync_wait", command, self.now)
            self.sync_waiters[state.sequencer_id] = (command, self.now)
            if self.sync_waiters.keys() == self.sync_participants:
                if self.synq_epoch_ticks is None and len(self.sync_participants) > 1:
                    self.synq_epoch_ticks = self.now_ticks
                for name, (waiting, started) in list(self.sync_waiters.items()):
                    peer = self.sequencers[name]
                    peer.rt_wait = None
                    packet = peer.state.rt_packets[waiting.packet_index]
                    self.emit(peer, "sync_wait", started, end=self.now, instruction=waiting.instruction,
                              meta={"rt_packet_id": packet.id})
                    self.finish_rt(peer, waiting, started, self.now-started+waiting.duration)
                self.sync_waiters.clear()
        elif command.instruction.op == "wait_trigger":
            address = command.args[0]
            tokens = seq.trigger_tokens.setdefault(address, deque())
            possible = seq.uncertain_trigger_tokens.get(address)
            while tokens and tokens[0]["time_ns"] < self.now:
                tokens.popleft()
            if tokens:
                token = tokens.popleft()
                self.finish_rt(seq, command, self.now, command.duration, input_event_id=token["event_id"])
            else:
                seq.rt_wait = ("trigger_wait", command, self.now)
                if seq.edge_trigger_intervals or (possible is not None and possible.latest_ticks >= self.now_ticks):
                    self.schedule_trigger_wait_check(seq)
        else:
            self.finish_rt(seq, command, self.now, command.duration)

    def finish_rt(self, seq: _Sequencer, command: _Command, started: int, duration: int, *, skipped: bool = False, input_event_id: str | None = None) -> None:
        if not skipped and command.linq_error is not None:
            status, reason = command.linq_error
            self.fail(seq, status, reason, command.instruction)
            if status == "blocked":
                self.clip_q1_issue(seq, self.now)
            return
        self.dispatch_rt(seq, command, started, duration, skipped=skipped, input_event_id=input_event_id)
        if seq.failed:
            self.close_rt_refusal(seq, attempt=seq.state.rt_packets[command.packet_index])

    def dispatch_rt(self, seq: _Sequencer, command: _Command, started: int, duration: int, *,
                    skipped: bool, input_event_id: str | None) -> None:
        state, op, args = seq.state, command.op, command.args
        end = started + duration
        queue_duration = command.condition[3] if skipped else command.duration
        seq.rt_guard = queue_duration != 0
        packet = state.rt_packets[command.packet_index]
        applied_state = dict(command.settings)
        setter_ids = dict(command.setter_ids)
        updated_fields = set(command.updated_fields)
        if not skipped and "captured_phase_reset" in packet.meta:
            if command.phase_revision != seq.rt_phase_revision:
                updated_fields.add("phase")
            if "phase" not in updated_fields:
                if command.phase_reset:
                    applied_state["phase"] = 0
                    setter_ids.pop("phase", None)
                elif "phase" in seq.rt_settings:
                    applied_state["phase"] = seq.rt_settings["phase"]
                    if applied_state["phase"] != command.settings.get("phase"):
                        setter_ids.pop("phase", None)
            seq.rt_phase_revision = command.phase_revision
        one_shot_fields = {"phase_delta", "phase_reset"}
        committed_setter_ids = {}
        if not skipped:
            committed_setter_ids = {field: setter_id for field, setter_id in setter_ids.items()
                                    if field in one_shot_fields or seq.rt_setter_ids.get(field) != setter_id}
            seq.rt_setter_ids.update({field: setter_id for field, setter_id in committed_setter_ids.items()
                                      if field not in one_shot_fields})
        meta = {**packet.meta, "rt_packet_id": packet.id, "applied_state": applied_state,
                "applied_state_mode": "snapshot", "applied_updated_fields": sorted(updated_fields),
                "applied_setter_event_ids": setter_ids, "committed_setter_event_ids": committed_setter_ids,
                "queue_duration_ns": queue_duration, "rt_execution_ns": self.now,
                "next_dequeue_not_before_ns": end if queue_duration else self.now + 4}
        if op in {"acquire_ttl", "acquire_timetags"}:
            meta["digital_gate_timing"] = "effective"
        if input_event_id is not None:
            meta["input_event_ids"] = [input_event_id]
        if skipped:
            meta.update(condition=list(command.condition), conditional_skipped=True,
                        captured_state=command.settings, captured_setter_event_ids=command.setter_ids,
                        applied_state={}, applied_updated_fields=[], applied_setter_event_ids={})
        elif command.phase_reset:
            meta["phase_reset"] = True
        if not skipped and command.netzero_reset:
            meta["netzero_reset"] = True
        if not skipped and command.time_ref:
            seq.time_ref_ticks = self.now_ticks
            meta["time_reference_ticks"] = self.now_ticks
        state.rt_packets[command.packet_index] = replace(packet, rt_t0=Concrete(started), rt_t1=Concrete(end), duration=Concrete(duration), meta=meta)
        state.rt_time_ns = Concrete(end)
        if not skipped and op == "play":
            for path, waveform_index in enumerate(args[:2]):
                pulse_event = self.emit(seq, "play", started, end=end, instruction=command.instruction,
                          meta={**meta, "path": path, "waveform_index": waveform_index}, lane=f"rt.path{path}")
        else:
            kind = "conditional_skip" if skipped else "acquire" if op.startswith("acquire") else op
            if op.startswith("acquire"):
                meta.update(op=op, acquisition_index=args[0], bin_index=args[1])
            if op == "play_netzero":
                meta["maxt_ns"] = args[0]
            pulse_event = self.emit(seq, kind, started, end=end, instruction=command.instruction, meta=meta,
                      lane="rt.branch" if skipped else f"rt.{kind}")
        meta["rt_execution_event_id"] = pulse_event.id
        if not skipped and op in core._NETZERO_PULSE_OPS:
            if state.netzero_window is not None:
                core._diagnose_netzero_gap(state, state.netzero_window, pulse_event, self.now)
            state.netzero_window = (core._NetZeroWindow(command.instruction.source, pulse_event.id, self.now, args[0], None)
                                    if op == "play_netzero" else None)
        if not skipped and "marker" in command.settings:
            core._emit_marker_state_event(state, command.instruction, duration=Concrete(duration), t0=Concrete(started), t1=Concrete(end),
                                          packet_id=packet.id, value=command.settings["marker"])
        if not skipped:
            changed = [key for key, value in applied_state.items() if seq.rt_settings.get(key) != value]
            seq.rt_settings.update({field: value for field, value in applied_state.items()
                                    if field not in one_shot_fields})
            settings = {**seq.rt_settings, **applied_state, "_updated_fields": sorted(updated_fields.union(changed)),
                        "_phase_reset": command.phase_reset, "_netzero_reset": command.netzero_reset, "_rt_packet_id": packet.id}
            if "scope_enable" in settings["_updated_fields"] and not settings["scope_enable"]:
                self.end_qtm_scope(seq, "disabled", command=command)
            if "digital" in settings["_updated_fields"]:
                value, mask, delay_ticks = command.settings["digital"]
                if mask != 1:
                    self.fail(seq, "blocked", "Only mask 1 is modeled for set_digital output.", command.instruction)
                    return
                seq.signal_pending += 1
                self.schedule_ticks(self.now_ticks + delay_ticks, 2.25, state.sequencer_id, "digital_output",
                                    (command, pulse_event.id, value & 1, delay_ticks))
            try:
                netzero_results = []
                if self.signals is not None:
                    netzero_results = self.signals.apply(state.sequencer_id, self.now, op, args, settings)
                elif op == "play_netzero" or command.netzero_reset:
                    netzero_results = [{"status": "unmodeled", "reason": "No physical output model configured."}]
                for result in netzero_results:
                    signal_event = self.emit(seq, "netzero_result", self.now, end=result.get("end_ns", self.now), instruction=command.instruction,
                              meta={**result, "maxt_ns": args[0] if op == "play_netzero" else None,
                                    "rt_packet_id": packet.id, "source_event_id": pulse_event.id}, lane="rt.signal")
                    seq.netzero_results.append({**signal_event.meta, "start_ns": self.now, "event_id": signal_event.id})
                    if result["status"] == "modeled" and result["end_ns"] > self.now:
                        seq.signal_pending += 1
                        seq.netzero_pending.add((packet.id, result["output"]))
                        self.schedule(result["end_ns"], 1, state.sequencer_id, "signal_end",
                                      {"source_event_id": signal_event.id, "rt_packet_id": packet.id, "output": result["output"]})
            except ValueError as error:
                self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                return
            if op == "set_latch_en":
                if seq.trigger_counters.enabled and not args[0]:
                    for interval in seq.edge_trigger_intervals:
                        if interval.full_latch_epoch == seq.trigger_latch_epoch and interval.latch_end_ticks is None:
                            interval.latch_end_ticks = self.now_ticks + (not interval.after_rt)
                seq.trigger_counters.enabled = bool(args[0])
                if args[0]:
                    self.update_uncertain_trigger_counts(seq)
                    self.recheck_trigger_intervals(seq)
            elif op == "latch_rst":
                seq.trigger_counters.counts.clear()
                self.update_uncertain_trigger_counts(seq, reset=True)
                seq.trigger_latch_epoch += 1
                seq.edge_trigger_counts.clear()
                if seq.trigger_counters.enabled:
                    self.recheck_trigger_intervals(seq)
            elif op == "fb_com_data":
                self.send(seq, args[0], args[1], command.instruction, source_packet=packet)
            elif op == "fb_acq_tb_id":
                seq.tb_id = args[0]
            elif op == "fb_acq_iq_id":
                seq.iq_id = args[0]
            elif op == "fb_acq_tb_valid":
                seq.tb_valid = args[0]
            elif op == "fb_acq_iq_shift":
                seq.iq_shift = args[0]
            elif op in LINQ_CONFIGURATION_STREAMS:
                limitation = linq_configuration_limitation(op, args)
                if limitation is not None:
                    self.fail(seq, "blocked", limitation, command.instruction)
                    return
                if op in PACKED_LINQ_CONFIGS:
                    setattr(seq, "tb_cfg" if op == "fb_acq_tb_cfg" else "com_cfg", tuple(args[:3]))
                else:
                    setattr(seq, "tb_extra" if op == "fb_acq_tb_extra" else "com_extra", tuple(args[:2]))
            elif op in {"fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id"}:
                seq.qtm_streams[op] = args[0]
            elif op in {"acquire_timetags", "acquire_ttl", "acquire_digital", "upd_thres"}:
                self.digital_command(seq, command, pulse_event)
            elif op in {"acquire", "acquire_weighted", "acquire_weighed"}:
                self.start_qrm_scope(seq, pulse_event)
                if seq.acquisition is not None:
                    self.finish_acquisition(seq, seq.acquisition, interrupted_by=pulse_event)
                    if seq.failed:
                        return
                try:
                    acquisition_duration, integration_source = self.acquisition_timing(seq, command)
                except ValueError as error:
                    self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                    return
                if acquisition_duration <= 0:
                    self.fail(seq, "blocked", "Zero-duration RT acquisition requires a configured integration length or integration weights.",
                              command.instruction)
                    return
                window = {"start_ns": self.now, "end_ns": None, "planned_duration_ns": acquisition_duration,
                          "configured_duration_ns": None if integration_source == "instruction_duration_fallback" else acquisition_duration,
                          "effective_duration_ns": None, "source": integration_source, "status": "pending"}
                pulse_event.meta["acquisition_window"] = window
                measurement = {"command": command, "start_ns": self.now, "duration_ns": acquisition_duration,
                               "integration_length_source": integration_source, "window": window,
                               "tb_id": seq.tb_id, "iq_id": seq.iq_id, "tb_valid": seq.tb_valid, "iq_shift": seq.iq_shift,
                               "tb_cfg": seq.tb_cfg, "tb_extra": seq.tb_extra}
                seq.acquisition = measurement
                self.schedule(self.now+acquisition_duration, 0, state.sequencer_id, "acquisition", measurement)
        self.schedule(end if queue_duration else self.now + 4, 2, state.sequencer_id, "rt")

    def acquisition_timing(self, seq: _Sequencer, command: _Command) -> tuple[int, str]:
        name = seq.state.sequencer_id
        physical = self.config["physical"].get("sequencers", {}).get(name, {})
        if command.instruction.op == "acquire":
            if "integration_length_ns" in physical:
                return physical["integration_length_ns"], "configured"
            hardware = seq.state.metadata.get("hardware_parameters", {})
            if "integration_length_acq" in hardware:
                return hardware["integration_length_acq"], "hardware_parameters"
        else:
            weights = command.args[2:4]
            if "input" in physical and self.find_acquisition_input(name, *command.args[:2]) is None:
                return self.signals.acquisition_duration(name, command.duration, weights), "weight_memory"
            entries = self.memories[name].get("weights", {})
            if isinstance(entries, Mapping):
                lengths = {entry["index"]: len(entry["data"]) for entry in entries.values()
                           if isinstance(entry, Mapping) and type(entry.get("index")) is int
                           and isinstance(entry.get("data"), list) and entry["data"]}
                if all(index in lengths for index in weights):
                    return max(lengths[index] for index in weights), "weight_memory"
        return command.duration, "instruction_duration_fallback"

    def find_acquisition_input(self, name: str, acquisition: int, bin_index: int) -> tuple[int, dict] | None:
        for index, item in enumerate(self.config.get("inputs", {}).get("acquisitions", [])):
            if (index not in self.used_acquisition_inputs and item["sequencer"] == name
                    and item["acquisition_index"] == acquisition and item["bin_index"] == bin_index):
                return index, item
        return None

    def start_qrm_scope(self, seq: _Sequencer, event: core.TimelineEvent) -> None:
        if seq.state.metadata.get("module_type") not in QRM_MODULE_TYPES:
            return
        key = qrm_scope_key(seq.state)
        paths = scope_trigger_paths(seq.state.metadata.get("sequencer_index"), self.qrm_scope_settings[key])
        if not paths:
            return
        previous = self.qrm_scope_latest.get(key)
        if previous is not None:
            owner, old_capture = previous
            old_window = old_capture["window"]
            completed = old_window["status"] == "completed"
            old_capture.update(overwritten_by_event_id=event.id, completed_before_overwrite=completed, sample_status="overwritten")
            if not completed:
                old_window.update(end_ns=self.now, effective_duration_ns=self.now - old_window["start_ns"],
                                  status="retriggered", interrupted_by_event_id=event.id)
                owner.signal_pending -= 1
            self.qrm_scope_buffers.pop(key, None)
        window = {"start_ns": self.now, "end_ns": self.now + QRM_SCOPE_SAMPLES,
                  "configured_duration_ns": QRM_SCOPE_SAMPLES, "effective_duration_ns": QRM_SCOPE_SAMPLES,
                  "source": "hardware_parameters", "status": "pending", "paths": paths}
        event.meta["scope_acquisition_window"] = window
        capture = {"acquire_event_id": event.id, "rt_packet_id": event.meta["rt_packet_id"], "window": window,
                   "sample_status": "pending"}
        seq.qrm_scope_captures.append(capture)
        self.qrm_scope_latest[key] = seq, capture
        seq.signal_pending += 1
        self.schedule(window["end_ns"], 0, seq.state.sequencer_id, "qrm_scope_end", capture)

    def finish_qrm_scope(self, seq: _Sequencer, capture: dict) -> None:
        window = capture["window"]
        window["status"] = "completed"
        start, end = window["start_ns"], window["end_ns"]
        if self.signals is None or type(start) is not int or type(end) is not int:
            capture.update(sample_status="unavailable", reason="Scope samples require modeled ADC input on a 1 ns grid.")
            return
        try:
            result = self.signals.scope_samples(seq.state.sequencer_id, start, end, window["paths"])
        except ValueError as error:
            capture.update(sample_status="unavailable", reason=str(error))
            return
        samples = result.pop("samples", None)
        capture.update(result)
        if samples is not None:
            self.qrm_scope_buffers[qrm_scope_key(seq.state)] = {"acquire_event_id": capture["acquire_event_id"], "samples": samples}

    def digital_output(self, seq: _Sequencer, command: _Command, source_event_id: str, level: int, delay_ticks: int) -> None:
        if level == seq.digital_output_level:
            return
        seq.digital_output_level = level
        packet = seq.state.rt_packets[command.packet_index]
        meta = {"level": level, "time_ticks": self.now_ticks, "fine_out_delay_ticks": delay_ticks,
                "model": "ideal-digital-v1", "hardware_parity": False,
                "rt_packet_id": packet.id, "source_event_id": source_event_id}
        if "digital" in command.setter_ids:
            meta["setter_event_id"] = command.setter_ids["digital"]
        event = self.emit(seq, "digital_output_transition", self.now, instruction=command.instruction,
                          meta=meta, lane="rt.output")
        seq.digital_output_transitions.append({**meta, "event_id": event.id})
        for route in self.digital_routes.get(seq.state.sequencer_id, []):
            target = route["target"]
            self.sequencers[target].signal_pending += 1
            arrival_ticks = self.now_ticks + route["delay_ticks"]
            self.schedule_ticks(arrival_ticks, 2.25, target, "digital_arrival",
                                {"time_ticks": arrival_ticks, "level": level, "delay_ticks": route["delay_ticks"],
                                 "source_sequencer_id": seq.state.sequencer_id,
                                 "source_event_id": event.id, "source_rt_packet_id": packet.id})

    def digital_bin_ready(self, seq: _Sequencer, command: _Command, acquisition: int, bin_index: int) -> bool:
        definitions = self.memories[seq.state.sequencer_id].get("acquisitions", {})
        if not isinstance(definitions, Mapping) or any(not isinstance(item, Mapping) or type(item.get("num_bins")) is not int
                or not 1 <= item["num_bins"] <= 2**24 for item in definitions.values()):
            self.fail(seq, "error", "Invalid digital acquisition memory definitions.", command.instruction)
            return False
        definition = next((item for item in definitions.values() if item.get("index") == acquisition), None)
        if definition is None:
            self.fail(seq, "blocked", f"Missing digital acquisition {acquisition} bin definition.", command.instruction)
            return False
        if not 0 <= bin_index < definition["num_bins"]:
            self.fail(seq, "error", f"Digital acquisition bin {bin_index} is out of bounds.", command.instruction)
            return False
        return True

    def record_digital(self, seq: _Sequencer, command: _Command, result: dict, *,
                       time_ns: int | float | None = None) -> tuple[dict, core.TimelineEvent]:
        time_ns = self.now if time_ns is None else time_ns
        name = seq.state.sequencer_id
        source = {"kind": "missing"}
        if name in self.digital_sources:
            source = {"kind": "digital_loopback", "sequencer": self.digital_sources[name]}
        elif seq.digital_level is not None:
            source = {"kind": "digital_trace", "sequencer": name}
        result = {"kind": command.instruction.op, "acquisition_index": command.args[0], "bin_index": command.args[1],
                  "model": "ideal-digital-v1", "hardware_parity": False,
                  "source": source,
                  "rt_packet_id": seq.state.rt_packets[command.packet_index].id,
                  "source_event_id": seq.state.rt_packets[command.packet_index].meta.get("rt_execution_event_id"), **result}
        event = self.emit(seq, "acquisition_result", time_ns, instruction=command.instruction, meta=result, lane="rt.acquire")
        result = {**result, "event_id": event.id, "time_ns": time_ns}
        seq.acquisitions.append(result)
        if result.get("status") in {"modeled", "supplied"}:
            key = result["kind"], result["acquisition_index"], result["bin_index"]
            previous = seq.digital_bins.get(key, {})
            bin_result = seq.digital_bins[key] = {**result}
            if result["kind"] == "acquire_ttl":
                bin_result["count"] = previous.get("count", 0) + result["count"]
                event.meta["bin_aggregate"] = result["bin_aggregate"] = {"kind": "ttl", "total_count": bin_result["count"]}
            elif result["kind"] == "acquire_timetags":
                avg_cnt = bin_result["avg_cnt"] = previous.get("avg_cnt", 0) + 1
                for field in ("count", "timetag_ticks"):
                    sum_field = f"{field}_sum"
                    total, value = previous.get(sum_field, 0), result.get(field)
                    if total is None or value is None:
                        bin_result[sum_field] = bin_result[field] = None
                        continue
                    total += value
                    whole, remainder = divmod(total, avg_cnt)
                    bin_result[sum_field] = total
                    bin_result[field] = total / avg_cnt if remainder else whole
                if bin_result["count"] is None:
                    bin_result["count_valid"] = False
                bin_result["valid"] = previous.get("valid", True) and result["valid"]
                fields = ("avg_cnt", "count", "count_sum", "timetag_ticks", "timetag_ticks_sum", "valid", "count_valid")
                aggregate = {field: bin_result[field] for field in fields if field in bin_result}
                event.meta["bin_aggregate"] = result["bin_aggregate"] = aggregate
        return result, event

    def find_digital_input(self, seq: _Sequencer, command: _Command, acquisition: int, bin_index: int) -> tuple[int, dict] | None:
        for index, item in enumerate(self.config["inputs"].get("digital_acquisitions", [])):
            if (index not in self.used_digital_inputs and item["sequencer"] == seq.state.sequencer_id
                    and item["kind"] == command.instruction.op and item["acquisition_index"] == acquisition and item["bin_index"] == bin_index):
                return index, item
        return None

    def supplied_digital(self, seq: _Sequencer, command: _Command, acquisition: int, bin_index: int) -> dict | None:
        supplied = self.find_digital_input(seq, command, acquisition, bin_index)
        if supplied is None:
            return None
        index, item = supplied
        self.used_digital_inputs.add(index)
        return {key: item[key] for key in ("count", "count_valid", "level", "timetag_ticks") if key in item} | {
            "status": "supplied", "source": {"kind": "digital_scenario", "input_index": index}}

    def sample_digital(self, seq: _Sequencer, command: _Command) -> None:
        acquisition, bin_index = command.args[:2]
        result = self.supplied_digital(seq, command, acquisition, bin_index)
        if result is None:
            result = ({"status": "modeled", "level": seq.digital_level} if seq.digital_level is not None else
                      {"status": "unavailable", "reason": "No digital input trace, loopback or supplied digital result."})
        input_ids = [seq.digital_input_event] if result["status"] == "modeled" and seq.digital_input_event else []
        result, event = self.record_digital(seq, command, {**result, "sample_ticks": self.now_ticks, "sampling": "ideal_at_rt_start",
                                                          "input_event_ids": input_ids})
        if "level" in result:
            mode = "sampled-high" if result["level"] else "sampled-low"
            self.forward_qtm_trigger(seq, command, mode, result["event_id"], input_ids)
            return
        settings = self.config["digital"].get(seq.state.sequencer_id, {})
        mode = settings.get("forward_trigger_mode")
        if settings.get("forward_trigger_en") and mode in {"sampled-high", "sampled-low"}:
            address = settings["forward_trigger_address"]
            self.route_trigger_request(seq, event, self.qtm_trigger_routes(seq, address), {
                "mode": mode, "source_event_ids": [event.id], "source_rt_packet_ids": [result["rt_packet_id"]],
                "thresholded_case": int(mode == "sampled-high"),
            }, preserve_fixed_arrival=True)

    def digital_command(self, seq: _Sequencer, command: _Command, event: core.TimelineEvent) -> None:
        op, args = command.op, command.args
        if op == "upd_thres":
            if args[0] not in {0, 1}:
                self.fail(seq, "blocked", f"Count threshold index {args[0]} is unsupported; only 0 (low) and 1 (high) are modeled.", command.instruction)
            else:
                seq.count_thresholds[args[0]] = args[1]
            return
        if op == "acquire_digital":
            if self.digital_bin_ready(seq, command, args[0], args[1]):
                # Same-time zero-delay outputs are only known after every RT commit.
                self.schedule_ticks(self.now_ticks, 2.75, seq.state.sequencer_id, "digital_sample", command)
            return
        if op == "acquire_ttl":
            self.digital_gate(seq, command, event)
            return
        boundary = self.now_ticks + args[3]
        if seq.timetag_boundary_ticks is not None and boundary <= seq.timetag_boundary_ticks:
            self.fail(seq, "blocked", "Timetag fine delays must produce strictly increasing effective boundaries.", command.instruction)
            return
        seq.timetag_boundary_ticks = boundary
        seq.signal_pending += 1
        captured = None
        if not args[2]:
            seq.pending_timetag_closes += 1
            captured = {"time_ref_ticks": seq.time_ref_ticks, "count_thresholds": dict(seq.count_thresholds),
                        "count_id": seq.qtm_streams.get("fb_llp_ttls_id", 0),
                        "delta_id": seq.qtm_streams.get("fb_tdc_tdelta_id", 0)}
        self.schedule_ticks(boundary, 2.125, seq.state.sequencer_id, "timetag_gate", (command, event, captured))

    def digital_gate(self, seq: _Sequencer, command: _Command, event: core.TimelineEvent, captured: dict | None = None) -> None:
        op, args = command.instruction.op, command.args
        attribute = "timetag_window" if op == "acquire_timetags" else "ttl_gate"
        window = getattr(seq, attribute)
        if args[2]:
            if window is not None:
                self.fail(seq, "blocked", "Retriggering an open digital acquisition gate is not modeled.", command.instruction)
                return
            if op == "acquire_ttl" and not self.digital_bin_ready(seq, command, args[0], args[1]):
                return
            observation = {"start_ticks": self.now_ticks, "end_ticks": None, "status": "open"}
            packet = seq.state.rt_packets[command.packet_index]
            packet.meta["digital_gate_window"] = event.meta["digital_gate_window"] = observation
            window = {"command": command, "start_ticks": self.now_ticks, "edges": [], "observation": observation}
            setattr(seq, attribute, window)
            self.open_trigger_edge_window(seq, window)
            if op == "acquire_timetags":
                self.start_qtm_scope(seq, command)
            return
        if window is None:
            self.fail(seq, "blocked", "Digital acquisition close has no open gate.", command.instruction)
            return
        window["observation"].update(end_ticks=self.now_ticks, status="closed")
        self.close_trigger_edge_window(window)
        setattr(seq, attribute, None)
        if op == "acquire_timetags" and (seq.qtm_scope_capture or {}).get("mode") == "timetags-windowed":
            self.end_qtm_scope(seq, "completed", command=command)
        target = command if op == "acquire_timetags" else window["command"]
        acquisition, bin_index = target.args[:2]
        if not self.digital_bin_ready(seq, command, acquisition, bin_index):
            return
        if op == "acquire_timetags":
            first = next(iter(window["edges"]), None)
            seq.last_timetag_window = self.now_ticks, first, seq.digital_level is not None
            # Resolve results after every same-tick close, independent of sequencer order.
            self.schedule_ticks(self.now_ticks, 2.375, seq.state.sequencer_id, "timetag_complete", (command, window, captured))
        else:
            self.finish_digital_gate(seq, command, window, captured)

    def open_trigger_edge_window(self, seq: _Sequencer, window: dict) -> None:
        if seq.digital_level is not None or (seq.rt_done and not seq.pending_timetag_closes):
            return
        command = window["command"]
        if command.op == "acquire_ttl":
            source_kind, mode = "ttl_edge", "rising"
            routes = [route for group in self.ttl_trigger_routes.get(seq.state.sequencer_id, {}).values() for route in group]
        else:
            source_kind = "qtm_edge"
            settings = self.config["digital"].get(seq.state.sequencer_id, {})
            mode = settings.get("forward_trigger_mode")
            if not settings.get("forward_trigger_en") or mode not in {"rising", "falling"}:
                return
            routes = self.qtm_trigger_routes(seq, settings["forward_trigger_address"])
        if not routes:
            return
        supplied = self.find_digital_input(seq, command, *command.args[:2]) if command.op == "acquire_ttl" else None
        count_limit = supplied[1]["count"] if supplied is not None else None
        if count_limit == 0:
            return
        packet = seq.state.rt_packets[command.packet_index]
        event = self.emit(seq, "trigger_edge_window", self.now, instruction=command.instruction, lane="debug.trigger_send", meta={
            "status": "input_unobserved", "source_kind": source_kind, "mode": mode,
            "start_ticks": self.now_ticks, "end_ticks": None, "source_rt_packet_ids": [packet.id],
        })
        if not seq.state.record_trace:
            seq.state.events.append(event)
        if supplied is not None:
            event.meta.update(count_upper_bound=count_limit, input_index=supplied[0])
        edge_window = window["trigger_edge_window"] = _TriggerEdgeWindow(event, self.now_ticks, count_limit=count_limit)
        for route in routes:
            if "network" in route:
                self.open_network_trigger_window(seq, edge_window, route)
            else:
                delay = route["latency_ns"] * 128
                interval = self.schedule_trigger_interval(
                    route["target"], route["address"], self.now_ticks + delay,
                    lambda delay=delay: None if edge_window.end_ticks is None else edge_window.end_ticks + delay,
                    event.id, after_rt=delay == 0, count_limit=count_limit,
                )
                if count_limit is not None:
                    edge_window.fixed_arrivals.append((route["target"], interval))

    def close_trigger_edge_window(self, window: dict | None, *, end_ticks: int | None = None) -> None:
        edge_window = (window or {}).get("trigger_edge_window")
        if edge_window is not None and edge_window.end_ticks is None:
            edge_window.end_ticks = self.now_ticks if end_ticks is None else end_ticks
            edge_window.event.meta["end_ticks"] = edge_window.end_ticks

    def close_failed_trigger_edge_windows(self, seq: _Sequencer) -> None:
        # A late failure does not remove the edge opportunity on its own tick.
        end_ticks = self.now_ticks + (self.now_phase >= _DIGITAL_EDGE_PHASE)
        self.close_trigger_edge_window(seq.ttl_gate, end_ticks=end_ticks)
        self.close_trigger_edge_window(seq.timetag_window, end_ticks=end_ticks)

    def finish_digital_gate(self, seq: _Sequencer, command: _Command, window: dict, captured: dict | None) -> None:
        op = command.instruction.op
        target = command if op == "acquire_timetags" else window["command"]
        acquisition, bin_index = target.args[:2]
        settings = self.config["digital"].get(seq.state.sequencer_id, {})
        edges = window["edges"]
        result = self.supplied_digital(seq, command, acquisition, bin_index)
        if result is None:
            result = ({"status": "modeled", "count": len(edges)} if seq.digital_level is not None else
                      {"status": "unavailable", "reason": "No digital input trace or supplied digital result."})
        result.update(acquisition_index=acquisition, bin_index=bin_index, start_ticks=window["start_ticks"], end_ticks=self.now_ticks,
                      window="[open, close)", edge_times_ticks=[edge["time_ticks"] for edge in edges],
                      input_event_ids=[edge["event_id"] for edge in edges],
                      open_packet_id=seq.state.rt_packets[window["command"].packet_index].id)
        if op == "acquire_timetags":
            assert captured is not None
            self.select_timetag(seq, window, captured, settings, result)
            result.update(open_delay_ticks=window["command"].args[3], close_delay_ticks=command.args[3],
                          fine_delay_model="nominal_ticks")
            low, high = captured["count_thresholds"].get(0), captured["count_thresholds"].get(1)
            count = result.get("count")
            if result.get("count_valid") is False:
                result.update(classification="invalid", classification_reason="The supplied count result is invalid.")
            elif count is not None and low is not None and high is not None and low <= high:
                result["classification"] = "low" if count < low else "high" if count >= high else "mid"
                result["count_thresholds"] = {"low": low, "high": high}
            else:
                result.update(classification=None, classification_reason="Missing or unsupported count threshold configuration.")
        elif settings.get("ttl_acq_auto_bin_incr_en", False) and result["status"] == "modeled" and edges:
            for index, edge in enumerate(edges):
                if not self.digital_bin_ready(seq, command, acquisition, bin_index + index):
                    return
                self.record_digital(seq, command, {**result, "bin_index": bin_index + index, "count": 1,
                                                 "edge_times_ticks": [edge["time_ticks"]], "input_event_ids": [edge["event_id"]]})
            return
        elif op == "acquire_ttl" and settings.get("ttl_acq_auto_bin_incr_en", False) and result["status"] == "supplied":
            result.update(status="unavailable", reason="An aggregate supplied TTL count cannot locate individual incremented bins.")
        result, event = self.record_digital(seq, command, result)
        edge_window = window.get("trigger_edge_window")
        if op == "acquire_ttl" and result["status"] == "supplied" and edge_window is not None:
            for target, interval in edge_window.fixed_arrivals:
                end = interval.end_ticks()
                assert end is not None
                phase = _POST_RT_POSSIBLE_TRIGGER_PHASE if interval.after_rt else 0.5
                self.schedule_ticks(end, phase, target, "trigger_interval_complete", interval)
        if captured is not None:
            self.collect_qtm_truth_result(seq, command, settings, result, event)
            self.send_timetag_result(seq, command, captured, result)
            if seq.failed:
                self.link_retained_event(seq.state, event.id)
                self.close_rt_refusal(seq)

    def append_qtm_scope_entry(self, seq: _Sequencer, kind: str, source_event_id: str, rt_packet_id: str) -> None:
        if len(seq.qtm_scope_entries) == _QTM_SCOPE_CAPACITY:
            seq.qtm_scope_dropped += 1
            return
        seq.qtm_scope_entries.append({"kind": kind, "time_ticks": self.now_ticks, "source_event_id": source_event_id,
                                      "rt_packet_id": rt_packet_id})
        if len(seq.qtm_scope_entries) == _QTM_SCOPE_CAPACITY and (seq.qtm_scope_capture or {}).get("mode") == "timetags":
            self.end_qtm_scope(seq, "capacity")

    def start_qtm_scope(self, seq: _Sequencer, command: _Command) -> None:
        settings = self.config["digital"].get(seq.state.sequencer_id, {})
        mode = settings.get("scope_mode")
        if (mode is None or seq.qtm_scope_capture is not None or not command.settings.get("scope_enable")
                or not seq.rt_settings.get("scope_enable")):
            return
        if mode == "timetags" and len(seq.qtm_scope_entries) == _QTM_SCOPE_CAPACITY:
            return
        packet = seq.state.rt_packets[command.packet_index]
        capture = {"mode": mode, "start_ticks": self.now_ticks, "open_packet_id": packet.id,
                   "open_event_id": packet.meta["rt_execution_event_id"], "status": "active"}
        if "scope_enable" in command.setter_ids:
            capture["setter_event_id"] = command.setter_ids["scope_enable"]
        seq.qtm_scope_captures.append(capture)
        if mode == "timetags":
            horizon = settings["scope_end_ticks"]
            if horizon <= self.now_ticks:
                capture.update(status="unavailable", reason="Scope horizon must follow the effective open time.")
                return
            seq.signal_pending += 1
            self.schedule_ticks(horizon, 2.125, seq.state.sequencer_id, "qtm_scope_horizon", capture)
        else:
            self.append_qtm_scope_entry(seq, "OPEN", packet.meta["rt_execution_event_id"], packet.id)
        seq.qtm_scope_capture = capture

    def end_qtm_scope(self, seq: _Sequencer, status: str, *, command: _Command | None = None,
                      end_ticks: int | None = None) -> None:
        capture = seq.qtm_scope_capture
        if capture is None:
            return
        capture.update(status=status, end_ticks=self.now_ticks if end_ticks is None else end_ticks,
                       input_status="known" if seq.digital_level is not None else "unavailable")
        if capture["mode"] == "timetags":
            seq.signal_pending -= 1
        if command is not None:
            packet = seq.state.rt_packets[command.packet_index]
            if status == "completed":
                self.append_qtm_scope_entry(seq, "CLOSE", packet.meta["rt_execution_event_id"], packet.id)
                capture.update(close_packet_id=packet.id, close_event_id=packet.meta["rt_execution_event_id"])
            elif status == "disabled":
                capture.update(disable_packet_id=packet.id, disable_event_id=packet.meta["rt_execution_event_id"])
        seq.qtm_scope_capture = None

    def timetag_reference(self, seq: _Sequencer, window: dict, captured: dict, settings: dict) -> tuple[int | None, str | None, str | None]:
        reference = settings.get("time_reference", "start")
        if reference == "start":
            return window["start_ticks"], None, None
        if reference == "end":
            return self.now_ticks, None, None
        if reference == "sequencer":
            time = captured["time_ref_ticks"]
            return time, None, "Sequencer time reference has not been applied." if time is None else None
        name = self.timetag_channels.get((settings["module"], int(reference[-1])))
        if name is None:
            return None, None, "The time-reference channel is not configured on this module."
        previous = self.sequencers[name].last_timetag_window
        if previous is None:
            return None, None, "The time-reference channel has no closed window."
        close_ticks, first, input_known = previous
        if name != seq.state.sequencer_id and close_ticks == self.now_ticks:
            return None, None, "Cross-channel windows closed at the same tick. Reference ordering is unverified."
        if not input_known:
            return None, None, "The time-reference window has no digital input evidence."
        return (first["time_ticks"], first["event_id"], None) if first is not None else (None, None, None)

    def select_timetag(self, seq: _Sequencer, window: dict, captured: dict, settings: dict, result: dict) -> None:
        time_source = settings.get("time_source", "first")
        edges = window["edges"]
        index = {"first": 0, "second": 1, "last": len(edges) - 1}[time_source]
        selected = edges[index] if 0 <= index < len(edges) else None
        reference, reference_event_id, reference_reason = self.timetag_reference(seq, window, captured, settings)
        result.update(reference_ticks=reference, time_reference=settings.get("time_reference", "start"), time_source=time_source)
        if reference_event_id is not None:
            result["reference_event_id"] = reference_event_id
        observed_invalid = False
        if result["status"] == "modeled":
            result["timetag_ticks"] = selected["time_ticks"] - reference if selected is not None and reference is not None else None
            if selected is None:
                result["timetag_reason"] = f"The window does not contain the {time_source} input event."
                observed_invalid = True
            else:
                result["time_source_event_id"] = selected["event_id"]
                if reference is None:
                    result["timetag_reason"] = reference_reason or "The reference window does not contain a first input event."
                    observed_invalid = reference_reason is None
        elif result["status"] == "supplied" and result.get("timetag_ticks") is None:
            result["timetag_reason"] = "The supplied result has no valid time delta."
            observed_invalid = True
        result["valid"] = result.get("timetag_ticks") is not None
        policy = settings.get("invalid_time_delta")
        if observed_invalid and policy is not None:
            result["invalid_time_delta"] = policy
            if policy == "record_0":
                result["timetag_ticks"] = 0
            elif policy == "discard":
                result["status"] = "discarded"
            else:
                result["status"] = "invalid"

    def send_timetag_result(self, seq: _Sequencer, command: _Command, captured: dict, result: dict) -> None:
        if result.get("status") == "invalid":
            self.fail(seq, "error", result["timetag_reason"], command.instruction, category="invalid_time_delta")
            return
        count_id, delta_id = captured["count_id"], captured["delta_id"]
        if (count_id or delta_id) and self.now_ticks % 128:
            self.fail(seq, "blocked", "Fractional QTM LINQ window-close emission requires a receiver scheduling quantization vector.",
                      command.instruction)
            return
        packet = seq.state.rt_packets[command.packet_index]
        if count_id:
            value = result.get("count")
            if value is None:
                self.fail(seq, "blocked", "fb_llp_ttls_id has no count result; invalid-result wire policy is unverified.", command.instruction)
                return
            self.send(seq, count_id, value & 0xFFFFFFFF, command.instruction, result["event_id"], source_packet=packet,
                      payload_meta={"stream": "fb_llp_ttls_id", "payload_units": "count"})
        if delta_id:
            if not result["valid"]:
                self.fail(seq, "blocked", "Invalid QTM time-delta LINQ wire behavior is unverified. "
                          + result.get("timetag_reason", result.get("reason", "No valid time delta.")), command.instruction)
                return
            delta = result["timetag_ticks"]
            if not -(2**31) <= delta < 2**31:
                self.fail(seq, "error", "QTM LINQ time delta is outside signed 32-bit range.", command.instruction)
                return
            self.send(seq, delta_id, delta & 0xFFFFFFFF, command.instruction, result["event_id"], source_packet=packet,
                      payload_meta={"stream": "fb_tdc_tdelta_id", "encoded_time_ticks": delta, "payload_units": "1/128 ns"})

    def digital_edge(self, seq: _Sequencer, data: dict) -> None:
        if seq.timetag_window is not None and (not seq.rt_done or seq.pending_timetag_closes):
            mode = "rising" if data["level"] else "falling"
            self.forward_qtm_trigger(seq, seq.timetag_window["command"], mode, data["source_event_id"])
        if not data["level"]:
            return
        scope = seq.qtm_scope_capture
        if scope is not None and (scope["mode"] == "timetags" or not seq.rt_done or seq.pending_timetag_closes):
            self.append_qtm_scope_entry(seq, "RISE", data["source_event_id"], scope["open_packet_id"])
        for window in (seq.timetag_window, seq.ttl_gate):
            if window is None or (seq.rt_done and (window is seq.ttl_gate or not seq.pending_timetag_closes)):
                continue
            command = window["command"]
            event = self.emit(seq, "digital_edge", self.now, instruction=command.instruction,
                              meta={**data, "rt_packet_id": seq.state.rt_packets[command.packet_index].id}, lane="rt.input")
            window["edges"].append({**data, "event_id": event.id})
            if window is not seq.timetag_window:
                settings = self.config["digital"].get(seq.state.sequencer_id, {})
                acquisition, bin_index = command.args[:2]
                if settings.get("ttl_acq_auto_bin_incr_en", False):
                    bin_index += len(window["edges"]) - 1
                    if not self.digital_bin_ready(seq, command, acquisition, bin_index):
                        return
                for address, routes in self.ttl_trigger_routes.get(seq.state.sequencer_id, {}).items():
                    sent = self.emit_generated_trigger(seq, command, address, routes, {
                        "source_kind": "ttl_edge", "acquisition_index": acquisition, "bin_index": bin_index,
                        "source_event_ids": [event.id], "source_rt_packet_ids": [event.meta["rt_packet_id"]],
                    })
                    seq.ttl_trigger_transmissions.append({**sent.meta, "event_id": sent.id})
                continue
            for stream in ("fb_llp_tags_id", "fb_tdc_tags_id"):
                identifier = seq.qtm_streams.get(stream, 0)
                if not identifier:
                    continue
                origin = self.config["digital"].get(seq.state.sequencer_id, {}).get("linq_timestamp_origin_ticks")
                if origin is None:
                    reason = "QTM absolute LINQ timetags require an explicit linq_timestamp_origin_ticks."
                else:
                    encoded = data["time_ticks"] + origin
                    if not (self.now_ticks % 128 or stream == "fb_llp_tags_id" and encoded % 128):
                        value = encoded // 128 if stream == "fb_llp_tags_id" else encoded
                        self.send(seq, identifier, value & 0xFFFFFFFF, command.instruction, event.id,
                                  source_packet=seq.state.rt_packets[command.packet_index],
                                  payload_meta={"stream": stream, "encoded_time_ticks": encoded,
                                                "payload_units": "ns" if stream == "fb_llp_tags_id" else "1/128 ns"})
                        continue
                    reason = "Fractional QTM LINQ emission/LLP rounding requires a scheduling quantization vector."
                self.fail(seq, "blocked", reason, command.instruction)
                self.link_retained_event(seq.state, event.id)
                self.close_rt_refusal(seq)
                return

    def send(self, seq: _Sequencer, identifier: int, value: int, instruction: Instr, source_event_id: str | None = None,
             *, source_packet: core.RtPacket | None = None, tb_cfg: tuple | None = None, payload_meta: dict | None = None) -> None:
        if identifier == 0:
            return
        routes = [route for route in self.config.get("routes", []) if route["source"] == seq.state.sequencer_id and route.get("id", identifier) == identifier]
        meta = {"id": identifier, "value": value,
                "targets": [route["target"] for route in routes], "source_event_id": source_event_id, **(payload_meta or {})}
        if source_packet is not None:
            meta.update(q1_issue_event_id=source_packet.meta["q1_issue_event_id"], source_packet_id=source_packet.id)
        combined = tb_cfg is not None and tb_cfg[0]
        event = self.emit(seq, "feedback_contribution" if combined else "feedback_send", self.now, instruction=instruction, meta=meta, lane="rt.feedback")
        for route in routes:
            if combined:
                key = (self.now_ticks, route["target"], identifier)
                if key not in self.tb_groups:
                    self.tb_groups[key] = []
                    # A same-tick RT acquisition can finish a previous integration.
                    self.schedule_ticks(self.now_ticks, 2.125, seq.state.sequencer_id, "tb_combine", key)
                self.tb_groups[key].append({"sequencer": seq.state.sequencer_id, "source_event_id": event.id,
                                           "acquisition_event_id": source_event_id,
                                           "rt_packet_id": source_packet.id if source_packet else None,
                                           "instruction": instruction, "value": value, "bit_position": tb_cfg[1],
                                           "length": tb_cfg[2], "latency_ns": route["latency_ns"]})
                continue
            self.schedule(self.now+route.get("latency_ns", 0), 0, route["target"], "feedback",
                          {"id": identifier, "value": value, "source_event_id": event.id})

    def combine_tb(self, key: tuple) -> None:
        contributions = self.tb_groups.pop(key)
        completion_ticks, target, identifier = key
        word, occupied = 0, 0
        first = contributions[0]
        for item in contributions:
            mask = 3 << item["bit_position"]
            if occupied & mask or item["length"] != first["length"] or item["latency_ns"] != first["latency_ns"]:
                self.reject_tb_round(target, contributions)
                return
            word |= item["value"] << item["bit_position"]
            occupied |= mask
        provenance = [{field: value for field, value in item.items() if field != "instruction"} for item in contributions]
        source = self.sequencers[first["sequencer"]]
        meta = {"id": identifier, "value": word, "length_bytes": first["length"], "contributors": provenance,
                "source_event_ids": [item["source_event_id"] for item in contributions], "targets": [target], "write_combine": True}
        event = self.emit(source, "feedback_send", self.now, instruction=first["instruction"], meta=meta, lane="rt.feedback")
        latency = first["latency_ns"]
        self.schedule_ticks(completion_ticks + latency * 128, 0 if latency else 2.25, target, "feedback",
                            {**meta, "source_event_id": event.id})

    def reject_tb_round(self, target: str, contributions: list[dict]) -> None:
        reason = "TB write-combine contributors have colliding bit positions, incompatible widths or different route latencies."
        sources = {}
        evidence = []
        for item in sorted(contributions, key=lambda item: item["sequencer"]):
            name = item["sequencer"]
            sources.setdefault(name, item["instruction"])
            event_id = item["source_event_id"]
            if any(event.id == event_id for event in self.sequencers[name].state.events):
                evidence.append(event_id)
        for name in sorted(sources.keys() | {target}):
            seq = self.sequencers[name]
            if seq.failed:
                continue
            state = seq.state
            packet = self.clip_active_rt_packet(state)
            instruction = sources.get(name)
            if instruction is None and packet is not None:
                instruction = next((item for item in state.instructions_by_pc.values() if item.source == packet.source), None)
            self.fail(seq, "error", reason, instruction, category="feedback_write_combine", link_pending_rt_wait=True)
            diagnostic = state.diagnostics[-1]
            related = list(dict.fromkeys([*evidence, *diagnostic.related_events]))
            state.diagnostics[-1] = replace(diagnostic, related_events=related)
            if packet is not None:
                self.link_retained_event(state, packet.meta.get("rt_execution_event_id"))

    def feedback(self, seq: _Sequencer, data: dict) -> None:
        source = SourceLocation("<simulation>", 1, 1, "")
        depth = len(seq.feedback)
        if depth >= self.feedback_capacity:
            event = self.emit(seq, "feedback_rejected", self.now, source=source, lane="rt.feedback",
                              meta={**data, "receiver_sequencer_id": seq.state.sequencer_id,
                                    "depth": depth, "capacity": self.feedback_capacity, "reason": "queue_full"})
            # Keep the diagnostic anchor even when detailed tracing is disabled.
            if not seq.state.record_trace:
                seq.state.events.append(event)
            self.clip_active_rt_packet(seq.state)
            self.fail(seq, "error", f"Feedback queue overflow at {self.now} ns.", category="feedback_queue_overflow")
            diagnostic = seq.state.diagnostics[-1]
            seq.state.diagnostics[-1] = replace(
                diagnostic, source=source, related_events=[event.id],
                details={**diagnostic.details, "display_message": f"LINQ feedback queue overflow (capacity {self.feedback_capacity})."},
            )
            return
        event = self.emit(seq, "feedback_receive", self.now, source=source,
                          meta={**data, "depth": depth + 1}, lane="rt.feedback")
        seq.feedback.append({**data, "event_id": event.id})
        if seq.waiting == "feedback_input":
            self.resume_q1(seq)

    def forward_qtm_trigger(self, seq: _Sequencer, command: _Command, mode: str, source_event_id: str,
                            input_event_ids: list[str] | None = None) -> None:
        settings = self.config["digital"].get(seq.state.sequencer_id, {})
        if settings.get("forward_trigger_en") and settings["forward_trigger_mode"] == mode:
            packet = seq.state.rt_packets[command.packet_index]
            self.send_qtm_trigger(seq, command, settings["forward_trigger_address"], {
                "mode": mode, "source_event_ids": [source_event_id], "source_rt_packet_ids": [packet.id],
                "input_event_ids": input_event_ids or [],
            })

    def collect_qtm_truth_result(self, seq: _Sequencer, command: _Command, settings: dict, result: dict,
                                 event: core.TimelineEvent) -> None:
        module, channel = settings.get("module"), settings.get("channel")
        if channel not in self.qtm_truth_channels.get(module, ()):
            return
        key = module, self.now_ticks
        contributions = self.qtm_truth_pending.setdefault(key, {})
        if not contributions:
            self.schedule_ticks(self.now_ticks, 2.625, seq.state.sequencer_id, "qtm_truth_round", key)
        contributions[channel] = seq, command, result, event

    def finish_qtm_truth_round(self, key: tuple[str, int]) -> None:
        module, ticks = key
        contributions = self.qtm_truth_pending.pop(key)
        inputs = {channel: {"sequencer": seq.state.sequencer_id, "classification": result["classification"],
                            "event_id": result["event_id"], "rt_packet_id": result["rt_packet_id"]}
                  for channel, (seq, _, result, _) in sorted(contributions.items())}
        unavailable = {channel for channel, result in inputs.items() if result["classification"] is None}
        classification_count = 4  # Low, mid, high and invalid.
        assignment_count = classification_count ** len(unavailable)
        decisions = []
        possible_channels: dict[int, set[int]] = {}
        covered_assignments: dict[int, int] = {}
        for rule in self.config["trigger_truth_tables"][module]:
            required = sorted(rule["when"])
            decision = {"address": rule["address"], "when": rule["when"], "status": "no_match"}
            missing = sorted(set(required) - inputs.keys())
            unavailable_required = unavailable.intersection(required)
            if missing:
                decision.update(status="incomplete_round", missing_channels=missing,
                                reason="Required channels must close at the same effective tick.")
            elif unavailable_required:
                decision.update(status="unavailable", reason="A required count classification is unavailable.")
                if all(inputs[channel]["classification"] in (None, rule["when"][channel]) for channel in required):
                    address = rule["address"]
                    possible_channels.setdefault(address, set()).update(required)
                    # Configuration rejects overlapping rules. Each compatible rule fixes
                    # its unavailable inputs and covers all choices of the other inputs.
                    covered_assignments[address] = covered_assignments.get(address, 0) + (
                        classification_count ** (len(unavailable) - len(unavailable_required))
                    )
                    decision["trigger_possible"] = True
            elif all(inputs[channel]["classification"] == rule["when"][channel] for channel in required):
                seq, command, _, _ = contributions[required[0]]
                event = self.send_qtm_trigger(seq, command, rule["address"], {
                    "mode": "truth_table", "source_module": module, "source_channels": required,
                    "source_event_ids": [inputs[channel]["event_id"] for channel in required],
                    "source_rt_packet_ids": [inputs[channel]["rt_packet_id"] for channel in required],
                })
                decision.update(status="matched", trigger_event_id=event.id)
            decisions.append(decision)
        output_cases = tuple(sorted(covered_assignments))
        if sum(covered_assignments.values()) < assignment_count:
            output_cases += (None,)
        proven_outputs = []
        for address, channels in possible_channels.items():
            required = sorted(channels)
            seq, command, _, source = contributions[required[0]]
            provenance = {
                "mode": "truth_table", "source_module": module, "source_channels": required,
                "source_event_ids": [inputs[channel]["event_id"] for channel in required],
                "source_rt_packet_ids": [inputs[channel]["rt_packet_id"] for channel in required],
            }
            if covered_assignments[address] == assignment_count:
                proof = {"unavailable_channels": sorted(unavailable), "assignment_count": assignment_count}
                event = self.send_qtm_trigger(seq, command, address, {**provenance, "truth_round_proof": proof})
                proven_outputs.append({"address": address, "source_channels": required, **proof, "trigger_event_id": event.id})
            else:
                self.route_trigger_request(seq, source, self.qtm_trigger_routes(seq, address, module), {
                    **provenance, "truth_round_id": f"{module}:{ticks}", "truth_round_outputs": list(output_cases),
                }, preserve_fixed_arrival=True)
        owner = self.timetag_channels[(module, min(self.qtm_truth_channels[module]))]
        round_ = {"module": module, "time_ticks": ticks, "inputs": inputs, "rules": decisions}
        if proven_outputs:
            round_["proven_outputs"] = proven_outputs
        elif possible_channels:
            round_["possible_outputs"] = list(output_cases)
        self.sequencers[owner].qtm_trigger_rounds.append(round_)

    def qtm_trigger_routes(self, seq: _Sequencer, address: int, module: str | None = None) -> list[dict]:
        source_key, source_value = ("source_module", module) if module else ("source", seq.state.sequencer_id)
        return [route for route in self.config["trigger_routes"]
                if "source_kind" not in route and route.get(source_key) == source_value and route["address"] == address]

    def send_qtm_trigger(self, seq: _Sequencer, command: _Command, address: int, provenance: dict) -> core.TimelineEvent:
        routes = self.qtm_trigger_routes(seq, address, provenance.get("source_module"))
        event = self.emit_generated_trigger(seq, command, address, routes, provenance)
        seq.qtm_trigger_transmissions.append({**event.meta, "event_id": event.id})
        return event

    def emit_generated_trigger(self, seq: _Sequencer, command: _Command, address: int, routes: list[dict],
                               provenance: dict) -> core.TimelineEvent:
        meta = {**provenance, "address": address, "time_ticks": self.now_ticks,
                "source_sequencer_id": seq.state.sequencer_id, "targets": [route["target"] for route in routes]}
        event = self.emit(seq, "trigger_send", self.now, instruction=command.instruction, meta=meta, lane="debug.trigger_send")
        self.route_trigger_request(seq, event, routes, provenance, preserve_fixed_arrival=True)
        return event

    def route_trigger_request(self, seq: _Sequencer, source: core.TimelineEvent, routes: list[dict],
                              provenance: dict, *, preserve_fixed_arrival: bool = False,
                              zero_latency_after_rt: bool = False) -> None:
        by_network: dict[tuple[str, int], list[dict]] = {}
        thresholded = provenance.get("thresholded_case")
        truth_round_id = provenance.get("truth_round_id")
        truth_round = (truth_round_id, tuple(provenance["truth_round_outputs"])) if truth_round_id is not None else None
        threshold_case = (source.id, thresholded) if thresholded is not None else None
        for route in routes:
            address = route["address"]
            if "network" in route:
                by_network.setdefault((route["network"], address), []).append(route)
                continue
            target, latency = route["target"], route["latency_ns"]
            ticks = self.now_ticks + latency * 128
            after_rt = latency == 0 and (zero_latency_after_rt or preserve_fixed_arrival)
            phase = _POST_RT_TRIGGER_PHASE if after_rt else 0
            if threshold_case is not None or truth_round is not None:
                possible_phase = _POST_RT_POSSIBLE_TRIGGER_PHASE if after_rt else 0.5
                self.schedule_ticks(ticks, possible_phase, target, "trigger_uncertain", _PossibleTriggerArrival(
                    address, ticks, threshold_case=threshold_case, guaranteed_in_case=True,
                    truth_round=truth_round, source_event_ids=(source.id, *provenance["source_event_ids"]),
                    source_rt_packet_ids=tuple(provenance["source_rt_packet_ids"]),
                ))
            elif preserve_fixed_arrival:
                self.sequencers[target].signal_pending += 1
                self.schedule_ticks(ticks, phase, target, "generated_trigger_arrival", {
                    **provenance, "address": address, "source_event_id": source.id, "source_sequencer_id": seq.state.sequencer_id,
                    "time_ticks": ticks, "latency_ns": latency,
                })
            else:
                self.schedule_ticks(ticks, phase, target, "triggers", {"address": address, "source_event_id": source.id})
        for (network, address), group in by_network.items():
            self.request_network_trigger(seq, source, address, network, group, provenance)

    def network_epoch_ticks(self, network: str) -> int | None:
        settings = self.config["trigger_networks"][network]
        epoch = settings.get("grid_epoch_ns")
        return epoch * 128 if epoch is not None else self.synq_epoch_ticks if settings.get("grid_epoch") == "synq" else None

    def network_windows_at(self, network: str, ticks: int) -> dict[str, _NetworkEdgeWindow]:
        windows = self.network_edge_windows[network]
        for event_id, window in list(windows.items()):
            first, last = window.launch_bounds()
            if last is not None and (last < first or ticks - last >= _TRIGGER_SPACING_TICKS):
                del windows[event_id]
        return windows

    def open_network_trigger_window(self, seq: _Sequencer, window: _TriggerEdgeWindow, route: dict) -> None:
        network = route["network"]
        windows = self.network_windows_at(network, self.now_ticks)
        edge = windows.get(window.event.id)
        if edge is None:
            edge = windows[window.event.id] = _NetworkEdgeWindow(window, self.network_epoch_ticks(network))
            if edge.epoch_ticks is None:
                seq.state.diagnostics.append(Diagnostic(
                    severity="warning", category="trigger_network_unknown_epoch", source=window.event.source,
                    message=f"Trigger network {network!r} has no resolved grid epoch at this edge window.",
                    related_events=[window.event.id], details={"network": network, "request_ticks": window.start_ticks},
                ))
            for request in self.recent_trigger_launches[network]:
                self.mark_trigger_window_conflict(request, edge)
        first, _ = edge.launch_bounds()
        delay = _TRIGGER_PROPAGATION_TICKS + route["extra_latency_ns"] * 128

        def arrival_end() -> int | None:
            _, last = edge.launch_bounds()
            return None if last is None else last + delay + 1

        self.schedule_trigger_interval(route["target"], route["address"], first + delay, arrival_end,
                                       window.event.id, count_limit=window.count_limit)

    def request_network_trigger(self, seq: _Sequencer, source: core.TimelineEvent, address: int, network: str,
                                routes: list[dict], provenance: dict) -> None:
        settings = self.config["trigger_networks"][network]
        epoch_ticks = self.network_epoch_ticks(network)
        event = self.emit(seq, "trigger_network_request", self.now, source=source.source, lane="debug.trigger_send", meta={
            **provenance, "network": network, "address": address, "source_event_id": source.id, "time_ticks": self.now_ticks,
        })
        record = {"network": network, "address": address, "request_event_id": event.id, "source_event_id": source.id,
                  "request_ticks": self.now_ticks, "grid_epoch_ticks": epoch_ticks,
                  "grid_epoch_source": "explicit" if "grid_epoch_ns" in settings else settings.get("grid_epoch"),
                  "status": "pending" if epoch_ticks is not None else "unknown_epoch",
                  "routes": [{"target": route["target"], "extra_latency_ns": route["extra_latency_ns"], "status": "pending"}
                             for route in routes]}
        thresholded = provenance.get("thresholded_case")
        threshold_case = (source.id, thresholded) if thresholded is not None else None
        truth_round_id = provenance.get("truth_round_id")
        if threshold_case is not None:
            record["thresholded_case"] = thresholded
        if truth_round_id is not None:
            record["truth_round_id"] = truth_round_id
            record["truth_round_outputs"] = provenance["truth_round_outputs"]
        seq.trigger_network_transmissions.append(record)
        request = _NetworkRequest(seq, event, provenance, record, threshold_case=threshold_case, truth_round_id=truth_round_id)
        if epoch_ticks is None:
            self.unresolved_launches[network].append(request)
            self.add_network_diagnostic(seq, Diagnostic(
                severity="warning", category="trigger_network_unknown_epoch", source=source.source,
                message=f"Trigger network {network!r} has no resolved grid epoch at this request.",
                related_events=[event.id, source.id], details={"network": network, "request_ticks": self.now_ticks},
            ), request)
            for route in record["routes"]:
                route["status"] = "unknown_epoch"
                self.schedule_network_delivery(request, route, self.now_ticks, uncertain=True)
            return
        launch_ticks = _network_launch_tick(self.now_ticks, epoch_ticks)
        record["launch_ticks"] = launch_ticks
        key = network, launch_ticks
        group = self.trigger_launches.setdefault(key, [])
        if not group:
            # All same-tick acquisition, digital edge, truth-table and sample requests must exist first.
            self.schedule_ticks(launch_ticks, 2.9375, seq.state.sequencer_id, "trigger_network_launch", key)
        group.append(request)

    def launch_trigger_requests(self, key: tuple[str, int]) -> None:
        network, launch_ticks = key
        batch = self.trigger_launches.pop(key)
        recent = self.recent_trigger_launches[network]
        while recent and launch_ticks - recent[0].record["launch_ticks"] >= _TRIGGER_SPACING_TICKS:
            recent.popleft()
        unresolved = self.unresolved_launches[network]
        while unresolved and launch_ticks - (unresolved[0].record["request_ticks"] + _TRIGGER_GRID_TICKS - 1) >= _TRIGGER_SPACING_TICKS:
            unresolved.popleft()
        if unresolved:
            for request in batch:
                self.mark_trigger_conflict(request, unresolved[-1])
        if any(request.optional for request in (*recent, *batch)):
            for request in batch:
                for previous in recent:
                    self.mark_trigger_conflict(request, previous)
                    self.mark_trigger_conflict(previous, request)
                recent.append(request)
        else:
            if len(batch) > 1:
                for index, request in enumerate(batch):
                    self.mark_trigger_conflict(request, batch[0 if index else 1])
            if recent:
                for request in batch:
                    self.mark_trigger_conflict(request, recent[-1])
                for previous in recent:
                    self.mark_trigger_conflict(previous, batch[0])
            recent.extend(batch)
        windows = self.network_windows_at(network, launch_ticks)
        for request in batch:
            for window in windows.values():
                self.mark_trigger_window_conflict(request, window)
            record = request.record
            if record["status"] == "pending" and request.optional:
                record["status"] = "optional"
            elif record["status"] == "pending":
                request.launch = self.emit(request.seq, "trigger_network_launch", self.now, source=request.event.source,
                                           lane="debug.trigger_send", meta={
                    **request.provenance, "network": network, "address": record["address"], "time_ticks": launch_ticks,
                    "source_event_id": request.event.id, "request_event_id": request.event.id,
                    "request_ticks": record["request_ticks"], "status": "launched",
                })
                record.update(status="launched", launch_event_id=request.launch.id)
            for route in record["routes"]:
                route["status"] = {"conflicted": "unknown_conflict", "possible_conflict": "unknown_possible_conflict",
                                   "optional": "scheduled_optional"}.get(record["status"], "scheduled")
                self.schedule_network_delivery(request, route, launch_ticks, uncertain=route["status"] != "scheduled")

    def mark_trigger_conflict(self, request: _NetworkRequest, other: _NetworkRequest) -> None:
        record = request.record
        case, other_case = request.threshold_case, other.threshold_case
        if case is not None and other_case is not None and case[0] == other_case[0] and case[1] != other_case[1]:
            return
        if request.truth_round_id is not None and request.truth_round_id == other.truth_round_id:
            return
        other_ticks = other.record.get("launch_ticks")
        possible = request.optional or other.optional or other_ticks is None
        if not self.flag_trigger_conflict(request, possible=possible):
            return
        network = record["network"]
        launch_ticks = record["launch_ticks"]
        record.update(conflicting_request_event_id=other.event.id, conflicting_launch_ticks=other_ticks)
        message = (f"Trigger network {network!r} has {'potentially ' if possible else ''}conflicting launch slots "
                   f"at {launch_ticks / 128:g} ns and {other_ticks / 128:g} ns." if other_ticks is not None else
                   f"Trigger network {network!r} could conflict with a request whose grid epoch is unknown.")
        self.add_network_diagnostic(request.seq, Diagnostic(
            severity="warning" if possible else "error",
            category="trigger_network_possible_conflict" if possible else "trigger_network_conflict", source=request.event.source,
            message=message,
            related_events=list(dict.fromkeys([request.event.id, other.event.id, record["source_event_id"], other.record["source_event_id"],
                                               *request.provenance["source_event_ids"], *other.provenance["source_event_ids"]])),
            details={"network": network, "launch_ticks": [launch_ticks, other_ticks], "minimum_spacing_ns": 252},
        ), request, other)

    def mark_trigger_window_conflict(self, request: _NetworkRequest, window: _NetworkEdgeWindow) -> None:
        record = request.record
        launch = record["launch_ticks"]
        first, last = window.launch_bounds()
        if first - launch >= _TRIGGER_SPACING_TICKS or (last is not None and (last < first or launch - last >= _TRIGGER_SPACING_TICKS)):
            return
        # An arrival on the same tick runs before a later launch on that tick.
        if not self.flag_trigger_conflict(request, possible=True, after_ticks=max(launch, first)):
            return
        event = window.source.event
        record["conflicting_window_event_id"] = event.id
        network = record["network"]
        self.add_network_diagnostic(request.seq, Diagnostic(
            severity="warning", category="trigger_network_possible_conflict", source=request.event.source,
            message=f"Trigger network {network!r} could conflict with an unobserved edge window.",
            related_events=list(dict.fromkeys([request.event.id, event.id, *request.provenance["source_event_ids"]])),
            details={"network": network, "launch_ticks": [launch], "minimum_spacing_ns": 252},
        ), request)

    def add_network_diagnostic(self, seq: _Sequencer, diagnostic: Diagnostic, *requests: _NetworkRequest) -> None:
        state = seq.state
        if self.prepared_trace:
            packets = tuple(dict.fromkeys(
                packet_id for request in requests for packet_id in request.provenance["source_rt_packet_ids"]
            ))
            self.network_diagnostic_packets.append((state, len(state.diagnostics), packets))
        state.diagnostics.append(diagnostic)

    def resolve_network_diagnostic_links(self) -> None:
        if not self.network_diagnostic_packets:
            return
        events = {event.id: event for seq in self.sequencers.values() for event in seq.state.events}
        needed = {packet_id for _, _, packet_ids in self.network_diagnostic_packets for packet_id in packet_ids}
        anchors = {}
        for seq in self.sequencers.values():
            for packet in seq.state.rt_packets:
                if packet.id in needed:
                    anchor = events.get(packet.meta.get("rt_execution_event_id")) or events.get(packet.meta.get("q1_issue_event_id"))
                    if anchor is not None:
                        anchors[packet.id] = anchor.id
        for state, index, packet_ids in self.network_diagnostic_packets:
            diagnostic = state.diagnostics[index]
            retained = [event_id for event_id in diagnostic.related_events if event_id in events]
            if retained == diagnostic.related_events:
                continue
            represented = {events[event_id].meta.get("rt_packet_id") for event_id in retained}
            retained.extend(anchors[packet_id] for packet_id in packet_ids if packet_id not in represented and packet_id in anchors)
            # Prefer an origin visible in Normal when the request marker was omitted.
            retained = sorted(
                dict.fromkeys(retained),
                key=lambda event_id: (events[event_id].kind != "acquisition_result", not events[event_id].lane.startswith("rt.")),
            )
            state.diagnostics[index] = replace(diagnostic, related_events=retained)

    @staticmethod
    def flag_trigger_conflict(request: _NetworkRequest, *, possible: bool, after_ticks: int | None = None) -> bool:
        record = request.record
        status = "possible_conflict" if possible else "conflicted"
        if record["status"] in {status, "conflicted"}:
            return False
        pending = [route for route in record["routes"]
                   if route["status"] in {"pending", "scheduled", "scheduled_optional", "unknown_possible_conflict"}
                   and (after_ticks is None or route.get("possible_arrival_ticks", after_ticks + 1) > after_ticks)]
        if after_ticks is not None and not pending:
            return False
        record["status"] = status
        if request.launch is not None:
            request.launch.meta["status"] = "delivery_uncertain" if possible else "delivery_conflict"
        for route in pending:
            route["status"] = "unknown_possible_conflict" if possible else "unknown_conflict"
        return True

    def schedule_network_delivery(self, request: _NetworkRequest, route: dict, departure_ticks: int, *, uncertain: bool) -> None:
        target = route["target"]
        ticks = departure_ticks + _TRIGGER_PROPAGATION_TICKS + route["extra_latency_ns"] * 128
        route["possible_arrival_ticks"] = ticks
        if not request.optional:
            self.sequencers[target].signal_pending += 1
        self.schedule_ticks(ticks, 0.5 if uncertain else 0, target,
                            "trigger_network_uncertain" if uncertain else "trigger_network_arrival", (request, route))

    def deliver_network_trigger(self, seq: _Sequencer, request: _NetworkRequest, route: dict) -> None:
        if route["status"] in {"unknown_conflict", "unknown_possible_conflict"}:
            self.schedule_ticks(self.now_ticks, 0.5, seq.state.sequencer_id, "trigger_network_uncertain", (request, route))
            return
        seq.signal_pending -= 1
        assert request.launch is not None
        record = request.record
        event = self.trigger(seq, {**request.provenance, "address": record["address"], "network": record["network"],
                                  "source_event_id": request.launch.id, "source_sequencer_id": request.seq.state.sequencer_id,
                                  "request_event_id": request.event.id, "request_ticks": record["request_ticks"],
                                  "launch_event_id": request.launch.id, "launch_ticks": record["launch_ticks"],
                                  "time_ticks": self.now_ticks, "extra_latency_ns": route["extra_latency_ns"],
                                  "latency_ns": (self.now_ticks - record["request_ticks"]) / 128})
        route.update(status="delivered", arrival_event_id=event.id, arrival_ticks=self.now_ticks)

    def prune_uncertain_trigger_windows(self, seq: _Sequencer) -> None:
        # Unknown-epoch windows have the same width and arrive in time order.
        # A possible arrival on this tick precedes any latch change on the tick.
        windows = seq.uncertain_trigger_windows
        while windows and windows[0].latest_ticks <= self.now_ticks:
            windows.popleft()

    def update_uncertain_trigger_counts(self, seq: _Sequencer, *, reset: bool = False) -> None:
        self.prune_uncertain_trigger_windows(seq)
        if reset:
            seq.uncertain_trigger_counts.clear()
            seq.uncertain_trigger_evidence.clear()
            seq.truth_trigger_rounds.clear()
            seq.threshold_trigger_cases.clear()
        for arrival in seq.uncertain_trigger_windows:
            if reset:
                arrival.counted = False
            if seq.trigger_counters.enabled and not arrival.counted:
                self.count_possible_trigger(seq, arrival)

    @staticmethod
    def count_possible_trigger(seq: _Sequencer, arrival: _PossibleTriggerArrival) -> None:
        address = arrival.address
        counts = seq.uncertain_trigger_counts
        extra = 1
        if arrival.truth_round is not None:
            round_id, outputs = arrival.truth_round
            retained = seq.truth_trigger_rounds
            bounds = next((item for item in retained.values() if item.round_id == round_id), None)
            if bounds is None:
                bounds = _TruthTriggerBounds(round_id, outputs)
            lower, upper = bounds.arrivals.get(address, (0, 0))
            bounds.arrivals[address] = (lower + arrival.guaranteed_in_case, upper + 1)
            # Keep at most one recent round per address. Older arrivals remain in
            # the conservative bounds even when their correlation is no longer retained.
            retained[address] = bounds
        if arrival.threshold_case is not None:
            event_id, bit = arrival.threshold_case
            zero, one = seq.threshold_trigger_cases.setdefault((event_id, address), [[0, 0], [0, 0]])
            old_minimum, old_maximum = min(zero[0], one[0]), max(zero[1], one[1])
            case = one if bit else zero
            case[0] += arrival.guaranteed_in_case
            case[1] += 1
            minimum, maximum = min(zero[0], one[0]), max(zero[1], one[1])
            # Only one bit value can occur. Both cases can prove a count even when the bit is unknown.
            if minimum > old_minimum:
                seq.trigger_counters.receive(address)
            extra = (maximum - old_maximum) - (minimum - old_minimum)
        counts[address] = counts.get(address, 0) + extra
        seq.uncertain_trigger_evidence[address] = arrival
        arrival.counted = True

    @staticmethod
    def decide_truth_trigger_condition(seq: _Sequencer, mask: int, operator: int, extra: dict[int, int | None]) -> bool | None:
        groups = {bounds.round_id: bounds for address, bounds in seq.truth_trigger_rounds.items()
                  if mask & (1 << (address - 1))}
        counters = seq.trigger_counters
        for bounds in groups.values():
            result = None
            # Refine one round at a time. Other rounds remain independent bounds.
            for output in bounds.outputs:
                counts, possible = dict(counters.counts), dict(extra)
                for address, (lower, upper) in bounds.arrivals.items():
                    if address == output:
                        counts[address] = counts.get(address, 0) + lower
                    if possible[address] is not None:
                        possible[address] -= lower if address == output else upper
                decision = replace(counters, counts=counts).decide(mask, operator, possible)
                if decision is None or result is not None and decision != result:
                    break
                result = decision
            else:
                return result
        return None

    def decide_trigger_condition(self, seq: _Sequencer, mask: int, operator: int) -> bool | None:
        counters = seq.trigger_counters
        extra: dict[int, int | None] = dict(seq.uncertain_trigger_counts)
        for address, counts in seq.edge_trigger_counts.items():
            extra[address] = None if counts.upper is None else seq.uncertain_trigger_counts.get(address, 0) + counts.upper
        decision = counters.decide(mask, operator, extra)
        if decision is not None:
            return decision
        decision = self.decide_truth_trigger_condition(seq, mask, operator, extra)
        if decision is not None:
            return decision
        source = None
        selected = {}
        for (event_id, address), cases in seq.threshold_trigger_cases.items():
            if not mask & (1 << (address - 1)) or cases[0] == cases[1]:
                continue
            if source is not None and event_id != source:
                return None
            source = event_id
            selected[address] = cases
        if len(selected) < 2:
            return None

        # One bit can correlate several selected addresses. Keep network uncertainty within each case.
        def outcome(bit: int) -> bool | None:
            counts, possible = dict(counters.counts), dict(extra)
            for address, (zero, one) in selected.items():
                lower, upper = one if bit else zero
                added = lower - min(zero[0], one[0])
                counts[address] = counts.get(address, 0) + added
                if possible[address] is not None:
                    possible[address] += upper - max(zero[1], one[1]) - added
            return replace(counters, counts=counts).decide(mask, operator, possible)

        zero, one = outcome(0), outcome(1)
        return zero if zero == one else None

    def uncertain_network_trigger(self, seq: _Sequencer, request: _NetworkRequest, route: dict) -> None:
        guaranteed_in_case = route["status"] == "scheduled_optional"
        if guaranteed_in_case:
            route["status"] = "possible"
        latest = self.now_ticks + (_TRIGGER_GRID_TICKS - 1 if route["status"] == "unknown_epoch" else 0)
        round_id = request.truth_round_id
        truth_round = (round_id, tuple(request.provenance["truth_round_outputs"])) if round_id is not None else None
        record = request.record
        source_event_ids = (record["source_event_id"], *request.provenance["source_event_ids"], request.event.id,
                            record.get("conflicting_request_event_id"), record.get("conflicting_window_event_id"))
        self.uncertain_trigger(seq, _PossibleTriggerArrival(
            record["address"], latest, threshold_case=request.threshold_case, guaranteed_in_case=guaranteed_in_case,
            truth_round=truth_round, source_event_ids=tuple(event_id for event_id in source_event_ids if event_id is not None),
            source_rt_packet_ids=tuple(request.provenance["source_rt_packet_ids"]),
        ))

    def uncertain_trigger(self, seq: _Sequencer, arrival: _PossibleTriggerArrival) -> None:
        if seq.rt_done:
            return
        address = arrival.address
        previous = seq.uncertain_trigger_tokens.get(address)
        if previous is None or previous.latest_ticks <= arrival.latest_ticks:
            seq.uncertain_trigger_tokens[address] = arrival
        self.prune_uncertain_trigger_windows(seq)
        if seq.trigger_counters.enabled:
            self.count_possible_trigger(seq, arrival)
        if arrival.latest_ticks > self.now_ticks:
            seq.uncertain_trigger_windows.append(arrival)
        if seq.rt_wait is not None and seq.rt_wait[0] == "trigger_wait" and seq.rt_wait[1].args[0] == address:
            self.schedule_trigger_wait_check(seq)

    def block_uncertain_trigger(self, seq: _Sequencer, command: _Command, acquisition: bool, consequence: str,
                                evidence: tuple[_PossibleTriggerArrival, ...]) -> None:
        cause = "An acquisition trigger" if acquisition else "A trigger network delivery"
        category = "acquisition_trigger_outcome_unknown" if acquisition else "trigger_network_outcome_unknown"
        self.fail(seq, "blocked", f"{cause} could {consequence}.", command.instruction, category=category)
        self.clip_q1_issue(seq, self.now)
        diagnostic = seq.state.diagnostics[-1]
        seq.state.diagnostics[-1] = replace(diagnostic, related_events=self.trigger_evidence_links(evidence))

    def trigger_evidence_links(self, arrivals: tuple[_PossibleTriggerArrival, ...]) -> list[str]:
        if not arrivals:
            return []
        events = {event.id: event for seq in self.sequencers.values() for event in seq.state.events}
        related = {}
        packets = None
        for arrival in arrivals:
            retained = {event_id: events[event_id] for event_id in arrival.source_event_ids if event_id in events}
            for event in tuple(retained.values()):
                if event.kind == "trigger_network_request":
                    retained.update((event_id, events[event_id]) for event_id in event.meta.get("source_event_ids", ())
                                    if event_id in events)
            if not any(event.lane.startswith("rt.") for event in retained.values()):
                # Sparse prepared runs retain acquisition packets even if their results were suppressed.
                if packets is None:
                    packets = {packet.id: packet for seq in self.sequencers.values() for packet in seq.state.rt_packets}
                for packet_id in arrival.source_rt_packet_ids:
                    packet = packets.get(packet_id)
                    if packet is None:
                        continue
                    anchor = events.get(packet.meta.get("rt_execution_event_id")) or events.get(packet.meta.get("q1_issue_event_id"))
                    if anchor is not None:
                        retained[anchor.id] = anchor
            related.update(retained)
        # The first diagnostic link is shown in Normal. Prefer the source acquisition over debug requests.
        return sorted(related, key=lambda event_id: (related[event_id].kind != "acquisition_result",
                                                     not related[event_id].lane.startswith("rt.")))

    def schedule_trigger_interval(self, target: str, address: int, start_ticks: int, end_ticks: Callable[[], int | None],
                                  source_event_id: str, *, after_rt: bool = False,
                                  count_limit: int | None = None) -> _TriggerArrivalInterval:
        interval = _TriggerArrivalInterval(address, start_ticks, end_ticks, source_event_id, after_rt, count_limit)
        phase = _POST_RT_POSSIBLE_TRIGGER_PHASE if after_rt else 0.5
        self.schedule_ticks(start_ticks, phase, target, "trigger_interval", (interval, True))
        return interval

    def observe_trigger_interval(self, seq: _Sequencer, interval: _TriggerArrivalInterval, activate: bool) -> None:
        if seq.rt_done or not interval.contains(self.now_ticks):
            return
        if activate:
            seq.edge_trigger_intervals = [entry for entry in seq.edge_trigger_intervals if entry.contains(self.now_ticks)]
            seq.edge_trigger_intervals.append(interval)
            if seq.rt_wait is not None and seq.rt_wait[0] == "trigger_wait":
                self.schedule_trigger_wait_check(seq)
        if seq.trigger_counters.enabled:
            if activate and interval.count_limit is not None:
                interval.full_latch_epoch = seq.trigger_latch_epoch
            counts = seq.edge_trigger_counts.setdefault(interval.address, _EdgeTriggerCounts())
            if interval not in counts.intervals:
                counts.intervals[interval] = None
                if counts.upper is not None:
                    counts.upper = None if interval.count_limit is None else counts.upper + interval.count_limit

    def complete_trigger_interval(self, seq: _Sequencer, interval: _TriggerArrivalInterval) -> None:
        if (seq.rt_done or interval.full_latch_epoch != seq.trigger_latch_epoch
                or interval.latch_end_ticks is not None and interval.latch_end_ticks < self.now_ticks):
            return
        address = interval.address
        count = interval.count_limit
        assert count is not None
        # The aggregate establishes a count at this boundary without locating any individual arrival.
        seq.trigger_counters.counts[address] = seq.trigger_counters.counts.get(address, 0) + count
        bounds = seq.edge_trigger_counts[address]
        del bounds.intervals[interval]
        if bounds.upper is not None:
            bounds.upper -= count
        if not bounds.intervals:
            del seq.edge_trigger_counts[address]

    def recheck_trigger_intervals(self, seq: _Sequencer) -> None:
        seq.edge_trigger_intervals = [entry for entry in seq.edge_trigger_intervals if entry.contains(self.now_ticks)]
        for interval in seq.edge_trigger_intervals:
            # Early arrivals on this tick preceded the RT latch change. Late ones still can count.
            tick = self.now_ticks if interval.after_rt else self.now_ticks + 1
            phase = _POST_RT_POSSIBLE_TRIGGER_PHASE if interval.after_rt else 0.5
            self.schedule_ticks(tick, phase, seq.state.sequencer_id, "trigger_interval", (interval, False))

    def schedule_trigger_wait_check(self, seq: _Sequencer) -> None:
        # Counter evidence keeps its arrival phase. A wait can still see a definite arrival later on this tick.
        self.schedule_ticks(self.now_ticks, _TRIGGER_WAIT_CHECK_PHASE, seq.state.sequencer_id, "trigger_wait_check")

    def check_trigger_wait(self, seq: _Sequencer) -> None:
        if seq.rt_wait is None or seq.rt_wait[0] != "trigger_wait":
            return
        command = seq.rt_wait[1]
        possible = seq.uncertain_trigger_tokens.get(command.args[0])
        if possible is not None and possible.latest_ticks >= self.now_ticks:
            self.block_uncertain_trigger(seq, command, possible.from_acquisition, "release this wait_trigger", (possible,))
            return
        for interval in seq.edge_trigger_intervals:
            if interval.address == command.args[0] and interval.contains(self.now_ticks):
                self.block_trigger_interval(seq, command, [interval.source_event_id], "release this wait_trigger")
                return

    def block_trigger_interval(self, seq: _Sequencer, command: _Command, sources: list[str], consequence: str) -> None:
        self.fail(seq, "blocked", f"An unobserved digital edge could {consequence}.", command.instruction,
                  category="digital_edge_trigger_outcome_unknown")
        self.clip_q1_issue(seq, self.now)
        diagnostic = seq.state.diagnostics[-1]
        seq.state.diagnostics[-1] = replace(diagnostic, related_events=list(dict.fromkeys(sources)))

    def trigger(self, seq: _Sequencer, data: dict) -> core.TimelineEvent:
        address = data["address"]
        event = self.emit(seq, "trigger_receive", self.now, source=SourceLocation("<simulation>", 1, 1, ""),
                          meta=data, lane="rt.trigger")
        if seq.rt_done:
            return event
        seq.trigger_counters.receive(address)
        token = {**data, "event_id": event.id, "time_ns": self.now}
        if seq.rt_wait is not None and seq.rt_wait[0] == "trigger_wait" and seq.rt_wait[1].args[0] == address:
            _, command, started = seq.rt_wait
            seq.rt_wait = None
            packet = seq.state.rt_packets[command.packet_index]
            self.emit(seq, "trigger_wait", started, end=self.now, instruction=command.instruction,
                      meta={"rt_packet_id": packet.id, "input_event_ids": [event.id]})
            self.finish_rt(seq, command, started, self.now-started+command.duration, input_event_id=event.id)
        else:
            seq.trigger_tokens.setdefault(address, deque()).append(token)
        return event

    def route_unknown_acquisition_trigger(self, seq: _Sequencer, source: core.TimelineEvent,
                                          routes: list[dict], provenance: dict, *,
                                          zero_latency_after_rt: bool = False) -> None:
        possible: list[list[dict]] = [[], []]
        fixed: dict[tuple[str, int, int], list[list[dict]]] = {}
        for route in routes:
            bit = route["thresholded"]
            if "network" in route:
                possible[bit].append(route)
            else:
                key = route["target"], route["address"], route["latency_ns"]
                fixed.setdefault(key, [[], []])[bit].append(route)
        guaranteed = []
        for zero, one in fixed.values():
            common = min(len(zero), len(one))
            guaranteed.extend(zero[:common])
            possible[0].extend(zero[common:])
            possible[1].extend(one[common:])
        self.route_trigger_request(seq, source, guaranteed, provenance, zero_latency_after_rt=zero_latency_after_rt)
        for bit, routes in enumerate(possible):
            self.route_trigger_request(seq, source, routes, {**provenance, "thresholded_case": bit},
                                       zero_latency_after_rt=zero_latency_after_rt)

    @staticmethod
    def close_acquisition_window(measurement: dict, end: int | float, status: str) -> None:
        window = measurement["window"]
        end = max(end, window["start_ns"])
        window.update(end_ns=end, effective_duration_ns=end - window["start_ns"], status=status)

    def finish_acquisition(self, seq: _Sequencer, measurement: dict, *,
                           interrupted_by: core.TimelineEvent | None = None) -> None:
        command = measurement["command"]
        args = command.args
        if self.now <= measurement["start_ns"]:
            self.fail(seq, "error", "Acquisition was interrupted before any sample interval elapsed.", command.instruction,
                      category="acquisition_overlap")
            return
        interrupted = interrupted_by is not None
        self.close_acquisition_window(measurement, self.now, "interrupted" if interrupted else "completed")
        if interrupted_by is not None:
            measurement["window"]["interrupted_by_event_id"] = interrupted_by.id
        seq.acquisition = None
        result = None
        supplied = self.find_acquisition_input(seq.state.sequencer_id, *args[:2])
        if supplied is not None:
            index, item = supplied
            self.used_acquisition_inputs.add(index)
            result = {key: item[key] for key in ("i", "q", "thresholded") if key in item}
            result.update(input_index=index, source={"kind": "scenario", "label": f"acquisition input {index}"})
        if result is None:
            try:
                if self.signals is not None:
                    result = self.signals.acquire(seq.state.sequencer_id, measurement["start_ns"], self.now-measurement["start_ns"],
                        args[0], args[1], weights=args[2:4] if command.instruction.op != "acquire" else None,
                        integration_length_source=measurement["integration_length_source"])
            except ValueError as error:
                self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                return
            if result is not None:
                result = {**result, "source": result.get("source", {"kind": "physical", "label": "modeled acquisition"})}
        routes = [route for route in self.config["trigger_routes"]
                  if "source_kind" not in route and route.get("source") == seq.state.sequencer_id]
        missing_reason = None
        if result is None:
            missing_reason = "Acquisition requires a scenario result or configured physical input."
            result = {"status": "unavailable", "reason": missing_reason, "source": {"kind": "missing"}}
        result = {**result, "acquisition_index": args[0], "bin_index": args[1], "start_ns": measurement["start_ns"],
                  "duration_ns": self.now-measurement["start_ns"], "interrupted": interrupted,
                  "integration_length_ns": measurement["duration_ns"],
                  "integration_length_source": measurement["integration_length_source"],
                  "rt_packet_id": seq.state.rt_packets[command.packet_index].id}
        thresholded = result.get("thresholded")
        if routes and thresholded is None:
            result["trigger_threshold_status"] = "unknown"
        kind, lane = (("acquisition_result", "rt.acquire") if missing_reason is None
                      else ("acquisition_unavailable", "debug.acquisition"))
        event = self.emit(seq, kind, self.now, instruction=command.instruction, meta=result, lane=lane)
        if missing_reason is None:
            seq.acquisitions.append({**result, "event_id": event.id, "time_ns": self.now})
        if not seq.state.record_trace and (missing_reason is not None or (routes and thresholded is None)):
            seq.state.events.append(event)
        provenance = {"source_event_ids": [event.id], "source_rt_packet_ids": [result["rt_packet_id"]]}
        if routes and thresholded is None:
            seq.state.diagnostics.append(Diagnostic(
                severity="warning", category="acquisition_trigger_threshold_unknown", source=event.source,
                message="The acquisition has no thresholded bit for its configured trigger routes.", related_events=[event.id],
            ))
            self.route_unknown_acquisition_trigger(seq, event, routes, provenance, zero_latency_after_rt=interrupted)
        else:
            self.route_trigger_request(seq, event, [route for route in routes if route["thresholded"] == thresholded],
                                       provenance, zero_latency_after_rt=interrupted)
        if missing_reason is not None:
            self.fail(seq, "blocked", missing_reason, command.instruction)
            diagnostic = seq.state.diagnostics[-1]
            seq.state.diagnostics[-1] = replace(diagnostic, related_events=[event.id])
            return
        if measurement["tb_id"]:
            if "thresholded" not in result:
                self.fail(seq, "blocked", "Threshold feedback requires a thresholded acquisition result.", command.instruction)
                return
            self.send(seq, measurement["tb_id"], (measurement["tb_valid"] << 1) | int(bool(result["thresholded"])), command.instruction, event.id,
                      tb_cfg=measurement["tb_cfg"], source_packet=seq.state.rt_packets[command.packet_index])
        if measurement["iq_id"]:
            scale = self.config.get("acquisition_iq_scale", {}).get(seq.state.sequencer_id)
            is_raw_scenario = result.get("source", {}).get("kind") == "scenario" and all(type(result.get(component)) is int for component in ("i", "q"))
            if not all(component in result for component in ("i", "q")) or (scale is None and not is_raw_scenario):
                self.fail(seq, "blocked", "IQ feedback needs integer scenario I/Q accumulator values or an explicit acquisition_iq_scale.", command.instruction)
                return
            scaled_values = [result[component] * scale if scale is not None else result[component] for component in ("i", "q")]
            if any(not -(2**47) <= value < 2**47 for value in scaled_values):
                self.fail(seq, "error", "Scaled IQ accumulator is outside signed 48-bit range.", command.instruction)
                return
            words = [round(value) for value in scaled_values]
            if any(not -(2**47) <= word < 2**47 for word in words):
                self.fail(seq, "error", "Rounded IQ accumulator is outside signed 48-bit range.", command.instruction)
                return
            for word in words:
                # The signed shift extends bit 47, then LINQ keeps only the selected word.
                payload = core._signed_32_bit_register_value(word >> measurement["iq_shift"])
                self.send(seq, measurement["iq_id"], payload, command.instruction, event.id)
