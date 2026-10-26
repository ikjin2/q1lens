"""Resumable, bounded execution of one analytical path over JSON lines.

This is not hardware simulation: feedback receipt values and Q1 waiting times
are explicit assumptions; unknown branches choose an assumed fallthrough path.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, replace
from copy import deepcopy
from pathlib import Path
from typing import Any

from q1timeline.analysis import interpreter as core
from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.playback_loops import ExecutedStep, observed_loops
from q1timeline.analysis.playback_timing import annotate_playback_queue_timing
from q1timeline.analysis.values import Concrete, Unknown, add_values, resolve_arg_value, value_to_json
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.external_inputs import resolve_trigger_sources, validate_external_inputs
from q1timeline.project import ConfigLoadError, load_project_config, load_single_file_config
from q1timeline.q1asm.ast import Instr
from q1timeline.q1asm.instruction_table import get_instruction_spec, rt_duration_arg_for_instruction
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.sequence import load_sequence_names


MAX_TRACE_EVENTS = 20_000
MAX_ASSUMPTION_REFERENCES = 100_000
_EXTERNAL_TIMING = {"fb_pull_data"}


@dataclass
class _Runner:
    state: core.AnalysisState
    symbols: dict
    trace: list[ExecutedStep] = field(default_factory=list)
    feedback_assumptions: list[str] = field(default_factory=list)
    trigger_assumptions: list[str] = field(default_factory=list)
    waiting: dict | None = None


class PlaybackSession:
    def __init__(self, documents: dict[str, tuple[str, str]], *, params: dict | None = None,
                 alignment_mode: str = "none", alignment_anchor_kinds: tuple[str, ...] = (),
                 project: dict | None = None, external_inputs: dict | None = None):
        self.documents = dict(documents)
        self.params = dict(params or {})
        self.external_inputs = validate_external_inputs(external_inputs, documents)
        self.base_external_inputs = deepcopy(self.external_inputs)
        self.trigger_sources = resolve_trigger_sources(self.external_inputs, self.params)
        self.alignment_mode = alignment_mode
        self.alignment_anchor_kinds = alignment_anchor_kinds
        self.project = project or {}
        self.sequencer_metadata: dict[str, dict] = {}
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
            project={"root": str(config.root), "alignment_mode": config.alignment_mode,
                     "alignment_anchor_kinds": list(config.alignment_anchor_kinds),
                     "display": config.display, "display_file": str(config.display_file) if config.display_file else None},
        )
        session.sequencer_metadata = {seq.id: {"module": seq.module} for seq in config.sequencers if seq.module}
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
        for seq, program in self.programs.items():
            state = core.AnalysisState(
                sequencer_id=seq, labels=program.labels,
                instructions_by_pc={item.pc: item for item in program.instructions},
                register_aliases=core._register_aliases(program.defs), execution_mode="concrete",
            )
            state.diagnostics.extend(program.diagnostics)
            symbols = core.symbol_table_from_params(self.params)
            defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
            resolved = core.resolve_def_values(defs, symbols, state.diagnostics, labels=program.labels)
            state.symbol_origins = core._symbol_origins(self.params, symbols, defs, resolved)
            symbols.update(resolved)
            core._diagnose_program(state, symbols, program)
            state.has_expanded_instructions = any(
                item.op in {"loop", "jlt"} or (item.op == "jge" and len(item.args) == 3)
                for item in program.instructions
            )
            state.metadata["execution"] = {
                "mode": "playback", "status": "paused", "steps": 0,
                "timing_model": "analytical", "address_model": "source_instruction_index",
                "assumptions": ["One executed path; feedback receipts are assumed and queue backpressure is not simulated.",
                                "External trigger times are assumed receiver arrivals on the unaligned analytical RT clock; hardware transport latency is not modeled."],
            }
            self.runners.append(_Runner(state, symbols))
            errors = [item for item in state.diagnostics if item.severity == "error"]
            if errors:
                self.status, self.reason = "error", errors[0].message

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
            if all(runner.state.stopped for runner in self.runners):
                self.status = "completed"
                break
            if all(runner.state.stopped or runner.waiting is not None for runner in self.runners):
                self.status = "waiting"
                waiters = [r for r in self.runners if r.waiting is not None]
                reasons = dict.fromkeys(r.waiting["reason"] for r in waiters)
                self.reason = f"{len(waiters)} sequencer(s) waiting. " + "; ".join(reasons)
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
            self.status = "completed" if all(r.state.stopped for r in self.runners) else "paused"
        return self.summary()

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

    def _block(self, runner: _Runner, instruction: Instr, reason: str, *, error: bool = False) -> bool:
        self.status, self.reason = ("error" if error else "blocked"), reason
        state = runner.state
        state.metadata["execution"].update(status=self.status, reason=reason, pc=instruction.pc)
        core._emit_event(
            state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns,
            t1=None, duration=Unknown(reason), label=reason, confidence="unknown", source=instruction.source,
            meta={"reason": "playback_boundary", "playback": True},
        )
        return False

    def _step(self, runner: _Runner) -> bool:
        state = runner.state
        history_start = len(state.register_history)
        core._apply_ready_register_writes(state)
        instruction = state.instructions_by_pc.get(state.pc)
        if instruction is None:
            self.status, self.reason = "error", "Execution reached a PC without an instruction or stop."
            return False
        spec = get_instruction_spec(instruction.op)
        validation = spec.validate(instruction)
        invalid = [item for item in validation if item.severity == "error"]
        if invalid:
            state.diagnostics.extend(validation)
            return self._block(runner, instruction, invalid[0].message, error=True)
        condition_disabled = core._set_cond_is_disabled(state, instruction, runner.symbols)
        if instruction.op == "set_cond" and not condition_disabled:
            spec, has_invalid_arguments, diagnostic_start = core._validate_instruction(state, instruction, runner.symbols)
            if not core._concrete_instruction_ready(
                state, instruction, runner.symbols, spec, has_invalid_arguments, diagnostic_start,
                supported_ops=core._CONCRETE_OPS | {"set_cond"}, allow_symbolic_operands=True,
            ):
                execution = state.metadata["execution"]
                return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
            return self._block(runner, instruction, "set_cond requires trigger-counter inputs at RT execution time.")
        if (spec.category == "unknown" or instruction.op in _EXTERNAL_TIMING
                or (spec.timing_effect_status == "analysis_incomplete" and not condition_disabled)):
            return self._block(runner, instruction, f"{instruction.op} requires unavailable execution or external timing semantics.")
        scope = core._scope(runner.symbols, state)
        duration_index = rt_duration_arg_for_instruction(spec, instruction)
        if instruction.op != "wait_trigger" and duration_index is not None and duration_index < len(instruction.args):
            if not isinstance(resolve_arg_value(instruction.args[duration_index], scope), Concrete):
                return self._block(runner, instruction, f"{instruction.op} duration is unknown.")
        if state.has_expanded_instructions:
            target_index = (1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0) if spec.category == "branch" else None
            if (target_index is not None and instruction.args[target_index].kind != "label") or any(
                arg.kind == "label" and index != target_index for index, arg in enumerate(instruction.args)
            ):
                return self._block(runner, instruction, "Compiled instruction addresses are unknown for this expanded program.")
        event_start = len(state.events)
        rt_start = state.rt_time_ns
        continuation = instruction.op == "jmp" and any(
            isinstance(value, Concrete) and value.value == instruction.pc + 1
            for value in [*state.registers.values(), *(write.value for write in state.pending_register_writes)]
        )
        state.next_pc = instruction.pc + 1
        state.current_resolved_args = core._resolved_args_for_instruction(instruction, scope, state)
        diagnostic_start = len(state.diagnostics)
        flags_before = dict(state.alu_flags) if state.alu_flags is not None else None
        try:
            if instruction.op == "wait_trigger":
                if not self._receive_trigger(runner, instruction):
                    return False
            elif instruction.op == "fb_pop_data":
                self._receive_feedback(runner, instruction)
            elif spec.category == "branch":
                if not self._branch(runner, instruction):
                    return False
            else:
                names = self.sequence_names.get(state.sequencer_id)
                core._execute_instruction(state, instruction, runner.symbols,
                                          names.waveforms if names else {}, names.acquisitions if names else {},
                                          allow_symbolic_operands=True)
        finally:
            state.current_resolved_args = []
        execution_status = state.metadata["execution"].get("status")
        if execution_status in {"error", "blocked"}:
            return self._block(runner, instruction, state.metadata["execution"]["reason"], error=execution_status == "error")
        branch_taken = False
        branch_target = None
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            event.meta.update(playback=True, execution_index=state.q1_issue_index, source_pc=instruction.pc)
            if event.kind == "q1_issue":
                event.meta.update(flags_before=flags_before, flags_after=dict(state.alu_flags) if state.alu_flags is not None else None)
                branch_taken = bool(event.meta.get("branch_taken"))
                branch_target = event.meta.get("target_pc")
            if runner.feedback_assumptions:
                event.meta["feedback_assumptions"] = list(runner.feedback_assumptions)
                self.assumption_references += len(runner.feedback_assumptions)
            if runner.trigger_assumptions:
                event.meta["trigger_assumptions"] = list(runner.trigger_assumptions)
                self.assumption_references += len(runner.trigger_assumptions)
            if (state.metadata.get("branches", {}).get("assumptions") or runner.feedback_assumptions or runner.trigger_assumptions) and event.confidence == "exact":
                state.events[index] = replace(event, confidence="assumed")
        # Unknown values may propagate; invalid instructions never become executed steps.
        if any(item.severity == "error" for item in state.diagnostics[diagnostic_start:]):
            return self._block(runner, instruction, "Instruction validation failed.", error=True)
        runner.trace.append(ExecutedStep(
            instruction.pc, None if state.stopped else state.next_pc,
            event_start, len(state.events), state.q1_issue_index, rt_start, state.rt_time_ns,
            branch_taken, branch_target, instruction.op == "jmp",
            instruction.op == "jmp" and core._register_name(instruction.args[0], state) is not None,
            continuation,
            instruction.op in {"cmp", "test", "nop"} or (spec.category == "branch" and instruction.op != "loop"),
        ))
        state.executed_pcs.add(instruction.pc)
        state.q1_issue_index += 1
        state.metadata["execution"]["steps"] = state.q1_issue_index
        self.steps += 1
        state.pc = state.next_pc
        if state.stopped:
            core._apply_ready_register_writes(state)
            state.metadata["execution"]["status"] = "completed"
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

    def _receive_trigger(self, runner: _Runner, instruction: Instr) -> bool:
        state = runner.state
        spec, invalid, diagnostic_start = core._validate_instruction(state, instruction, runner.symbols)
        if not core._concrete_instruction_ready(
            state, instruction, runner.symbols, spec, invalid, diagnostic_start,
            supported_ops=core._CONCRETE_OPS | {"wait_trigger"}, allow_symbolic_operands=True,
        ):
            execution = state.metadata["execution"]
            return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        address, duration = [resolve_arg_value(arg, core._scope(runner.symbols, state)) for arg in instruction.args]
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
            core._emit_event(state, lane="rt.sync", kind="wait_trigger", t0=state.rt_time_ns,
                             t1=None, duration=Unknown(assumption["reason"]), label="Waiting for external trigger",
                             confidence="unknown", source=instruction.source,
                             meta={"playback": True, "trigger_assumption": assumption})
            return False
        arrival, event_id, source_id = min(candidates)
        assumption.update(source_id=source_id, event_id=event_id, arrival_ns=arrival,
                          resume_ns=arrival + duration.value, status="received")
        event_start = len(state.events)
        core._execute_instruction(state, instruction, runner.symbols, {}, {}, allow_symbolic_operands=True)
        if state.metadata["execution"].get("status") in {"error", "blocked"}:
            execution = state.metadata["execution"]
            return self._block(runner, instruction, execution["reason"], error=execution["status"] == "error")
        end, elapsed = Concrete(assumption["resume_ns"]), Concrete(assumption["resume_ns"] - armed)
        state.rt_time_ns = end
        packet = state.rt_packets[-1]
        state.rt_packets[-1] = replace(packet, rt_t1=end, duration=elapsed,
                                       meta={**packet.meta, "trigger_assumption": assumption})
        for index in range(event_start, len(state.events)):
            event = state.events[index]
            event.meta["trigger_assumption"] = assumption
            if event.kind == "wait_trigger":
                state.events[index] = replace(event, t1=end, duration=elapsed, confidence="assumed")
        runner.trigger_assumptions.append(event_id)
        self.triggers.append({**assumption, "sequencer_id": state.sequencer_id})
        return True

    def _receive_feedback(self, runner: _Runner, instruction: Instr) -> None:
        state = runner.state
        event_start = len(state.events)
        pending_start = len(state.pending_register_writes)
        core._execute_instruction(state, instruction, runner.symbols, {}, {}, allow_symbolic_operands=True)
        if state.metadata["execution"].get("status") in {"error", "blocked"}:
            return
        feedback_id = f"{state.sequencer_id}:feedback:{state.q1_issue_index}"
        assumption = dict(self.feedback_choices.get(feedback_id, {"value": None, "delay_ns": 0, "source": "default"}))
        delay = Concrete(assumption["delay_ns"])
        state.q1_time_ns = add_values(state.q1_time_ns, delay)
        issue = next(event for event in state.events[event_start:] if event.kind == "q1_issue")
        # The receive stalls Q1 only. Already queued RT packets keep their
        # scheduled coordinates; subsequent packet issue times inherit the stall.
        available_at = add_values(issue.t0, delay)
        value = (Concrete(assumption["value"] & 0xFFFFFFFF) if assumption["value"] is not None
                 else Unknown(f"Assumed feedback receipt {feedback_id} has no supplied value"))
        destination = core._register_name(instruction.args[1], state)
        channel = core._feedback_channel(instruction.args[0], core._scope(runner.symbols, state))
        expression = f"assumed {instruction.op} channel {channel}"
        provenance = core._register_provenance(
            state, destination, expression=expression, value=value,
            steps=[{**core._provenance_step(instruction, expression, value),
                    "playback_feedback_id": feedback_id, "feedback_assumption": assumption}],
        )
        for index in range(pending_start, len(state.pending_register_writes)):
            pending = state.pending_register_writes[index]
            state.pending_register_writes[index] = replace(
                pending, value=value,
                provenance=core._concrete_write_provenance(state, instruction, provenance, issue.id),
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
            "op": instruction.op, "source": core._source_to_json(instruction.source),
            "channel": channel, "register": destination, "step": self.steps,
            "value": assumption["value"], "delay_ns": assumption["delay_ns"],
            "assumption_source": assumption["source"], "assumed": True,
            "available_at": value_to_json(available_at), "completed_at": value_to_json(state.q1_time_ns),
        })

    def _branch(self, runner: _Runner, instruction: Instr) -> bool:
        state = runner.state
        _, invalid, diagnostic_start = core._validate_instruction(state, instruction, runner.symbols)
        failures = [item for item in state.diagnostics[diagnostic_start:] if item.severity == "error" or item.category == "register_not_ready"]
        if invalid or failures:
            return self._block(runner, instruction, failures[0].message if failures else "Invalid branch operands.", error=True)
        scope = core._scope(runner.symbols, state)
        target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
        target_arg = instruction.args[target_index]
        target = resolve_arg_value(target_arg, scope)
        target_pc = target.value if isinstance(target, Concrete) else None
        conditional = instruction.op != "jmp"
        value = None
        if instruction.op == "loop":
            previous = resolve_arg_value(instruction.args[0], scope)
            value = core._wrap_concrete_classical_register_value(core.subtract_values(previous, Concrete(1)))
            natural = value.value != 0 if isinstance(value, Concrete) else None
        elif len(instruction.args) == 3:
            left, right = (resolve_arg_value(arg, scope) for arg in instruction.args[:2])
            natural = None
            if isinstance(left, Concrete) and isinstance(right, Concrete):
                a, b = left.value & core._CLASSICAL_REGISTER_MASK, right.value & core._CLASSICAL_REGISTER_MASK
                natural = a < b if instruction.op == "jlt" else a >= b
        else:
            natural = core._status_branch_decision(instruction.op, state.alu_flags) if conditional else True
        branch_id = f"{state.sequencer_id}:playback:{state.q1_issue_index}"
        choice = self.choices.get(branch_id) if conditional else None
        assumed = choice is not None or natural is None
        taken = choice == "taken" if choice is not None else natural if natural is not None else False
        if taken and target_pc not in state.instructions_by_pc:
            return self._block(runner, instruction, "Branch destination is unknown or invalid.")
        origin = "user" if choice is not None else "default" if natural is None else "calculated"
        duration = (24 if instruction.op in {"loop", "jlt", "jge"} else 16) if taken else 4
        t0 = state.q1_time_ns
        t1 = add_values(t0, Concrete(duration))
        state.q1_time_ns = t1
        issue = core._emit_q1_issue(
            state, instruction, t0, t1, None,
            register_metadata=core._register_issue_metadata(state, instruction, scope),
        )
        if natural is not None and target_pc is not None:
            # Keep arithmetic, status flags and loop counter writes identical to concrete execution.
            core._execute_concrete_branch(state, instruction, issue, runner.symbols, defer_register_write=True)
            issue = core._revise_q1_issue_duration(state, issue, duration)
        elif instruction.op == "loop":
            register = core._register_name(instruction.args[0], state)
            core._set_alu_flags(state, instruction, core._sub_flags(previous.value, 1) if isinstance(previous, Concrete) else None)
            provenance = core._register_provenance(
                state, register, expression=f"{register} - 1", value=value,
                steps=[core._provenance_step(instruction, f"{register} - 1", value)],
            )
            state.pending_register_writes.append(core.PendingRegisterWrite(
                register, value, core._concrete_write_provenance(state, instruction, provenance, issue.id),
                instruction.source, state.q1_issue_index + 1, issue.id, {"op": "loop"},
            ))
        elif len(instruction.args) == 3:
            core._set_alu_flags(state, instruction, core._sub_flags(left.value, right.value)
                                if isinstance(left, Concrete) and isinstance(right, Concrete) else None)
        state.next_pc = target_pc if taken else instruction.pc + 1
        source_branch_id = core._branch_id(state, instruction, str(target_arg.value) if target_arg.kind == "label" else None, target_arg.raw)
        decision = "default" if origin == "default" else "assumed" if assumed else "concrete"
        metadata = {
            "playback_branch_id": branch_id, "branch_id": source_branch_id, "branch_taken": taken,
            "branch_decision": decision, "target_pc": target_pc,
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
                "sequencer_id": state.sequencer_id, "op": instruction.op, "source": core._source_to_json(instruction.source),
                "target_label": metadata["target_label"], "target_pc": target_pc,
                "calculated_taken": natural, "path": "taken" if taken else "fallthrough",
                "assumed": assumed, "assumption_source": origin, "step": self.steps,
            })
        return True

    def snapshot(self) -> dict:
        states = []
        loops = []
        for runner in self.runners:
            state = runner.state
            observed = observed_loops(state.sequencer_id, runner.trace, stopped=state.stopped)
            events = list(state.events)
            loop_summaries = {}
            for loop in observed:
                first, last = loop["ranges"][0], loop["ranges"][-1]
                members = [event.id for event in state.events[first[0]:last[1]]]
                first_ids = [event.id for event in state.events[first[0]:first[1]]]
                durations = [core.subtract_values(end, start) for _, _, start, end in loop["ranges"]]
                period = durations[0] if all(item == durations[0] for item in durations) else Unknown("Observed iteration durations differ")
                count = len(loop["ranges"])
                start_source = state.instructions_by_pc[loop["start_pc"]].source
                end_source = state.instructions_by_pc[loop["end_pc"]].source
                meta = {
                    "loop_id": loop["loop_id"], "playback": True,
                    "count": count if loop["complete"] else f">={count}", "completed_iterations": count,
                    "complete": loop["complete"], "period": value_to_json(period),
                    "first_iteration_end": value_to_json(first[3]),
                    "first_iteration_event_ids": first_ids, "playback_event_ids": members,
                    "hidden_event_ids": members[len(first_ids):], "loop_preview_key": loop["loop_id"],
                    "shown_iterations": [0], "visible_iteration_count": 1,
                    "source_start": core._source_to_json(start_source), "source_end": core._source_to_json(end_source),
                }
                loop_summaries[loop["loop_id"]] = meta
                events.append(core.TimelineEvent(
                    loop["loop_id"], state.sequencer_id, "rt.loop", "loop_block", first[2], last[3],
                    core.subtract_values(last[3], first[2]), f"loop x{meta['count']}",
                    "assumed" if any(event.confidence == "assumed" for event in state.events[first[0]:last[1]]) else "exact", start_source, meta,
                ))
                loops.append({"sequencer_id": state.sequencer_id, **meta})
            parents: list[dict] = []
            for loop in observed:
                start, end = loop["ranges"][0][0], loop["ranges"][-1][1]
                while parents and end > parents[-1]["ranges"][-1][1]:
                    parents.pop()
                for parent in parents:
                    if start >= parent["ranges"][0][1]:
                        loop_summaries[parent["loop_id"]]["hidden_event_ids"].append(loop["loop_id"])
                parents.append(loop)
            metadata = dict(state.metadata)
            metadata["execution"] = {**metadata["execution"]}
            if not state.stopped and metadata["execution"]["status"] == "paused":
                metadata["execution"]["status"] = self.status
            states.append(replace(state, events=[replace(event, meta=dict(event.meta)) for event in events],
                                  diagnostics=list(state.diagnostics), metadata=metadata))
        annotate_playback_queue_timing(states)
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
