"""Bounded, deterministic digital simulation using Q1Lens instruction semantics.

Queue and synchronization rules follow the Qblox sequencer/Q1ASM documentation.
Startup delay, link latency, trigger thresholds and analog inputs are explicit
scenario assumptions, not hardware calibration or a firmware compatibility claim.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import heapq
from typing import Any

from q1timeline.analysis import interpreter as core
from q1timeline.analysis.values import Concrete, Unknown, resolve_arg_value, resolve_def_values, symbol_table_from_params, value_to_json
from q1timeline.q1asm.ast import Arg, Instr, Program, SourceLocation
from q1timeline.q1asm.instruction_table import get_instruction_spec, rt_duration_arg_for_instruction


_SUPPORTED = core._CONCRETE_OPS | {
    "set_cond", "wait_trigger", "set_latch_en", "latch_rst", "fb_com_data",
    "fb_pop_data", "fb_pull_data", "fb_acq_tb_id", "fb_acq_iq_id",
    "fb_acq_tb_valid", "fb_acq_iq_shift", "acquire_weighed", "acquire_weighted",
    "mulu16", "muls16",
}


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


@dataclass
class _Sequencer:
    state: core.AnalysisState
    symbols: dict
    queue: deque = field(default_factory=deque)
    reserved: int = 0
    q1_done: bool = False
    rt_done: bool = False
    failed: bool = False
    waiting: str | None = None
    waiting_since: int = 0
    feedback: list[dict] = field(default_factory=list)
    trigger_tokens: dict[int, deque] = field(default_factory=dict)
    trigger_counts: dict[int, int] = field(default_factory=dict)
    latch_enabled: bool = False
    condition: tuple[int, int, int, int] = (0, 0, 0, 4)
    rt_settings: dict[str, Any] = field(default_factory=dict)
    rt_wait: tuple | None = None
    phase_reset: bool = False
    acquisition: dict | None = None
    acquisitions: list[dict] = field(default_factory=list)
    tb_id: int = 0
    iq_id: int = 0
    tb_valid: int = 1
    iq_shift: int = 0
    setter_ids: dict[str, str] = field(default_factory=dict)


def simulate_programs(
    programs: dict[str, Program], *, params: dict | None = None, max_steps: int = 10000,
    simulation: dict | None = None, sequences: dict[str, dict] | None = None,
    strict_q1asm: bool = False,
) -> list[core.AnalysisState]:
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
    validate_params_json_mapping(dict(params or {}))
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    config = validate_simulation(simulation, sequencer_ids=set(programs))
    memories = {name: (sequences or {}).get(name, {}) for name in programs}
    signals = SignalModel(config["physical"], memories) if config["physical"] else None
    engine = _Runtime(programs, params or {}, max_steps, config, signals, strict_q1asm)
    return engine.run()


class _Runtime:
    def __init__(self, programs: dict[str, Program], params: dict, max_steps: int, config: dict, signals: Any, strict_q1asm: bool):
        self.config, self.signals, self.max_steps = config, signals, max_steps
        self.capacity = config["queue_capacity"]
        self.feedback_capacity = config["feedback_queue_capacity"]
        self.startup = config["startup_delay_ns"]
        self.max_time = config["max_time_ns"]
        self.now = 0
        self.serial = 0
        self.events: list[tuple] = []
        self.sequencers: dict[str, _Sequencer] = {}
        self.sync_waiters: dict[str, tuple] = {}
        self.used_acquisition_inputs: set[int] = set()
        for name, program in programs.items():
            state = core.AnalysisState(
                sequencer_id=name, labels=program.labels,
                instructions_by_pc={instruction.pc: instruction for instruction in program.instructions},
                register_aliases=core._register_aliases(program.defs), execution_mode="concrete",
                strict_q1asm=strict_q1asm,
            )
            state.diagnostics.extend(program.diagnostics)
            if strict_q1asm:
                core._diagnose_strict_q1asm_placeholder_defs(state, program.defs)
            symbols = symbol_table_from_params(params)
            defs = {key: arg for key, arg in program.defs.items() if arg.kind != "reg"}
            resolved = resolve_def_values(defs, symbols, state.diagnostics)
            state.symbol_origins = core._symbol_origins(params, symbols, defs, resolved)
            symbols.update(resolved)
            state.has_expanded_instructions = any(
                instruction.op in {"loop", "jlt"} or (instruction.op == "jge" and len(instruction.args) == 3)
                for instruction in program.instructions
            )
            state.metadata["execution"] = {
                "mode": "simulation", "status": "completed", "steps": 0, "max_steps": max_steps,
                "timing_model": "event_driven", "address_model": "source_instruction_index",
                "assumptions": [
                    f"RT startup delay {self.startup} ns is a scenario parameter, not measured hardware latency.",
                    "Same-time events run in input, enqueue/completion, RT, then Q1 order; remaining ties use scheduling order.",
                    "All supplied sequencers participate in wait_sync; synchronization transport latency is zero.",
                    "Trigger counters start disabled, use threshold one without inversion, and require set_latch_en.",
                    "wait_trigger accepts scenario arrivals at or after its RT start; earlier arrivals only affect enabled counters.",
                    "PC values index source instructions; assembler instruction expansion is not modeled.",
                ],
            }
            seq = self.sequencers[name] = _Sequencer(state, symbols)
            errors = [item for item in state.diagnostics if item.severity == "error" and item.category != "unresolved_symbol"]
            if errors:
                self.fail(seq, "error", errors[0].message)
            else:
                self.schedule(0, 3, name, "q1")
                self.schedule(self.startup, 2, name, "rt")
        for kind in ("feedback", "triggers"):
            for index, item in enumerate(config.get("inputs", {}).get(kind, [])):
                self.schedule(item["time_ns"], 0, item["target"], kind, {**item, "input_index": index})

    def schedule(self, time: int, phase: int, name: str, action: str, data: Any = None) -> None:
        self.serial += 1
        heapq.heappush(self.events, (time, phase, self.serial, name, action, data))

    def source(self, seq: _Sequencer) -> SourceLocation:
        instruction = seq.state.instructions_by_pc.get(seq.state.pc)
        if instruction is None and seq.state.instructions_by_pc:
            instruction = next(reversed(seq.state.instructions_by_pc.values()))
        return instruction.source if instruction else SourceLocation("<simulation>", 1, 1, "")

    def emit(self, seq: _Sequencer, kind: str, time: int, *, end: int | None = None,
             instruction: Instr | None = None, meta: dict | None = None, lane: str = "rt.simulation") -> core.TimelineEvent:
        end = time if end is None else end
        return core._emit_event(
            seq.state, lane=lane, kind=kind, t0=Concrete(time), t1=Concrete(end),
            duration=Concrete(end-time), label=kind.replace("_", " "), confidence="exact",
            source=instruction.source if instruction else self.source(seq), meta=meta,
        )

    def fail(self, seq: _Sequencer, status: str, reason: str, instruction: Instr | None = None, *, category: str | None = None) -> None:
        if seq.failed:
            return
        self.close_waits(seq)
        core._end_concrete_execution(seq.state, status, reason, instruction)
        diagnostic = seq.state.diagnostics[-1]
        seq.state.diagnostics[-1] = replace(diagnostic, category=category or diagnostic.category,
                                             source=diagnostic.source or self.source(seq))
        seq.failed = True

    def close_waits(self, seq: _Sequencer) -> None:
        if seq.waiting is not None:
            self.emit(seq, "q1_stall", seq.waiting_since, end=self.now,
                      meta={"reason": seq.waiting, "pending": True}, lane="debug.q1_stall")
            seq.waiting = None
        if seq.rt_wait is not None:
            kind, command, started = seq.rt_wait
            self.emit(seq, kind, started, end=self.now, instruction=command.instruction,
                      meta={"pending": True, "rt_packet_id": seq.state.rt_packets[command.packet_index].id})
            seq.rt_wait = None
            self.sync_waiters.pop(seq.state.sequencer_id, None)

    def run(self) -> list[core.AnalysisState]:
        while self.events:
            time, _phase, _serial, name, action, data = heapq.heappop(self.events)
            seq = self.sequencers[name]
            if seq.failed or (seq.q1_done and seq.rt_done and seq.acquisition is None):
                continue
            if time > self.max_time:
                self.now = self.max_time
                for active in self.sequencers.values():
                    if not active.failed and not (active.q1_done and active.rt_done and active.acquisition is None):
                        self.fail(active, "step_limit", f"Reached simulation time limit {self.max_time} ns.", category="simulation_time_limit")
                break
            self.now = time
            if action == "q1":
                self.q1(seq)
            elif action == "enqueue":
                seq.reserved -= 1
                seq.queue.append(data)
                self.emit(seq, "rt_queue_enqueue", time, instruction=data.instruction, meta={
                    "depth": len(seq.queue), "capacity": self.capacity,
                    "rt_packet_id": seq.state.rt_packets[data.packet_index].id,
                }, lane="debug.rt_queue")
            elif action == "q1_stop":
                seq.q1_done = True
            elif action == "register_ready":
                if data in seq.state.pending_register_writes:
                    seq.state.pending_register_writes.remove(data)
                    core._apply_register_write(seq.state, data, time=Concrete(time))
            elif action == "rt":
                self.rt(seq)
            elif action == "feedback":
                self.feedback(seq, data)
            elif action == "triggers":
                self.trigger(seq, data)
            elif action == "acquisition":
                if seq.acquisition is data:
                    self.finish_acquisition(seq, data)
        for seq in self.sequencers.values():
            if not seq.failed and not (seq.q1_done and seq.rt_done and seq.acquisition is None):
                reason = "No scheduled input can resume " + (seq.waiting or (seq.rt_wait[0] if seq.rt_wait else "execution")) + "."
                self.fail(seq, "blocked", reason)
        try:
            signals = self.signals.export(self.now) if self.signals is not None else {}
        except ValueError as error:
            signals = {}
            for seq in self.sequencers.values():
                self.fail(seq, "error", f"Signal model: {error}")
        for name, seq in self.sequencers.items():
            seq.state.stopped = True
            seq.state.metadata["simulation"] = {
                "queue_capacity": self.capacity, "feedback_queue_capacity": self.feedback_capacity,
                "startup_delay_ns": self.startup, "max_time_ns": self.max_time,
                "routes": self.config.get("routes", []), "trigger_routes": self.config.get("trigger_routes", []),
                "acquisition_iq_scale": self.config.get("acquisition_iq_scale", {}),
                "end_time_ns": self.now, "acquisitions": seq.acquisitions, "signals": signals.get(name, {}),
                "physical_enabled": self.signals is not None,
            }
        return [seq.state for seq in self.sequencers.values()]

    def resume_q1(self, seq: _Sequencer) -> None:
        if seq.waiting is None or seq.failed:
            return
        self.emit(seq, "q1_stall", seq.waiting_since, end=self.now, meta={"reason": seq.waiting}, lane="debug.q1_stall")
        seq.waiting = None
        self.schedule(self.now, 3, seq.state.sequencer_id, "q1")

    def q1(self, seq: _Sequencer) -> None:
        state = seq.state
        if seq.q1_done:
            return
        state.q1_time_ns = Concrete(self.now)
        instruction = state.instructions_by_pc.get(state.pc)
        if instruction is None:
            self.fail(seq, "error", "Execution reached a PC without an instruction or stop.")
            return
        if state.metadata["execution"]["steps"] >= self.max_steps:
            self.fail(seq, "step_limit", f"Reached the {self.max_steps} instruction limit.", instruction)
            return
        spec, invalid, diagnostic_start = core._validate_instruction(state, instruction, seq.symbols)
        if not core._concrete_instruction_ready(state, instruction, seq.symbols, spec, invalid, diagnostic_start, supported_ops=_SUPPORTED):
            self.close_waits(seq)
            seq.failed = True
            return
        scope = core._scope(seq.symbols, state)
        if spec.emits_rt_packet and len(seq.queue) + seq.reserved >= self.capacity:
            seq.waiting, seq.waiting_since = "queue_full", self.now
            return
        feedback = None
        if instruction.op in {"fb_pop_data", "fb_pull_data"}:
            # The documented pull form reads its first register as the requested
            # ID. The analysis-only approximation treats both as destinations.
            id_value = resolve_arg_value(instruction.args[0], scope)
            if not isinstance(id_value, Concrete):
                self.fail(seq, "blocked", "Feedback ID register is unknown.", instruction)
                return
            if instruction.op == "fb_pull_data" and core._pending_register_write(state, core._register_name(instruction.args[0], state)) is not None:
                self.fail(seq, "error", "Feedback ID register is not yet readable.", instruction)
                return
            matching_index = next((index for index, item in enumerate(seq.feedback) if item["id"] == id_value.value), len(seq.feedback))
            feedback = seq.feedback[matching_index] if matching_index < len(seq.feedback) else None
            discarded = seq.feedback[:matching_index]
            if discarded:
                self.emit(seq, "feedback_discard", self.now, instruction=instruction,
                    meta={"requested_id": id_value.value, "input_event_ids": [item["event_id"] for item in discarded]}, lane="rt.feedback")
            del seq.feedback[:matching_index + (feedback is not None)]
            if feedback is None:
                seq.waiting, seq.waiting_since = "feedback_input", self.now
                return
        state.next_pc = state.pc + 1
        reads = {register: value_to_json(resolve_arg_value(Arg("reg", register, register), scope))
                 for _, register in core._register_read_operands(instruction, state)}
        if instruction.op == "fb_pull_data":
            register = core._register_name(instruction.args[0], state)
            reads[register] = value_to_json(resolve_arg_value(instruction.args[0], scope))
        before = dict(state.alu_flags) if state.alu_flags is not None else None
        state.current_resolved_args = core._resolved_args_for_instruction(instruction, scope, state)
        event_start = len(state.events)
        pending_start = len(state.pending_register_writes)
        duration = spec.q1_time_model(core._instruction_for_q1_timing(instruction, state, seq.symbols))
        state.q1_time_ns = Concrete(self.now + duration)
        packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}" if spec.emits_rt_packet else None
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
            if instruction.op == "set_cond":
                resolved = tuple(resolve_arg_value(arg, scope) for arg in instruction.args)
                # Readiness permits unknown mask/operator only when disabled.
                values = tuple(value.value if isinstance(value, Concrete) else 0 for value in resolved)
                if values[0] and values[2] > 5:
                    self.fail(seq, "blocked", "Conditional operators 6 and 7 have no supported semantics.", instruction)
                    return
                seq.condition = values
                self.emit(seq, "latched_state_pending", state.q1_time_ns.value, instruction=instruction,
                          meta={"field": "condition", "value": core._plain_value(resolved)}, lane="state.pending")
            else:
                old_rt = state.rt_time_ns
                state.rt_time_ns = state.q1_time_ns
                core._execute_latched(state, instruction, seq.symbols)
                state.rt_time_ns = old_rt
                seq.phase_reset = seq.phase_reset or instruction.op == "reset_ph"
        elif feedback is not None:
            register = core._register_name(instruction.args[1], state)
            value = Concrete(feedback["value"] & 0xFFFFFFFF)
            provenance = core._register_provenance(state, register, expression=f"feedback id {feedback['id']}", value=value,
                steps=[core._provenance_step(instruction, f"feedback id {feedback['id']}", value)])
            provenance["input_event_id"] = feedback["event_id"]
            core._schedule_register_write(state, instruction, register, value, provenance)
        issue = core._emit_q1_issue(state, instruction, Concrete(self.now), state.q1_time_ns, packet_id)
        for event in state.events[event_start:]:
            if event.kind == "latched_state_pending":
                seq.setter_ids[event.meta["field"]] = event.id
                event.meta["q1_issue_event_id"] = issue.id
        if spec.category == "branch":
            core._execute_concrete_branch(state, instruction, issue, seq.symbols, defer_register_write=True)
        issue.meta.update(source_pc=instruction.pc, execution_index=state.q1_issue_index, register_reads=reads,
                          flags_before=before, flags_after=dict(state.alu_flags) if state.alu_flags is not None else None)
        if feedback is not None:
            issue.meta["input_event_ids"] = [feedback["event_id"]]
        for pending in state.pending_register_writes[pending_start:]:
            ready_time = state.q1_time_ns.value + (0 if instruction.op == "loop" else 4)
            self.schedule(ready_time, 1, state.sequencer_id, "register_ready", pending)
        state.current_resolved_args = []
        state.executed_pcs.add(state.pc)
        state.pc = state.next_pc
        state.q1_issue_index += 1
        state.metadata["execution"]["steps"] += 1
        if spec.emits_rt_packet:
            args = [resolve_arg_value(arg, scope).value for arg in instruction.args]
            duration_index = rt_duration_arg_for_instruction(spec, instruction)
            rt_duration = args[duration_index] if duration_index is not None else 0
            if rt_duration < 4:
                self.fail(seq, "error", "RT instruction duration must be at least 4 ns.", instruction)
                return
            updates = core._consume_applied_state(state) if spec.applies_latched_state else {}
            settings = self.latched_snapshot(state) if spec.applies_latched_state else {}
            packet = core.RtPacket(packet_id, state.sequencer_id, instruction.source, instruction.op, Concrete(rt_duration),
                                   Concrete(self.now), state.q1_time_ns, None, None, "exact",
                                   {"operand_snapshot": args, "applied_state": settings, "q1_issue_event_id": issue.id})
            state.rt_packets.append(packet)
            command = _Command(instruction, args, len(state.rt_packets)-1, rt_duration, settings, list(updates), seq.condition,
                               {key: seq.setter_ids[key] for key in settings if key in seq.setter_ids},
                               seq.phase_reset if spec.applies_latched_state else False)
            if spec.applies_latched_state:
                seq.phase_reset = False
            seq.reserved += 1
            self.schedule(state.q1_time_ns.value, 1, state.sequencer_id, "enqueue", command)
        if instruction.op == "stop":
            self.schedule(state.q1_time_ns.value, 1, state.sequencer_id, "q1_stop")
        else:
            self.schedule(state.q1_time_ns.value, 3, state.sequencer_id, "q1")

    @staticmethod
    def latched_snapshot(state: core.AnalysisState) -> dict:
        names = {"marker": "marker", "awg_gain": "awg_gain", "awg_offset": "awg_offset", "frequency": "frequency",
                 "phase": "phase", "phase_delta": "phase_delta", "digital": "digital", "scope_enable": "scope_enable"}
        return {key: core._plain_value(getattr(state.latched_state, attr)) for key, attr in names.items()
                if getattr(state.latched_state, attr) is not None}

    def rt(self, seq: _Sequencer) -> None:
        if seq.rt_done or seq.rt_wait is not None:
            return
        state = seq.state
        if not seq.queue:
            if seq.q1_done and seq.reserved == 0:
                seq.rt_done = True
                state.rt_time_ns = Concrete(self.now)
                self.emit(seq, "stop", self.now, lane="rt.control")
                try:
                    if self.signals is not None:
                        self.signals.apply(state.sequencer_id, self.now, "stop", [], {**seq.rt_settings, "_updated_fields": []})
                except ValueError as error:
                    self.fail(seq, "error", f"Signal model: {error}")
            else:
                self.emit(seq, "underflow_warning", self.now, meta={"simulated": True, "depth": 0, "queue_capacity": self.capacity})
                self.fail(seq, "error", f"RT queue underflow at {self.now} ns.", category="rt_queue_underflow")
            return
        command = seq.queue.popleft()
        self.emit(seq, "rt_queue_dequeue", self.now, instruction=command.instruction,
                  meta={"depth": len(seq.queue), "capacity": self.capacity, "rt_packet_id": state.rt_packets[command.packet_index].id}, lane="debug.rt_queue")
        if seq.waiting == "queue_full":
            self.resume_q1(seq)
        condition = command.condition
        if condition[0] and not self.condition(seq, condition):
            self.finish_rt(seq, command, self.now, condition[3], skipped=True)
        elif command.instruction.op == "wait_sync":
            seq.rt_wait = ("sync_wait", command, self.now)
            self.sync_waiters[state.sequencer_id] = (command, self.now)
            if len(self.sync_waiters) == len(self.sequencers):
                for name, (waiting, started) in list(self.sync_waiters.items()):
                    peer = self.sequencers[name]
                    peer.rt_wait = None
                    self.emit(peer, "sync_wait", started, end=self.now, instruction=waiting.instruction)
                    self.finish_rt(peer, waiting, started, self.now-started+waiting.duration)
                self.sync_waiters.clear()
        elif command.instruction.op == "wait_trigger":
            tokens = seq.trigger_tokens.setdefault(command.args[0], deque())
            while tokens and tokens[0]["time_ns"] < self.now:
                tokens.popleft()
            if tokens:
                token = tokens.popleft()
                self.finish_rt(seq, command, self.now, command.duration, input_event_id=token["event_id"])
            else:
                seq.rt_wait = ("trigger_wait", command, self.now)
        else:
            self.finish_rt(seq, command, self.now, command.duration)

    @staticmethod
    def condition(seq: _Sequencer, config: tuple) -> bool:
        mask, operator = config[1:3]
        values = [seq.trigger_counts.get(address, 0) >= 1 for address in range(1, 16) if mask & (1 << (address-1))]
        result = (any(values), not any(values), all(values), not all(values), sum(values) % 2 == 1, sum(values) % 2 == 0)
        return result[operator]

    def finish_rt(self, seq: _Sequencer, command: _Command, started: int, duration: int, *, skipped: bool = False, input_event_id: str | None = None) -> None:
        state, op, args = seq.state, command.instruction.op, command.args
        end = started + duration
        packet = state.rt_packets[command.packet_index]
        meta = {**packet.meta, "rt_packet_id": packet.id, "applied_state": command.settings,
                "applied_setter_event_ids": command.setter_ids}
        if input_event_id is not None:
            meta["input_event_ids"] = [input_event_id]
        if skipped:
            meta.update(condition=list(command.condition), conditional_skipped=True,
                        captured_state=command.settings, applied_state={}, applied_setter_event_ids={})
        state.rt_packets[command.packet_index] = replace(packet, rt_t0=Concrete(started), rt_t1=Concrete(end), duration=Concrete(duration), meta=meta)
        state.rt_time_ns = Concrete(end)
        if not skipped and op == "play":
            for path, waveform_index in enumerate(args[:2]):
                self.emit(seq, "play", started, end=end, instruction=command.instruction,
                          meta={**meta, "path": path, "waveform_index": waveform_index}, lane=f"rt.path{path}")
        else:
            kind = "conditional_skip" if skipped else "acquire" if op.startswith("acquire") else op
            if op.startswith("acquire"):
                meta.update(op=op, acquisition_index=args[0], bin_index=args[1])
            self.emit(seq, kind, started, end=end, instruction=command.instruction, meta=meta,
                      lane="rt.branch" if skipped else f"rt.{kind}")
        if not skipped and "marker" in command.settings:
            core._emit_marker_state_event(state, command.instruction, duration=Concrete(duration), t0=Concrete(started), t1=Concrete(end),
                                          packet_id=packet.id, value=command.settings["marker"])
        if not skipped:
            changed = [key for key, value in command.settings.items() if seq.rt_settings.get(key) != value]
            seq.rt_settings.update(command.settings)
            settings = {**seq.rt_settings, "_updated_fields": sorted(set(command.updated_fields) | set(changed)), "_phase_reset": command.phase_reset}
            try:
                if self.signals is not None:
                    self.signals.apply(state.sequencer_id, self.now, op, args, settings)
            except ValueError as error:
                self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                return
            if op == "set_latch_en":
                seq.latch_enabled = bool(args[0])
            elif op == "latch_rst":
                seq.trigger_counts.clear()
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
            elif op in {"acquire", "acquire_weighted", "acquire_weighed"}:
                if seq.acquisition is not None:
                    self.finish_acquisition(seq, seq.acquisition, interrupted=True)
                    if seq.failed:
                        return
                try:
                    acquisition_duration = self.signals.acquisition_duration(state.sequencer_id, command.duration,
                        weights=args[2:4] if op != "acquire" else None) if self.signals is not None else command.duration
                except ValueError as error:
                    self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                    return
                measurement = {"command": command, "start_ns": self.now, "duration_ns": acquisition_duration,
                               "tb_id": seq.tb_id, "iq_id": seq.iq_id, "tb_valid": seq.tb_valid, "iq_shift": seq.iq_shift}
                seq.acquisition = measurement
                self.schedule(self.now+acquisition_duration, 0, state.sequencer_id, "acquisition", measurement)
        self.schedule(end, 2, state.sequencer_id, "rt")

    def send(self, seq: _Sequencer, identifier: int, value: int, instruction: Instr, source_event_id: str | None = None,
             *, source_packet: core.RtPacket | None = None) -> None:
        if identifier == 0:
            return
        routes = [route for route in self.config.get("routes", []) if route["source"] == seq.state.sequencer_id and route.get("id", identifier) == identifier]
        meta = {"id": identifier, "value": value,
                "targets": [route["target"] for route in routes], "source_event_id": source_event_id}
        if source_packet is not None:
            meta.update(q1_issue_event_id=source_packet.meta["q1_issue_event_id"], source_packet_id=source_packet.id)
        event = self.emit(seq, "feedback_send", self.now, instruction=instruction, meta=meta, lane="rt.feedback")
        for route in routes:
            self.schedule(self.now+route.get("latency_ns", 0), 0, route["target"], "feedback",
                          {"id": identifier, "value": value, "source_event_id": event.id})

    def feedback(self, seq: _Sequencer, data: dict) -> None:
        if len(seq.feedback) >= self.feedback_capacity:
            self.fail(seq, "error", f"Feedback queue overflow at {self.now} ns.", category="feedback_queue_overflow")
            return
        event = self.emit(seq, "feedback_receive", self.now, meta={**data, "depth": len(seq.feedback)+1}, lane="rt.feedback")
        seq.feedback.append({**data, "event_id": event.id})
        if seq.waiting == "feedback_input":
            self.resume_q1(seq)

    def trigger(self, seq: _Sequencer, data: dict) -> None:
        address = data["address"]
        event = self.emit(seq, "trigger_receive", self.now, meta=data, lane="rt.trigger")
        if seq.latch_enabled:
            seq.trigger_counts[address] = seq.trigger_counts.get(address, 0) + 1
        token = {**data, "event_id": event.id, "time_ns": self.now}
        if seq.rt_wait is not None and seq.rt_wait[0] == "trigger_wait" and seq.rt_wait[1].args[0] == address:
            _, command, started = seq.rt_wait
            seq.rt_wait = None
            self.emit(seq, "trigger_wait", started, end=self.now, instruction=command.instruction, meta={"input_event_ids": [event.id]})
            self.finish_rt(seq, command, started, self.now-started+command.duration, input_event_id=event.id)
        else:
            seq.trigger_tokens.setdefault(address, deque()).append(token)

    def finish_acquisition(self, seq: _Sequencer, measurement: dict, *, interrupted: bool = False) -> None:
        command = measurement["command"]
        args = command.args
        if self.now <= measurement["start_ns"]:
            self.fail(seq, "error", "Acquisition was interrupted before any sample interval elapsed.", command.instruction,
                      category="acquisition_overlap")
            return
        result = None
        for index, item in enumerate(self.config.get("inputs", {}).get("acquisitions", [])):
            if index not in self.used_acquisition_inputs and item["sequencer"] == seq.state.sequencer_id and item["acquisition_index"] == args[0] and item["bin_index"] == args[1]:
                self.used_acquisition_inputs.add(index)
                result = {key: item[key] for key in ("i", "q", "thresholded") if key in item}
                result.update(input_index=index, source={"kind": "scenario", "label": f"acquisition input {index}"})
                break
        if result is None:
            try:
                if self.signals is not None:
                    result = self.signals.acquire(seq.state.sequencer_id, measurement["start_ns"], self.now-measurement["start_ns"],
                        args[0], args[1], weights=args[2:4] if command.instruction.op != "acquire" else None)
            except ValueError as error:
                self.fail(seq, "error", f"Signal model: {error}", command.instruction)
                return
            if result is not None:
                result = {**result, "source": result.get("source", {"kind": "physical", "label": "modeled acquisition"})}
        seq.acquisition = None
        if result is None:
            self.fail(seq, "blocked", "Acquisition requires a scenario result or configured physical input.", command.instruction)
            return
        result = {**result, "acquisition_index": args[0], "bin_index": args[1], "start_ns": measurement["start_ns"],
                  "duration_ns": self.now-measurement["start_ns"], "interrupted": interrupted,
                  "rt_packet_id": seq.state.rt_packets[command.packet_index].id}
        event = self.emit(seq, "acquisition_result", self.now, instruction=command.instruction, meta=result, lane="rt.acquire")
        seq.acquisitions.append({**result, "event_id": event.id, "time_ns": self.now})
        for route in self.config.get("trigger_routes", []):
            if route["source"] == seq.state.sequencer_id and result.get("thresholded") == route["thresholded"]:
                self.schedule(self.now+route["latency_ns"], 0, route["target"], "triggers",
                              {"address": route["address"], "source_event_id": event.id})
        if measurement["tb_id"]:
            if "thresholded" not in result:
                self.fail(seq, "blocked", "Threshold feedback requires a thresholded acquisition result.", command.instruction)
                return
            self.send(seq, measurement["tb_id"], (measurement["tb_valid"] << 1) | int(bool(result["thresholded"])), command.instruction, event.id)
        if measurement["iq_id"]:
            scale = self.config.get("acquisition_iq_scale", {}).get(seq.state.sequencer_id)
            is_raw_scenario = result.get("source", {}).get("kind") == "scenario" and all(type(result.get(component)) is int for component in ("i", "q"))
            if not all(component in result for component in ("i", "q")) or (scale is None and not is_raw_scenario):
                self.fail(seq, "blocked", "IQ feedback needs integer scenario I/Q words or an explicit acquisition_iq_scale.", command.instruction)
                return
            scaled_values = [result[component] * scale if scale is not None else result[component] for component in ("i", "q")]
            if any(not -(2**31) <= value < 2**31 for value in scaled_values):
                self.fail(seq, "error", "Scaled IQ feedback is outside signed 32-bit range.", command.instruction)
                return
            words = [round(value) for value in scaled_values]
            if any(not -(2**31) <= word < 2**31 for word in words):
                self.fail(seq, "error", "Rounded IQ feedback is outside signed 32-bit range.", command.instruction)
                return
            for word in words:
                self.send(seq, measurement["iq_id"], word >> measurement["iq_shift"], command.instruction, event.id)
