"""Resumable, bounded execution of one analytical path over JSON lines.

This is not hardware simulation: feedback receipt values and Q1 waiting times
are explicit assumptions; unknown branches choose an assumed fallthrough path.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from copy import deepcopy
from pathlib import Path
from typing import Any

from q1timeline.analysis import interpreter
from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.playback_loops import ExecutedStep
from q1timeline.analysis.playback_timing import annotate_playback_queue_timing
from q1timeline.analysis.queue_timing import DISABLED_SYNC_REASON
from q1timeline.analysis.trigger_counters import ScheduledTriggerCounters, TriggerSchedule, selected_addresses
from q1timeline.analysis.values import (
    Concrete, Unknown, add_values, resolve_arg_value, resolve_def_values, subtract_values, symbol_table_from_params,
    value_to_json,
)
from q1timeline.diagnostics import Diagnostic
from q1timeline.execution import issue as q1_issue, operands as operand_binding, registers as register_writes
from q1timeline.execution import trace as timeline, validation as q1_validation
from q1timeline.execution.state import AnalysisState, CLASSICAL_REGISTER_MASK, LOCAL_EXECUTION_OPS, PendingRegisterWrite
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.external_inputs import resolve_feedback_sources, resolve_trigger_sources, validate_external_inputs
from q1timeline.feedback_fifo import DEFAULT_FEEDBACK_QUEUE_CAPACITY, feedback_pop_limitation, feedback_q1_completion, take_feedback
from q1timeline.project import ConfigLoadError, load_project_config, load_single_file_config
from q1timeline.q1asm.ast import Instr, SourceLocation
from q1timeline.q1asm.compiled_addresses import compile_addresses, next_source_position
from q1timeline.q1asm.core import branch_on_flags
from q1timeline.q1asm.core_alu import register_write_delay_slots, sub_flags
from q1timeline.q1asm.core_devices import DeviceState
from q1timeline.q1asm.instruction_table import branch_duration, get_instruction_spec, rt_duration_arg_for_instruction
from q1timeline.q1asm.linq_commands import MOCK_LIMITATION, raw_linq_operation
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.sequence import load_sequence_names


MAX_TRACE_EVENTS = 20_000
MAX_ASSUMPTION_REFERENCES = 100_000


@dataclass
class _ScheduledFeedback:
    arrivals: deque[dict]
    capacity: int
    pending: list[dict] = field(default_factory=list)

    def advance(self, time_ns: int) -> dict | None:
        while self.arrivals and self.arrivals[0]["time_ns"] <= time_ns:
            entry = self.arrivals.popleft()
            if len(self.pending) == self.capacity:
                return entry
            self.pending.append(entry)
        return None


@dataclass
class _Runner:
    state: AnalysisState
    symbols: dict
    trigger_counters: ScheduledTriggerCounters
    scheduled_feedback: _ScheduledFeedback | None = None
    scheduled_feedback_gap: bool = False
    trace: list[ExecutedStep] = field(default_factory=list)
    feedback_assumptions: list[str] = field(default_factory=list)
    trigger_assumptions: list[str] = field(default_factory=list)
    waiting: dict | None = None


def _record_q1_terminal(state: AnalysisState, reason: str, category: str = "playback_boundary") -> None:
    execution = state.metadata["execution"]
    if isinstance(state.q1_time_ns, Concrete) and "terminal" not in execution:
        execution["terminal"] = {"local_time_ns": state.q1_time_ns.value, "category": category,
                                 "display_reason": reason, "core": "q1"}


class PlaybackSession:
    def __init__(self, documents: dict[str, tuple[str, str]], *, params: dict | None = None,
                 alignment_mode: str = "none", alignment_anchor_kinds: tuple[str, ...] = (),
                 project: dict | None = None, external_inputs: dict | None = None,
                 hardware_parameters: dict[str, dict] | None = None,
                 feedback_queue_capacity: int = DEFAULT_FEEDBACK_QUEUE_CAPACITY):
        if type(feedback_queue_capacity) is not int or feedback_queue_capacity < 1:
            raise ValueError("feedback_queue_capacity must be a positive integer")
        self.documents = dict(documents)
        self.params = dict(params or {})
        self.external_inputs = validate_external_inputs(external_inputs, documents)
        self.base_external_inputs = deepcopy(self.external_inputs)
        self.trigger_sources = resolve_trigger_sources(self.external_inputs, self.params)
        self.feedback_queue_capacity = feedback_queue_capacity
        self.alignment_mode = alignment_mode
        self.alignment_anchor_kinds = alignment_anchor_kinds
        self.project = project or {}
        self.sequencer_metadata: dict[str, dict] = {}
        self.hardware_parameters = deepcopy(hardware_parameters or {})
        self.acquisition_context: dict[str, dict] = {}
        self.sequence_names: dict[str, Any] = {}
        self.parameters: list[dict] = []
        self.programs = {seq: parse_q1asm(text, file=file) for seq, (file, text) in documents.items()}
        self.choices: dict[str, str] = {}
        self.feedback_choices: dict[str, dict] = {}
        self._reset()

    @classmethod
    def from_project(cls, path: str | Path, *, params: dict | None = None) -> PlaybackSession:
        path = Path(path)
        config = load_single_file_config(path) if path.suffix.lower() == ".q1asm" else load_project_config(path)
        errors = [item for item in config.diagnostics if item.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)
        session = cls(
            {seq.id: (str(seq.file), seq.file.read_text(encoding="utf-8-sig")) for seq in config.sequencers},
            params={**config.params, **(params or {})}, alignment_mode=config.alignment_mode,
            alignment_anchor_kinds=config.alignment_anchor_kinds,
            external_inputs=config.external_inputs,
            feedback_queue_capacity=config.simulation.get("feedback_queue_capacity", DEFAULT_FEEDBACK_QUEUE_CAPACITY),
            hardware_parameters={seq.id: dict(seq.hardware_parameters) for seq in config.sequencers},
            project={"root": str(config.root), "alignment_mode": config.alignment_mode,
                     "feedback_transport_delay_ns": config.analysis.feedback_transport_delay_ns,
                     "alignment_anchor_kinds": list(config.alignment_anchor_kinds),
                     "display": config.display, "display_file": str(config.display_file) if config.display_file else None},
        )
        session.sequencer_metadata = {seq.id: {"module": seq.module} for seq in config.sequencers if seq.module}
        session.acquisition_context = {seq.id: {"sequencer_index": seq.sequencer_index, "module_type": seq.module_type}
                                       for seq in config.sequencers}
        session.sequence_names = {seq.id: load_sequence_names(seq.sequence_json) for seq in config.sequencers if seq.sequence_json}
        for name, value in sorted(session.params.items()):
            parameter = {"name": name, "value_json": json.dumps(value, sort_keys=True, allow_nan=False),
                         "overridden": name in (params or {})}
            if name in (params or {}) and name in config.params:
                parameter["base_value_json"] = json.dumps(config.params[name], sort_keys=True, allow_nan=False)
            session.parameters.append(parameter)
        return session

    def _reset(self) -> None:
        self.runners = []
        self.branches: list[dict] = []
        self.feedback: list[dict] = []
        self.triggers: list[dict] = []
        self.steps = 0
        self.assumption_references = 0
        self.next_runner = 0
        self.status, self.reason = "idle", None
        self.feedback_sources = resolve_feedback_sources(self.external_inputs)
        feedback_by_target = {source["target"]: source["arrivals"] for source in self.feedback_sources}
        for seq, program in self.programs.items():
            state = AnalysisState(
                sequencer_id=seq, labels=program.labels,
                instructions_by_pc={item.pc: item for item in program.instructions},
                register_aliases=operand_binding.register_aliases(program.defs), execution_mode="concrete", playback_devices=DeviceState(),
            )
            state.diagnostics.extend(program.diagnostics)
            symbols = symbol_table_from_params(self.params)
            defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
            state.compiled_addresses = compile_addresses(state.instructions_by_pc, state.labels)
            resolved = resolve_def_values(defs, symbols, state.diagnostics, labels=operand_binding.address_labels(state))
            state.symbol_origins = operand_binding.symbol_origins(self.params, symbols, defs, resolved)
            symbols.update(resolved)
            q1_validation.diagnose_program(state, symbols, program)
            state.metadata["execution"] = {
                "mode": "playback", "status": "paused", "steps": 0,
                "timing_model": "analytical", "address_model": operand_binding.address_model(state),
                "assumptions": ["One executed path with assumed feedback receipts and a 32-entry RT FIFO.",
                                "External trigger times are assumed receiver arrivals on the unaligned analytical RT clock; hardware transport latency is not modeled."],
            }
            sources = [source for source in self.trigger_sources if seq in source["targets"]]
            schedule = TriggerSchedule(
                addresses=frozenset(source["resolved_address"] for source in sources),
                arrivals=tuple(sorted((time, source["resolved_address"], f"external:{source['id']}:{index}")
                                      for source in sources for index, time in enumerate(source["times_ns"]))),
                settings=self.hardware_parameters.get(seq, {}),
            )
            arrivals = feedback_by_target.get(seq)
            fifo = (_ScheduledFeedback(deque(sorted(arrivals, key=lambda entry: entry["time_ns"])), self.feedback_queue_capacity)
                    if arrivals is not None else None)
            self.runners.append(_Runner(state, symbols, ScheduledTriggerCounters(schedule), fifo))
            errors = [item for item in state.diagnostics
                      if item.severity == "error" and not item.details.get("source_line_skipped")]
            if errors:
                error = errors[0]
                self.status, self.reason = "error", error.message
                execution = state.metadata["execution"]
                execution.update(status="error", reason=error.message)
                if error.source is not None:
                    execution["source"] = timeline.source_to_json(error.source)

    def run(self, *, budget_ms: int = 100, max_steps: int = 1000) -> dict:
        if type(budget_ms) is not int or not 1 <= budget_ms <= 5000:
            raise ValueError("budget_ms must be an integer between 1 and 5000")
        if type(max_steps) is not int or not 1 <= max_steps <= 10000:
            raise ValueError("max_steps must be an integer between 1 and 10000")
        if self.status not in {"idle", "paused"}:
            return self.summary()
        deadline = time.monotonic() + budget_ms / 1000
        initial_steps = self.steps
        self.status, self.reason = "running", None
        while self.steps - initial_steps < max_steps and time.monotonic() < deadline:
            if all(runner.state.stopped or runner.waiting is not None for runner in self.runners):
                self._finish_run()
                break
            if (sum(len(runner.state.events) for runner in self.runners) >= MAX_TRACE_EVENTS
                    or self.assumption_references >= MAX_ASSUMPTION_REFERENCES):
                self.status, self.reason = "limit", "Recorded event limit reached; the trace is a partial execution."
                break
            runner = self.runners[self.next_runner]
            self.next_runner = (self.next_runner + 1) % len(self.runners)
            if runner.state.stopped or runner.waiting is not None:
                continue
            if not self._step(runner) and runner.waiting is None:
                break
        if self.status == "running":
            if all(r.state.stopped or r.waiting is not None for r in self.runners):
                self._finish_run()
            else:
                self.status = "paused"
        return self.summary()

    def _finish_run(self) -> None:
        for runner in self.runners:
            execution = runner.state.metadata["execution"]
            if "fault" in execution:
                self.status, self.reason = "error", f"{runner.state.sequencer_id}: {execution['reason']}"
                return
        waiters = [runner for runner in self.runners if runner.waiting is not None]
        self.status = "waiting" if waiters else "completed"
        if waiters:
            reasons = dict.fromkeys(runner.waiting["reason"] for runner in waiters)
            self.reason = f"{len(waiters)} sequencer(s) waiting. " + " ".join(reasons)

    def choose(self, branch_id: str, path: str) -> dict:
        if path not in {"taken", "fallthrough"}:
            raise ValueError("Playback branch path must be taken or fallthrough")
        branch = next((item for item in self.branches if item["branch_id"] == branch_id), None)
        if branch is None:
            raise ValueError("Branch occurrence is not in the current execution")
        self._discard_choices_from(branch["step"])
        self.choices[branch_id] = path
        self._reset()
        # Replaying is done by the same bounded run requests as normal execution.
        return self.snapshot()

    def set_external_inputs(self, config: dict | None) -> dict:
        candidate = validate_external_inputs(self.base_external_inputs if config is None else config, self.documents)
        sources = resolve_trigger_sources(candidate, self.params)
        self.external_inputs, self.trigger_sources = candidate, sources
        self._reset()
        return self.snapshot()

    def accept_external_inputs(self) -> dict:
        self.base_external_inputs = deepcopy(self.external_inputs)
        return self.snapshot()

    def set_feedback(self, feedback_id: str, value: int | None, delay_ns: int) -> dict:
        if value is not None and (type(value) is not int or not -(1 << 31) <= value <= 0xFFFFFFFF):
            raise ValueError("Feedback value must be null or a 32-bit signed/unsigned integer")
        if type(delay_ns) is not int or not 0 <= delay_ns <= 9007199254740991:
            raise ValueError("Feedback delay_ns must be a nonnegative safe integer")
        receipt = next((item for item in self.feedback if item["feedback_id"] == feedback_id), None)
        if receipt is None:
            raise ValueError("Feedback occurrence is not in the current execution")
        if receipt.get("assumption_source") == "scenario":
            raise ValueError("Edit scheduled LINQ receipts in Configure external inputs.")
        self._discard_choices_from(receipt["step"])
        self.feedback_choices[feedback_id] = {"value": value, "delay_ns": delay_ns, "source": "user"}
        self._reset()
        return self.snapshot()

    def _discard_choices_from(self, step: int) -> None:
        branches = {item["branch_id"] for item in self.branches if item["step"] < step}
        receipts = {item["feedback_id"] for item in self.feedback if item["step"] < step}
        self.choices = {key: value for key, value in self.choices.items() if key in branches}
        self.feedback_choices = {key: value for key, value in self.feedback_choices.items() if key in receipts}

    def summary(self) -> dict:
        return {
            "status": self.status, "can_resume": self.status in {"idle", "paused"},
            "reason": self.reason, "steps": self.steps, "pending_branch": None,
        }

    def _block(self, runner: _Runner, instruction: Instr, reason: str, *, error: bool = False,
               emit_boundary: bool = True) -> bool:
        self.status, self.reason = ("error" if error else "blocked"), reason
        state = runner.state
        execution = state.metadata["execution"]
        execution.update(status=self.status, reason=reason, pc=instruction.pc, source=timeline.source_to_json(instruction.source))
        if state.compiled_addresses is not None:
            execution["compiled_pc"] = q1_validation.compiled_pc(state, instruction.pc)
        _record_q1_terminal(state, reason)
        if emit_boundary:
            timeline.emit_event(
                state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns,
                t1=None, duration=Unknown(reason), label=reason, confidence="unknown", source=instruction.source,
                meta={"reason": "playback_boundary", "playback": True},
            )
        return False

    def _step(self, runner: _Runner) -> bool:
        state = runner.state
        history_start = len(state.register_history)
        instruction = operand_binding.execution_instruction(state)
        if instruction is None:
            if state.pc == len(state.instructions_by_pc) and any(
                item.details.get("source_line_skipped") for item in state.diagnostics
            ):
                state.stopped = True
                return True
            self.status, self.reason = "error", "Execution reached a PC without an instruction or stop."
            state.metadata["execution"].update(status=self.status, reason=self.reason, pc=state.pc)
            _record_q1_terminal(state, self.reason)
            return False
        if runner.scheduled_feedback is not None:
            overflow = runner.scheduled_feedback.advance(state.q1_time_ns.value)
            if overflow is not None:
                return self._feedback_overflow(runner, instruction, overflow)
        register_writes.apply_ready_register_writes(state)
        spec = get_instruction_spec(instruction.op)
        validation = spec.validate(instruction)
        invalid = [item for item in validation if item.severity == "error"]
        if invalid:
            state.diagnostics.extend(validation)
            return self._block(runner, instruction, invalid[0].message, error=True)
        if (spec.category == "unknown"
                or (spec.timing_effect_status == "analysis_incomplete" and not spec.emits_rt_packet
                    and instruction.op != "set_cond")):
            return self._block(runner, instruction, f"{instruction.op} requires unavailable execution or external timing semantics.")
        scope = operand_binding.operand_scope(runner.symbols, state)
        duration_error = q1_validation.invalid_rt_duration(instruction, spec, scope)
        if instruction.op == "set_cond" or duration_error is not None:
            _, invalid, diagnostic_start = q1_validation.validate_instruction(state, instruction, runner.symbols)
            if not q1_validation.concrete_instruction_ready(
                state, instruction, runner.symbols, spec, invalid, diagnostic_start,
                supported_ops=LOCAL_EXECUTION_OPS | {instruction.op}, allow_symbolic_operands=True,
                rt_duration_error=duration_error,
            ):
                execution = state.metadata["execution"]
                return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        if instruction.op == "set_cond":
            enable, mask, operator, duration = (resolve_arg_value(arg, scope) for arg in instruction.args)
            if (not isinstance(enable, Concrete) or not isinstance(duration, Concrete)
                    or enable.value and not all(isinstance(value, Concrete) for value in (mask, operator))):
                return self._block(runner, instruction, "set_cond trigger-counter control operands are unknown.")
            if enable.value and operator.value > 5:
                return self._block(runner, instruction, "Conditional operators 6 and 7 have no supported semantics.")
        rt_start = state.rt_time_ns
        assumption = None
        decision = None
        latch_enable = None
        if spec.emits_rt_packet:
            if not isinstance(rt_start, Concrete):
                return self._block(runner, instruction, "The RT start time for the packet is unknown.")
            runner.trigger_counters.advance(rt_start.value)
            if state.conditional_enable != Concrete(0):
                mask, operator = state.conditional_mask.value, state.conditional_operator.value
                decision = runner.trigger_counters.decide(mask, operator)
                if decision is None:
                    return self._block(runner, instruction, "set_cond trigger-counter inputs are incomplete. Configure external inputs for the selected addresses.")
                addresses = selected_addresses(mask)
                counted_ids = [runner.trigger_counters.counted_ids.get(address, ()) for address in addresses]
                if self.assumption_references + sum(map(len, counted_ids)) >= MAX_ASSUMPTION_REFERENCES:
                    self.status, self.reason = "limit", "Recorded trigger assumption limit reached."
                    return False
                assumption = {"mask": mask, "operator": operator, "taken": decision, "time_ns": rt_start.value,
                              "counts": {str(address): runner.trigger_counters.counters.counts.get(address, 0) for address in addresses},
                              "uncertain_addresses": sorted(runner.trigger_counters.uncertain_addresses().intersection(addresses)),
                              "input_event_ids": [event_id for group in counted_ids for event_id in group]}
            if decision is not False:
                raw_linq_op = (raw_linq_operation(operand_binding.arg_int(instruction.args[0], scope))
                               if instruction.op == "fb_cmd" else None)
                if "fb_acq_tb_mock" in {instruction.op, raw_linq_op}:
                    return self._block(runner, instruction, MOCK_LIMITATION)
                if spec.timing_effect_status == "analysis_incomplete":
                    return self._block(runner, instruction, f"{instruction.op} requires unavailable execution or external timing semantics.")
            if decision is not False and instruction.op == "set_latch_en":
                latch = resolve_arg_value(instruction.args[0], scope)
                if not isinstance(latch, Concrete) or latch.value not in (0, 1):
                    return self._block(runner, instruction, "set_latch_en requires a known enable value of 0 or 1.")
                latch_enable = latch.value
            if (decision is not False and instruction.op == "wait_sync"
                    and self.hardware_parameters.get(state.sequencer_id, {}).get("sync_en") is False):
                state.diagnostics.append(Diagnostic(severity="warning", category="analysis_incomplete",
                                                    message=DISABLED_SYNC_REASON, source=instruction.source))
                return self._block(runner, instruction, DISABLED_SYNC_REASON)
        duration_index = rt_duration_arg_for_instruction(spec, instruction)
        if (decision is not False and instruction.op != "wait_trigger"
                and duration_index is not None and duration_index < len(instruction.args)):
            if not isinstance(resolve_arg_value(instruction.args[duration_index], scope), Concrete):
                return self._block(runner, instruction, f"{instruction.op} duration is unknown.")
        event_start = len(state.events)
        continuation = interpreter._has_jump_continuation(state, instruction)
        if spec.category != "branch":
            state.next_pc, state.next_interior_pc = next_source_position(
                state.compiled_addresses, instruction, state.interior_pc, len(state.instructions_by_pc),
            )
        state.current_resolved_args = operand_binding.resolved_args_for_instruction(instruction, scope, state)
        diagnostic_start = len(state.diagnostics)
        flags_before = dict(state.alu_flags) if state.alu_flags is not None else None
        try:
            if instruction.op == "wait_trigger" and decision is not False:
                if not self._receive_trigger(runner, instruction, rt_condition_decision=decision):
                    return False
            elif instruction.op in {"fb_pop_data", "fb_pull_data"}:
                if runner.scheduled_feedback is not None:
                    if not self._receive_scheduled_feedback(runner, instruction, runner.scheduled_feedback):
                        return False
                elif instruction.op == "fb_pop_data":
                    self._receive_feedback(runner, instruction)
                else:
                    return self._block(runner, instruction,
                                       "fb_pull_data requires external timing. Configure a LINQ schedule for this receiver.")
            elif spec.category == "branch":
                if not self._branch(runner, instruction):
                    return False
            else:
                names = self.sequence_names.get(state.sequencer_id)
                interpreter._execute_instruction(state, instruction, runner.symbols,
                                          names.waveforms if names else {}, names.acquisitions if names else {},
                                          allow_symbolic_operands=True, rt_condition_decision=decision,
                                          share_resolved_issue=True)
        finally:
            state.current_resolved_args = []
        execution = state.metadata["execution"]
        execution_status = execution.get("status")
        fault = "fault" in execution
        if execution_status in {"error", "blocked"} and not fault:
            return self._block(runner, instruction, execution["reason"], error=execution_status == "error")
        stop_overflow = None
        if instruction.op == "stop" and runner.scheduled_feedback is not None:
            stop_overflow = runner.scheduled_feedback.advance(state.q1_time_ns.value)
            if stop_overflow is not None:
                # The Q1 issue was recorded, but the RT stop was only provisional.
                state.events.pop()
                state.events[event_start].meta.pop("stop_code", None)
                execution.pop("stop_code", None)
                execution.pop("stop_source", None)
        if spec.emits_rt_packet:
            packet = state.rt_packets[-1]
            if runner.scheduled_feedback_gap:
                packet.meta["scheduled_feedback_gap"] = True
                runner.scheduled_feedback_gap = False
            if assumption is not None:
                packet.meta["trigger_counter_assumption"] = assumption
            if decision is not False:
                runner.trigger_counters.apply(instruction.op, latch_enable)
                if latch_enable is not None:
                    packet.meta["trigger_latch_enable"] = latch_enable
        branch_taken = False
        branch_target = None
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            if event.kind == "trigger_receive":
                continue
            event.meta.update(playback=True, execution_index=state.q1_issue_index, source_pc=instruction.pc)
            if event.kind == "q1_issue":
                event.meta.update(flags_before=flags_before, flags_after=dict(state.alu_flags) if state.alu_flags is not None else None)
                branch_taken = bool(event.meta.get("branch_taken"))
                branch_target = event.meta.get("compiled_target_pc", event.meta.get("target_pc"))
            if runner.feedback_assumptions:
                event.meta["feedback_assumptions"] = list(runner.feedback_assumptions)
                self.assumption_references += len(runner.feedback_assumptions)
            if runner.trigger_assumptions:
                event.meta["trigger_assumptions"] = list(runner.trigger_assumptions)
                self.assumption_references += len(runner.trigger_assumptions)
            if assumption is not None:
                event.meta["trigger_counter_assumption"] = assumption
                self.assumption_references += len(assumption["input_event_ids"])
            if (state.metadata.get("branches", {}).get("assumptions") or runner.feedback_assumptions or runner.trigger_assumptions
                    or assumption is not None) and event.confidence == "exact":
                state.events[index] = replace(event, confidence="assumed")
        # Unknown values may propagate; invalid instructions never become executed steps.
        if not fault and any(item.severity == "error" for item in state.diagnostics[diagnostic_start:]):
            return self._block(runner, instruction, "Instruction validation failed.", error=True)
        runner.trace.append(interpreter._executed_step(state, instruction, event_start, rt_start,
                                                branch_taken, branch_target, continuation))
        state.executed_pcs.add(instruction.pc)
        state.q1_issue_index += 1
        state.metadata["execution"]["steps"] = state.q1_issue_index
        self.steps += 1
        q1_issue.advance_execution(state)
        if stop_overflow is not None:
            return self._feedback_overflow(runner, instruction, stop_overflow)
        if state.stopped:
            register_writes.apply_ready_register_writes(state)
            if fault:
                _record_q1_terminal(state, execution["reason"], "illegal_instruction")
            else:
                execution["status"] = "completed"
        if runner.feedback_assumptions:
            for index in range(history_start, len(state.register_history)):
                write = state.register_history[index]
                state.register_history[index] = replace(
                    write, confidence="assumed" if write.confidence == "exact" else write.confidence,
                    meta={**write.meta, "feedback_assumptions": list(runner.feedback_assumptions)},
                )
                self.assumption_references += len(runner.feedback_assumptions)
        self.assumption_references += sum(len(write.meta.get("branch_assumptions", []))
                                         for write in state.register_history[history_start:])
        return True

    def _receive_trigger(self, runner: _Runner, instruction: Instr, *, rt_condition_decision: bool | None = None) -> bool:
        state = runner.state
        spec, invalid, diagnostic_start = q1_validation.validate_instruction(state, instruction, runner.symbols)
        if not q1_validation.concrete_instruction_ready(
            state, instruction, runner.symbols, spec, invalid, diagnostic_start,
            supported_ops=LOCAL_EXECUTION_OPS | {"wait_trigger"}, allow_symbolic_operands=True,
        ):
            execution = state.metadata["execution"]
            return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        address, duration = [resolve_arg_value(arg, operand_binding.operand_scope(runner.symbols, state)) for arg in instruction.args]
        if not all(isinstance(value, Concrete) for value in (address, duration, state.rt_time_ns)):
            return self._block(runner, instruction, "wait_trigger address, duration or RT start is unknown.")
        if not 0 <= address.value <= 15:
            return self._block(runner, instruction, f"wait_trigger resolved address {address.value} is outside the supported range 0..15.")
        armed = state.rt_time_ns.value
        candidates = []
        connected = [source for source in self.trigger_sources if state.sequencer_id in source["targets"]]
        matching = [source for source in connected if source["resolved_address"] == address.value]
        for source in matching:
            for index, arrival in enumerate(source["times_ns"]):
                event_id = f"external:{source['id']}:{index}"
                if arrival >= armed and event_id not in runner.trigger_assumptions:
                    candidates.append((arrival, event_id, source["id"]))
        assumption = {"source_id": None, "event_id": None, "address": address.value,
                      "armed_at_ns": armed, "arrival_ns": None, "resume_ns": None,
                      "duration_ns": duration.value, "status": "waiting"}
        if not candidates:
            reason = ("No unused trigger at or after the wait start" if any(s["times_ns"] for s in matching)
                      else "No scheduled trigger" if matching else "No connected trigger source")
            assumption["reason"] = f"{reason} for address {address.value} at {armed} ns. Configure external inputs."
            assumption["source_id"] = matching[0]["id"] if len(matching) == 1 else None
            runner.waiting = assumption
            state.metadata["execution"].update(status="waiting", reason=assumption["reason"], pc=instruction.pc)
            timeline.emit_event(state, lane="rt.sync", kind="wait_trigger", t0=state.rt_time_ns,
                             t1=None, duration=Unknown(assumption["reason"]), label="Waiting for external trigger",
                             confidence="unknown", source=instruction.source,
                             meta={"playback": True, "trigger_assumption": assumption})
            return False
        arrival, event_id, source_id = min(candidates)
        assumption.update(source_id=source_id, event_id=event_id, arrival_ns=arrival,
                          resume_ns=arrival + duration.value, status="received")
        event_start = len(state.events)
        interpreter._execute_instruction(state, instruction, runner.symbols, {}, {}, allow_symbolic_operands=True,
                                  rt_condition_decision=rt_condition_decision)
        if state.metadata["execution"].get("status") in {"error", "blocked"}:
            execution = state.metadata["execution"]
            return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        end, elapsed = Concrete(assumption["resume_ns"]), Concrete(assumption["resume_ns"] - armed)
        state.rt_time_ns = end
        packet = state.rt_packets[-1]
        packet = replace(packet, rt_t1=end, duration=elapsed, meta={**packet.meta, "trigger_assumption": assumption})
        state.rt_packets[-1] = packet
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            event.meta["trigger_assumption"] = assumption
            if event.kind == "wait_trigger":
                edge = Concrete(arrival)
                receive = timeline.emit_event(
                    state, lane="rt.trigger", kind="trigger_receive", t0=edge, t1=edge,
                    duration=Concrete(0), label="trigger receive", confidence="assumed",
                    source=SourceLocation("<simulation>", 1, 1, ""),
                    meta={"playback": True, "address": address.value, "trigger_assumption": assumption,
                          "consumer_event_id": event.id, "resolved_args": []},
                )
                assumption["arrival_event_id"] = receive.id
                event.meta["input_event_ids"] = [receive.id]
                state.events[index] = replace(event, t1=end, duration=elapsed, confidence="assumed")
        interpreter._advance_after_zero_duration(state, packet, event_start)
        runner.trigger_assumptions.append(event_id)
        self.triggers.append({**assumption, "sequencer_id": state.sequencer_id})
        return True

    def _receive_feedback(self, runner: _Runner, instruction: Instr) -> None:
        state = runner.state
        event_start = len(state.events)
        pending_start = len(state.pending_register_writes)
        interpreter._execute_instruction(state, instruction, runner.symbols, {}, {}, allow_symbolic_operands=True)
        if state.metadata["execution"].get("status") in {"error", "blocked"}:
            return
        feedback_id = f"{state.sequencer_id}:feedback:{state.q1_issue_index}"
        assumption = dict(self.feedback_choices.get(feedback_id, {"value": None, "delay_ns": 0, "source": "default"}))
        # Play's manual "Additional wait" specifies a stall on top of fetch.
        delay = Concrete(assumption["delay_ns"])
        state.q1_time_ns = add_values(state.q1_time_ns, delay)
        issue = next(event for event in state.events[event_start:] if event.kind == "q1_issue")
        # The receive stalls Q1 only. Already queued RT packets keep their
        # scheduled coordinates; subsequent packet issue times inherit the stall.
        available_at = add_values(issue.t0, delay)
        value = (Concrete(assumption["value"] & 0xFFFFFFFF) if assumption["value"] is not None
                 else Unknown(f"Assumed feedback receipt {feedback_id} has no supplied value"))
        destination = operand_binding.resolve_register_name(instruction.args[1], state)
        channel = interpreter._feedback_channel(instruction.args[0], operand_binding.operand_scope(runner.symbols, state))
        expression = f"assumed {instruction.op} channel {channel}"
        provenance = register_writes.make_register_provenance(
            state, destination, expression=expression, value=value,
            steps=[{**register_writes.provenance_step(instruction, expression, value),
                    "playback_feedback_id": feedback_id, "feedback_assumption": assumption}],
        )
        for index in range(pending_start, len(state.pending_register_writes)):
            pending = state.pending_register_writes[index]
            state.pending_register_writes[index] = replace(
                pending, value=value,
                provenance=register_writes.concrete_write_provenance(state, instruction, provenance, issue.id),
                meta={**pending.meta, "playback_feedback_id": feedback_id, "feedback_assumption": assumption},
            )
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            meta = {**event.meta, "playback_feedback_id": feedback_id, "feedback_assumption": assumption,
                    "feedback_available_at": value_to_json(available_at)}
            state.events[index] = replace(
                event, meta=meta, confidence="assumed",
                t0=event.t0 if event.kind == "q1_issue" else available_at,
                t1=state.q1_time_ns if event.kind == "q1_issue" else available_at,
                duration=add_values(event.duration, delay) if event.kind == "q1_issue" else Concrete(0),
            )
        runner.feedback_assumptions.append(feedback_id)
        self.feedback.append({
            "feedback_id": feedback_id, "event_id": issue.id, "sequencer_id": state.sequencer_id,
            "op": instruction.op, "source": timeline.source_to_json(instruction.source),
            "channel": channel, "register": destination, "step": self.steps,
            "value": assumption["value"], "delay_ns": assumption["delay_ns"],
            "assumption_source": assumption["source"], "assumed": True,
            "available_at": value_to_json(available_at), "completed_at": value_to_json(state.q1_time_ns),
        })

    def _feedback_overflow(self, runner: _Runner, instruction: Instr, entry: dict) -> bool:
        state = runner.state
        edge = Concrete(entry["time_ns"])
        for index in range(len(state.events) - 1, -1, -1):
            issue = state.events[index]
            if issue.kind != "q1_issue":
                continue
            if (isinstance(issue.t0, Concrete) and isinstance(issue.t1, Concrete)
                    and issue.t0.value <= edge.value < issue.t1.value):
                state.events[index] = replace(issue, t1=edge, duration=Concrete(edge.value - issue.t0.value), meta={
                    **issue.meta, "planned_q1_end_ns": issue.t1.value, "observation_cutoff_ns": edge.value,
                })
                state.q1_time_ns = edge
            break
        marker = timeline.emit_event(
            state, lane="rt.feedback", kind="feedback_rejected", t0=edge, t1=edge, duration=Concrete(0),
            label="Rejected LINQ arrival", confidence="assumed", source=instruction.source,
            meta={"id": entry["id"], "input_event_id": entry["event_id"], "capacity": self.feedback_queue_capacity,
                  "reason": "queue_full", "playback": True},
        )
        reason = f"LINQ feedback queue overflow at Q1 {entry['time_ns']} ns (capacity {self.feedback_queue_capacity})."
        category = "feedback_queue_overflow"
        display_reason = f"LINQ feedback queue overflow (capacity {self.feedback_queue_capacity})."
        state.metadata["execution"]["terminal"] = {
            "local_time_ns": edge.value, "category": category, "display_reason": display_reason, "core": "q1",
        }
        state.diagnostics.append(Diagnostic(
            severity="error", category=category, message=reason, source=instruction.source,
            related_events=[marker.id], details={"input_event_id": entry["event_id"], "display_message": display_reason},
        ))
        return self._block(runner, instruction, reason, error=True, emit_boundary=False)

    def _receive_scheduled_feedback(self, runner: _Runner, instruction: Instr, fifo: _ScheduledFeedback) -> bool:
        state = runner.state
        requested_id = None
        destinations = {"data": operand_binding.resolve_register_name(instruction.args[1], state)}
        if instruction.op == "fb_pop_data":
            requested = resolve_arg_value(instruction.args[0], operand_binding.operand_scope(runner.symbols, state))
            if not isinstance(requested, Concrete):
                return self._block(runner, instruction, "fb_pop_data requires a known LINQ ID for scheduled receipts.")
            requested_id = requested.value
            limitation = feedback_pop_limitation(requested_id)
            if limitation is not None:
                state.diagnostics.append(Diagnostic(severity="warning", category="analysis_incomplete",
                                                    message=limitation, source=instruction.source))
                return self._block(runner, instruction, limitation)
        else:
            destinations["id"] = operand_binding.resolve_register_name(instruction.args[0], state)
        armed = state.q1_time_ns.value
        receipt, discarded = take_feedback(fifo.pending, requested_id)
        while receipt is None and fifo.arrivals:
            overflow = fifo.advance(fifo.arrivals[0]["time_ns"])
            if overflow is not None:
                return self._feedback_overflow(runner, instruction, overflow)
            receipt, skipped = take_feedback(fifo.pending, requested_id)
            discarded.extend(skipped)
        feedback_id = f"{state.sequencer_id}:feedback:{state.q1_issue_index}"
        input_details = {"requested_id": requested_id, "armed_at_ns": armed, "destinations": destinations,
                         "discarded_input_event_ids": [entry["event_id"] for entry in discarded]}
        if receipt is None:
            requested = "a LINQ word" if requested_id is None else f"LINQ ID {requested_id}"
            reason = f"No scheduled arrival supplies {requested} for {state.sequencer_id}. Configure external inputs."
            input_details.update(status="waiting", reason=reason)
            runner.waiting = input_details
            state.metadata["execution"].update(status="waiting", reason=reason, pc=instruction.pc, feedback_input=input_details)
            event = timeline.emit_event(state, lane="rt.feedback", kind="feedback_pop", t0=state.q1_time_ns, t1=None,
                                     duration=Unknown(reason), label="Waiting for LINQ feedback", confidence="unknown",
                                     source=instruction.source, meta={"playback": True, "feedback_input": input_details})
            self.feedback.append({**input_details, "feedback_id": feedback_id, "event_id": event.id,
                                  "sequencer_id": state.sequencer_id, "op": instruction.op, "step": self.steps,
                                  "assumption_source": "scenario", "source": timeline.source_to_json(instruction.source)})
            return False

        event_start = len(state.events)
        pending_start = len(state.pending_register_writes)
        interpreter._execute_instruction(state, instruction, runner.symbols, {}, {}, allow_symbolic_operands=True)
        if state.metadata["execution"].get("status") in {"error", "blocked"}:
            execution = state.metadata["execution"]
            return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        state.q1_time_ns = Concrete(feedback_q1_completion(state.q1_time_ns.value, receipt["time_ns"]))
        issue = state.events[event_start]
        value = receipt["value"] & 0xFFFFFFFF
        available = max(armed, receipt["time_ns"])
        input_details.update(status="received", input_event_id=receipt["event_id"], id=receipt["id"], value=value,
                             arrival_ns=receipt["time_ns"], available_at_ns=available, completed_at_ns=state.q1_time_ns.value)
        write_roles = ("id", "data") if "id" in destinations else ("data",)
        for index, role in enumerate(write_roles, pending_start):
            pending = state.pending_register_writes[index]
            result = Concrete(receipt["id"] if role == "id" else value)
            expression = f"scheduled LINQ {role} from ID {receipt['id']}"
            provenance = register_writes.make_register_provenance(state, pending.register, expression=expression, value=result,
                steps=[{**register_writes.provenance_step(instruction, expression, result), "input_event_id": receipt["event_id"]}])
            provenance["input_event_id"] = receipt["event_id"]
            state.pending_register_writes[index] = replace(
                pending, value=result, provenance=register_writes.concrete_write_provenance(state, instruction, provenance, issue.id),
                meta={**pending.meta, "feedback_input": input_details, "input_event_id": receipt["event_id"]},
            )
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            meta = {**event.meta, "feedback_input": input_details, "playback_feedback_id": feedback_id}
            meta.pop("feedback", None)
            state.events[index] = replace(
                event, meta=meta, confidence="assumed",
                t0=event.t0 if event.kind == "q1_issue" else Concrete(available),
                t1=state.q1_time_ns if event.kind == "q1_issue" else Concrete(available),
                duration=Concrete(state.q1_time_ns.value - armed) if event.kind == "q1_issue" else Concrete(0),
            )
        runner.feedback_assumptions.append(feedback_id)
        runner.scheduled_feedback_gap = True
        self.assumption_references += len(discarded)
        self.feedback.append({**input_details, "feedback_id": feedback_id, "event_id": issue.id,
                              "sequencer_id": state.sequencer_id, "op": instruction.op, "step": self.steps,
                              "assumption_source": "scenario", "assumed": True, "delay_ns": available - armed,
                              "source": timeline.source_to_json(instruction.source)})
        return True

    def _branch(self, runner: _Runner, instruction: Instr) -> bool:
        state = runner.state
        _, invalid, diagnostic_start = q1_validation.validate_instruction(state, instruction, runner.symbols)
        failures = [item for item in state.diagnostics[diagnostic_start:] if item.severity == "error" or item.category == "register_not_ready"]
        if invalid or failures:
            return self._block(runner, instruction, failures[0].message if failures else "Invalid branch operands.", error=True)
        scope = operand_binding.operand_scope(runner.symbols, state)
        target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
        target_arg = instruction.args[target_index]
        target_value = resolve_arg_value(target_arg, scope)
        target = operand_binding.resolve_execution_target(state, target_value)
        conditional = instruction.op != "jmp"
        effects = q1_issue.resolved_core_issue(state, instruction, scope)
        value = None
        if effects is not None:
            natural = effects.taken
        elif instruction.op == "loop":
            previous = resolve_arg_value(instruction.args[0], scope)
            value = interpreter._wrap_concrete_classical_register_value(subtract_values(previous, Concrete(1)))
            natural = value.value != 0 if isinstance(value, Concrete) else None
        elif len(instruction.args) == 3:
            left, right = (resolve_arg_value(arg, scope) for arg in instruction.args[:2])
            natural = None
            if isinstance(left, Concrete) and isinstance(right, Concrete):
                a, b = left.value & CLASSICAL_REGISTER_MASK, right.value & CLASSICAL_REGISTER_MASK
                natural = a < b if instruction.op == "jlt" else a >= b
        else:
            natural = branch_on_flags(instruction.op, state.alu_flags) if conditional else True
        branch_id = f"{state.sequencer_id}:playback:{state.q1_issue_index}"
        choice = self.choices.get(branch_id) if conditional else None
        assumed = choice is not None or natural is None
        taken = choice == "taken" if choice is not None else natural if natural is not None else False
        if taken and target is None:
            return self._block(runner, instruction, q1_validation.invalid_branch_target_reason(state, target_value))
        origin = "user" if choice is not None else "default" if natural is None else "calculated"
        duration = (effects.q1_duration if effects is not None and choice is None else
                    branch_duration(q1_issue.branch_taken_duration(instruction, state, runner.symbols), taken))
        t0 = state.q1_time_ns
        t1 = add_values(t0, Concrete(duration))
        state.q1_time_ns = t1
        issue = q1_issue.emit_q1_issue(
            state, instruction, t0, t1, None,
            register_metadata=register_writes.register_issue_metadata(state, instruction, scope),
        )
        if effects is not None and target is not None:
            # The chosen arm sets the clock; its natural condition still sets the flags and counter.
            q1_issue.execute_concrete_branch(state, instruction, issue, runner.symbols, defer_register_write=True,
                                          branch_issue=replace(effects, q1_duration=duration), projected=True)
        elif instruction.op == "loop":
            register = operand_binding.resolve_register_name(instruction.args[0], state)
            if effects is not None:
                value = Concrete(effects.words[0])
                flags = effects.flags.as_dict()
            else:
                flags = sub_flags(previous.value, 1) if isinstance(previous, Concrete) else None
            register_writes.set_alu_flags(state, instruction, flags)
            provenance = register_writes.make_register_provenance(
                state, register, expression=f"{register} - 1", value=value,
                steps=[register_writes.provenance_step(instruction, f"{register} - 1", value)],
            )
            state.pending_register_writes.append(PendingRegisterWrite(
                register, value, register_writes.concrete_write_provenance(state, instruction, provenance, issue.id),
                instruction.source, state.q1_issue_index + 1 + register_write_delay_slots(instruction.op),
                issue.id, {"op": "loop"},
            ))
        elif len(instruction.args) == 3:
            if effects is not None:
                flags = effects.flags.as_dict()
            else:
                flags = sub_flags(left.value, right.value) if isinstance(left, Concrete) and isinstance(right, Concrete) else None
            register_writes.set_alu_flags(state, instruction, flags)
        state.next_pc, state.next_interior_pc = next_source_position(
            state.compiled_addresses, instruction, state.interior_pc, len(state.instructions_by_pc),
            taken=taken, target=target_value.value if isinstance(target_value, Concrete) else None,
        )
        source_branch_id = interpreter._branch_id(state, instruction, str(target_arg.value) if target_arg.kind == "label" else None, target_arg.raw)
        decision = "default" if origin == "default" else "assumed" if assumed else "concrete"
        metadata = {
            "playback_branch_id": branch_id, "branch_id": source_branch_id, "branch_taken": taken,
            "branch_decision": decision, **operand_binding.branch_target_metadata(target, state),
            "target_label": str(target_arg.value) if target_arg.kind == "label" else None,
        }
        if assumed:
            metadata.update(assumption_source=origin, assumed_branch_path="taken" if taken else "fallthrough")
            state.metadata.setdefault("branches", {}).setdefault("assumptions", []).append({
                "branch_id": branch_id, "assumed_branch_path": metadata["assumed_branch_path"], "assumption_source": origin,
            })
        issue.meta.update(metadata)
        if conditional:
            self.branches.append({
                "branch_id": branch_id, "source_branch_id": source_branch_id, "event_id": issue.id,
                "sequencer_id": state.sequencer_id, "op": instruction.op, "source": timeline.source_to_json(instruction.source),
                "target_label": metadata["target_label"], **operand_binding.branch_target_metadata(target, state),
                "calculated_taken": natural, "path": "taken" if taken else "fallthrough",
                "assumed": assumed, "assumption_source": origin, "step": self.steps,
            })
        return True

    def snapshot(self) -> dict:
        states = []
        loops = []
        for runner in self.runners:
            state = runner.state
            summaries = interpreter._observed_loop_events(state, runner.trace, mode="playback")
            events = [*state.events, *summaries]
            loops.extend({"sequencer_id": state.sequencer_id, **event.meta} for event in summaries)
            metadata = dict(state.metadata)
            metadata.update(self.sequencer_metadata.get(state.sequencer_id, {}))
            metadata["hardware_parameters"] = dict(self.hardware_parameters.get(state.sequencer_id, {}))
            metadata.update(self.acquisition_context.get(state.sequencer_id, {}))
            names = self.sequence_names.get(state.sequencer_id)
            if names is not None:
                metadata["weight_lengths"] = names.weight_lengths
            metadata["execution"] = {**metadata["execution"]}
            if not state.stopped and metadata["execution"]["status"] == "paused":
                metadata["execution"]["status"] = "partial" if self.status in {"blocked", "error"} else self.status
            states.append(replace(state, events=[replace(event, meta=dict(event.meta)) for event in events],
                                  diagnostics=list(state.diagnostics), metadata=metadata))
        annotate_playback_queue_timing(states, trigger_schedules={runner.state.sequencer_id: runner.trigger_counters.schedule
                                                                 for runner in self.runners})
        align_timelines(states, mode=self.alignment_mode, anchor_kinds=self.alignment_anchor_kinds)
        ir = timeline_ir_from_states(states, project=self.project)
        for sequencer in ir["sequencers"]:
            sequencer.update(self.sequencer_metadata.get(sequencer["id"], {}))
        if self.parameters:
            ir["analysis"]["parameters"] = self.parameters
        ir["playback"] = {**self.summary(), "branches": list(self.branches), "feedback": list(self.feedback), "loops": loops,
                          "triggers": deepcopy(self.triggers), "external_inputs": {
                              "triggers": deepcopy(self.trigger_sources),
                              "overridden": self.external_inputs != self.base_external_inputs,
                              "timing_model": "receiver arrivals on the shared unaligned analytical RT clock; wait_trigger resumes after arrival plus duration",
                          }}
        if self.feedback_sources:
            ir["playback"]["external_inputs"].update(
                feedback=deepcopy(self.feedback_sources), feedback_queue_capacity=self.feedback_queue_capacity,
                feedback_timing_model="LINQ arrivals on each receiver's unaligned analytical Q1 clock",
            )
        if self.status != "completed" or self.feedback:
            ir["diagnostics"] = [item for item in ir["diagnostics"] if item["category"] != "feedback_fifo_imbalance"]
            ir["feedback_balance"] = {"status": "incomplete" if self.status != "completed" else "assumed", "channels": {}}
            if self.feedback:
                ir["feedback_balance"]["reason"] = "Playback feedback receipts are assumed external inputs; FIFO balance is not verified."
        return ir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--set-param", action="append", default=[])
    args = parser.parse_args(argv)
    from q1timeline.cli import _parse_param_overrides

    try:
        session = PlaybackSession.from_project(args.project, params=_parse_param_overrides(args.set_param))
    except (OSError, ValueError, ConfigLoadError) as exc:
        print(json.dumps({"id": None, "error": str(exc)}), flush=True)
        return 2
    for line in sys.stdin:
        request: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Playback command must be an object")
            command = request.get("command")
            if command == "run":
                result = {"playback": session.run(budget_ms=request.get("budget_ms", 100), max_steps=request.get("max_steps", 1000))}
            elif command in {"snapshot", "pause"}:
                result = {"ir": session.snapshot()}
            elif command == "choose":
                result = {"ir": session.choose(request.get("branch_id"), request.get("path"))}
            elif command == "set_feedback":
                result = {"ir": session.set_feedback(request.get("feedback_id"), request.get("value"), request.get("delay_ns"))}
            elif command == "set_external_inputs":
                result = {"ir": session.set_external_inputs(request.get("external_inputs"))}
            elif command == "accept_external_inputs":
                result = {"ir": session.accept_external_inputs()}
            else:
                raise ValueError(f"Unknown playback command: {command}")
            result["id"] = request.get("id")
        except (ValueError, TypeError, KeyError) as exc:
            result = {"id": request.get("id") if isinstance(request, dict) else None, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
