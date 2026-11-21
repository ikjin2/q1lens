"""Execution state, register writes and timeline records shared by Q1 schedulers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from q1timeline.analysis.values import Concrete, SymbolTable, Value
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm import core
from q1timeline.q1asm.ast import Instr, SourceLocation
from q1timeline.q1asm.compiled_addresses import CompiledAddresses
from q1timeline.q1asm.core import FlagCase as _AluFlagCase
from q1timeline.q1asm.instruction_table import InstructionSpec

Confidence = Literal["exact", "symbolic", "assumed", "unknown", "runtime_dependent"]
BranchAssumptionPath = Literal["collapsed", "taken", "fallthrough", "both"]

VALID_BRANCH_ASSUMPTION_PATHS = {"collapsed", "taken", "fallthrough", "both"}
CLASSICAL_REGISTER_MASK = 0xFFFFFFFF
RT_DURATION_MAX = 65535
NETZERO_PULSE_OPS = {"play", "play_netzero"}
ILLEGAL_INSTRUCTION_FLAG = core.ILLEGAL_INSTRUCTION_FLAG
# Analysis probes and Play admit external operations through their own handlers.
LOCAL_EXECUTION_OPS = core.LOCAL_OPS - {"set_cond"}


@dataclass(frozen=True)
class TimelineEvent:
    id: str
    sequencer_id: str
    lane: str
    kind: str
    t0: Value
    t1: Value | None
    duration: Value
    label: str
    confidence: Confidence
    source: SourceLocation
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RtPacket:
    id: str
    sequencer_id: str
    source: SourceLocation
    op: str
    duration: Value
    q1_issue_t0: Value
    q1_issue_t1: Value
    rt_t0: Value | None
    rt_t1: Value | None
    confidence: Confidence
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NetZeroWindow:
    source: SourceLocation
    event_id: str
    start_ns: int
    maxt_ns: int
    maxt_register: str | None


@dataclass(frozen=True)
class AnalysisVisitEntry:
    event_start: int
    packet_start: int
    diagnostic_start: int


@dataclass(frozen=True)
class RegisterWrite:
    sequencer_id: str
    register: str
    time: Value
    value: Value
    source: SourceLocation
    event_id: str | None
    confidence: Confidence
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingRegisterWrite:
    register: str
    value: Value
    provenance: dict[str, Any]
    source: SourceLocation
    available_issue_index: int
    event_id: str
    meta: dict[str, Any] = field(default_factory=dict)
    unsigned_bounds: tuple[int, int] | None = None
    known_bits: tuple[int, int] | None = None
    value_origin: tuple[str, bool] | None = field(default=None, compare=False)

    def ready_time_ns(self, issue_index: int, issue_end_ns: int) -> int:
        """Map the remaining Q1 pipeline slots to 4 ns clock ticks."""
        return issue_end_ns + 4 * (self.available_issue_index - issue_index - 1)


@dataclass
class LatchedState:
    marker: Value | None = None
    awg_gain: tuple[Value, Value] | None = None
    awg_offset: tuple[Value, Value] | None = None
    frequency: Value | None = None
    phase: Value | None = None
    phase_delta: Value | None = None
    digital: tuple[Value, Value, Value] | None = None
    scope_enable: Value | None = None
    netzero_reset: bool = False
    time_ref: bool = False
    pending_since: dict[str, SourceLocation] = field(default_factory=dict)
    pending_event_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class AnalysisState:
    sequencer_id: str
    labels: dict[str, int] = field(default_factory=dict)
    instructions_by_pc: dict[int, Instr] = field(default_factory=dict)
    register_aliases: dict[str, str] = field(default_factory=dict)
    pc: int = 0
    registers: SymbolTable = field(default_factory=dict)
    register_bounds: dict[str, tuple[int, int]] = field(default_factory=dict)
    latched_state: LatchedState = field(default_factory=LatchedState)
    conditional_enable: Value = field(default_factory=lambda: Concrete(0))
    conditional_mask: Value = field(default_factory=lambda: Concrete(0))
    conditional_operator: Value = field(default_factory=lambda: Concrete(0))
    conditional_enable_bounds: tuple[int, int] | None = None
    conditional_operator_bounds: tuple[int, int] | None = None
    conditional_enable_known_bits: tuple[int, int] | None = None
    conditional_operator_known_bits: tuple[int, int] | None = None
    conditional_else_duration: Value = field(default_factory=lambda: Concrete(0))
    conditional_time_unknown: bool = False
    q1_time_ns: Value = field(default_factory=lambda: Concrete(0))
    rt_fifo_starts: tuple[Value, ...] = ()
    reference_sync_ns: Value = field(default_factory=lambda: Concrete(0))
    q1_fifo_stall_ns: int = 0
    rt_time_ns: Value = field(default_factory=lambda: Concrete(0))
    rt_trigger_epoch: int = 0
    netzero_window: NetZeroWindow | None = None
    loop_stack: list[Any] = field(default_factory=list)
    rt_packets: list[RtPacket] = field(default_factory=list)
    events: list[TimelineEvent] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    symbol_origins: dict[str, Any] = field(default_factory=dict)
    register_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_register_writes: list[PendingRegisterWrite] = field(default_factory=list)
    register_history: list[RegisterWrite] = field(default_factory=list)
    current_resolved_args: list[dict[str, Any]] = field(default_factory=list)
    feedback_acq_channels: dict[str, str] = field(default_factory=dict)
    alu_flags: dict[str, bool] | None = None
    alu_flags_source: SourceLocation | None = None
    alu_flag_cases: frozenset[_AluFlagCase] | None = None
    alu_flags_read_hazard: bool = False
    loop_counter: int = 0
    q1_issue_index: int = 0
    next_pc: int | None = None
    stopped: bool = False
    executed_pcs: set[int] = field(default_factory=set)
    analysis_back_edge_limit: int | None = None
    analysis_back_edge_issue: TimelineEvent | None = None
    analysis_replayed_steps: int = 0
    analysis_replayed_pcs: set[int] = field(default_factory=set)
    analysis_visit_entries: dict[int, AnalysisVisitEntry] = field(default_factory=dict)
    branch_policy: str = "collapse_unresolved"
    branch_assumptions: dict[str, BranchAssumptionPath] = field(default_factory=dict)
    branch_explore_depth: int = 0
    loop_preview_counts: dict[str, int] = field(default_factory=dict)
    strict_q1asm: bool = False
    execution_mode: Literal["analysis", "concrete"] = "analysis"
    compiled_addresses: CompiledAddresses | None = None
    interior_pc: int | None = None
    next_interior_pc: int | None = None
    static_diagnostics: list[Diagnostic] = field(default_factory=list)
    register_known_bits: dict[str, tuple[int, int]] = field(default_factory=dict)
    register_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    value_origin_serial: int = field(default=0, compare=False)
    immutable_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    pending_phase_reset: bool | None = False
    nco_phase_uncertain: bool = False
    queue_alternatives: dict[str, AnalysisState] = field(default_factory=dict, compare=False, repr=False)
    queue_alternative_probe: bool = field(default=False, compare=False, repr=False)
    record_trace: bool = True
    bound_instructions: dict[int, BoundInstruction] = field(default_factory=dict, repr=False)
    event_serial: int = 0
    observed_loop_events: list[TimelineEvent] = field(default_factory=list)
    playback_devices: core.DeviceState | None = None


@dataclass(frozen=True)
class BoundInstruction:
    spec: InstructionSpec
    raw_diagnostics: tuple[Diagnostic, ...]
    duration_ns: int | None
    simple_operands: bool
    reads: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class PreparedDefProgram:
    """Reusable source data for a sweep that changes only named integer DEFs."""

    instructions_by_pc: dict[int, Instr]
    register_aliases: dict[str, str]
    param_symbols: SymbolTable
    revalidate: tuple[Instr, ...] | None
    lints: tuple[Diagnostic, ...]
    bindings: dict[int, BoundInstruction]
    needs_queue_analysis: bool
    compiled_addresses: CompiledAddresses | None = None
