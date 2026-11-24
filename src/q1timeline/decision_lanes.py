"""Record Playback runs of one sequencer for the Decision lanes view, over JSON lines.

Every run is one analytical Playback execution (q1timeline.playback) of a single sequencer.
It is not hardware simulation: feedback words and trigger arrivals are assumptions. The base
run follows the conditions computed from the supplied inputs. A flip run repeats a recorded run
with one conditional branch occurrence forced the other way, which gives the view a recorded
opposite arm to compare with; it does not show that the path is reachable.

Feedback words come from the project's LINQ schedule for the sequencer, or from a list supplied
with the request: the k-th executed fb_pop_data reads value k, cycling through the list, and is
treated as already received (no additional wait).
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sys
from time import monotonic
from typing import Any

from q1timeline.analysis.values import Concrete, resolve_arg_value
from q1timeline.execution import operands as operand_binding
from q1timeline.feedback_fifo import DEFAULT_FEEDBACK_QUEUE_CAPACITY
from q1timeline.playback import PlaybackSession
from q1timeline.project import ConfigLoadError, ProjectConfig, SequencerConfig, load_project_config, load_single_file_config
from q1timeline.q1asm.ast import Instr
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.sequence import load_sequence_names


SCHEMA_VERSION = 1
REGISTERS = [f"R{i}" for i in range(64)]
_REGISTER_INDEX = {name: index for index, name in enumerate(REGISTERS)}
# Instructions that bring an external word into a register.
INPUT_OPS = frozenset({"fb_pop_data", "fb_pull_data"})
MAX_STEPS = 100_000
MAX_FEEDBACK_VALUES = 4096
BASE_RUN = "base"
SCENARIO = "inputs"


def _word(value: Any) -> int | None:
    if isinstance(value, Concrete):
        return int(value.value)
    if isinstance(value, dict) and value.get("kind") == "concrete":
        return int(value["value"])
    return None


def _signed(value: Any) -> int | None:
    """A register word as a signed 32-bit integer; None when the value is unknown."""
    raw = _word(value)
    if raw is None:
        return None
    raw &= 0xFFFFFFFF
    return raw - 0x100000000 if raw >= 0x80000000 else raw


def _clock(value: Any) -> int | None:
    return int(value.value) if isinstance(value, Concrete) else None


def _is_conditional(instruction: Instr) -> bool:
    return get_instruction_spec(instruction.op).category == "branch" and instruction.op != "jmp"


def validate_feedback(values: Any) -> list[int] | None:
    """The supplied feedback words, or None to use the project's own inputs."""
    if values is None:
        return None
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_FEEDBACK_VALUES:
        raise ValueError(f"feedback must list 1 to {MAX_FEEDBACK_VALUES} integers.")
    for value in values:
        if type(value) is not int or not -(1 << 31) <= value <= 0xFFFFFFFF:
            raise ValueError("Each feedback value must be a 32-bit signed or unsigned integer.")
    return list(values)


def _inputs_for(external_inputs: dict, sequencer: str, *, schedule: bool) -> dict:
    """The project's external inputs narrowed to one receiver."""
    result: dict[str, Any] = {"triggers": [{**source, "targets": [sequencer]}
                                           for source in external_inputs.get("triggers", [])
                                           if sequencer in source["targets"]]}
    receivers = [receiver for receiver in external_inputs.get("feedback", []) if receiver["target"] == sequencer]
    if schedule and receivers:
        result["feedback"] = receivers
    return result


@dataclass
class _Run:
    payload: dict
    steps: list[list]
    # step index -> (source pc, occurrence, taken) for every executed conditional branch
    branches: dict[int, tuple[int, int, bool]] = field(default_factory=dict)
    forced: dict[tuple[int, int], bool] = field(default_factory=dict)
    instructions: list[dict] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    engine_model: dict = field(default_factory=dict)


class LaneRecorder:
    """Loads one project and records the runs the Decision lanes view asks for."""

    def __init__(self, project: str | Path, *, params: dict | None = None, max_steps: int | None = None):
        if max_steps is not None and (type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS):
            raise ValueError(f"max_steps must be an integer from 1 to {MAX_STEPS}.")
        self.project = Path(project)
        self.param_overrides = dict(params or {})
        self.max_steps_override = max_steps
        self._config: ProjectConfig | None = None
        self._sequencer: SequencerConfig | None = None
        self._text = ""
        self._feedback: list[int] | None = None
        self._runs: dict[str, _Run] = {}

    # ---- sessions --------------------------------------------------------------------------
    def _session(self) -> PlaybackSession:
        config, seq = self._config, self._sequencer
        assert config is not None and seq is not None
        session = PlaybackSession(
            {seq.id: (str(seq.file), self._text)},
            params={**config.params, **self.param_overrides},
            external_inputs=_inputs_for(config.external_inputs, seq.id, schedule=self._feedback is None),
            hardware_parameters={seq.id: dict(seq.hardware_parameters)},
            feedback_queue_capacity=config.simulation.get("feedback_queue_capacity", DEFAULT_FEEDBACK_QUEUE_CAPACITY),
        )
        if seq.sequence_json:
            session.sequence_names = {seq.id: load_sequence_names(seq.sequence_json)}
        return session

    @property
    def max_steps(self) -> int:
        if self.max_steps_override is not None:
            return self.max_steps_override
        assert self._config is not None
        return max(1, min(MAX_STEPS, self._config.analysis.max_steps))

    # ---- one recorded run ------------------------------------------------------------------
    def _execute(self, forced: dict[tuple[int, int], bool]) -> _Run:
        """Run the sequencer once. forced maps (source pc, occurrence) to the direction to take."""
        seq = self._sequencer
        assert seq is not None
        session = self._session()
        if session.status == "error":
            raise ValueError(session.reason or "The program cannot be executed.")
        runner = session.runners[0]
        state, program = runner.state, session.programs[seq.id]
        pc_at_source = {(item.source.line, item.source.column): item.pc for item in program.instructions}
        feedback = self._feedback
        # For a branch that reads the status flags: the instruction that set them. The engine
        # keeps this itself (state.alu_flags_source); it is read just before the branch runs.
        flags_from: dict[int, int | None] = {}
        branch_keys: dict[int, tuple[int, int]] = {}
        occurrences: dict[int, int] = defaultdict(int)
        pops = 0
        stop_reason = None
        limit = self.max_steps
        while True:
            if session.steps >= limit:
                stop_reason = ("step_limit", f"Stopped after {limit:,} instructions (step limit).")
                break
            if state.interior_pc is not None:
                stop_reason = ("blocked", "Execution entered the interior of a compiled instruction; Decision lanes stops here.")
                break
            instruction = state.instructions_by_pc.get(state.pc)
            index = state.q1_issue_index
            key = None
            if instruction is not None:
                if instruction.op == "fb_pop_data" and feedback is not None:
                    session.feedback_choices[f"{seq.id}:feedback:{index}"] = {
                        "value": feedback[pops % len(feedback)], "delay_ns": 0, "source": "user"}
                if _is_conditional(instruction):
                    source = state.alu_flags_source
                    flags_from[index] = None if source is None else pc_at_source.get((source.line, source.column))
                    key = (instruction.pc, occurrences[instruction.pc])
                    if key in forced:
                        session.choices[f"{seq.id}:playback:{index}"] = "taken" if forced[key] else "fallthrough"
            before = session.steps
            summary = session.run(max_steps=1, budget_ms=1000)
            if session.steps > before and instruction is not None:
                if instruction.op in INPUT_OPS:
                    pops += 1
                if key is not None:
                    branch_keys[index] = key
                    occurrences[instruction.pc] += 1
            if not summary["can_resume"]:
                break

        issues = {event.meta["execution_index"]: event for event in state.events
                  if event.kind == "q1_issue" and "execution_index" in event.meta}
        writes_by_event: dict[str, list[list]] = defaultdict(list)
        writers: dict[str, list[tuple[int, int]]] = defaultdict(list)
        index_of_event = {event.id: index for index, event in issues.items()}
        for write in state.register_history:
            index = index_of_event.get(write.event_id)
            at = _clock(write.time)
            if index is None or at is None or write.register not in _REGISTER_INDEX:
                continue
            writes_by_event[write.event_id].append([_REGISTER_INDEX[write.register], _signed(write.meta.get("previous_value")),
                                                    _signed(write.value), at])
            writers[write.register].append((at, index))
        for values in writers.values():
            values.sort()
        aliases = dict(state.register_aliases)
        scope = operand_binding.operand_scope(runner.symbols, state)

        def operand(arg, resolved, reads) -> list:
            """One compared operand as [source text, register index or None, unsigned 32-bit word]."""
            name = arg.raw[1:] if arg.raw.startswith("$") else arg.raw
            register = aliases.get(name, name if name in _REGISTER_INDEX else None)
            value = _word(resolved.get("value")) if resolved else None
            if value is None and register is not None:
                value = _word(reads.get(register))             # the word the branch read at that moment
            if value is None and register is None:
                value = _word(resolve_arg_value(arg, scope))   # an immediate or a .DEF constant
            return [arg.raw, _REGISTER_INDEX.get(register), None if value is None else value & 0xFFFFFFFF]

        branch_by_event = {item["event_id"]: item for item in session.branches}
        records: list[list] = []
        branches: dict[int, tuple[int, int, bool]] = {}
        last_run_of: dict[int, int] = {}       # source pc -> index of its most recent execution
        cut = None
        for index, executed in enumerate(runner.trace):
            event = issues.get(index)
            source_pc = executed.source_pc
            instruction = state.instructions_by_pc.get(source_pc)
            times = None if event is None else (_clock(event.t0), _clock(event.t1), _clock(executed.rt_start), _clock(executed.rt_end))
            if event is None or instruction is None or None in times:
                # A step without a concrete Q1 or RT time cannot be placed on the lanes.
                cut = index
                break
            last_flags = last_run_of.get(flags_from.get(index))
            reads = []
            for register, value in event.meta.get("register_reads", {}).items():
                if register not in _REGISTER_INDEX:
                    continue
                # The writer of the value read: the latest write by an earlier step that was ready by now.
                ready = writers.get(register, ())
                position = bisect_right(ready, (times[0], index)) - 1
                while position >= 0 and ready[position][1] >= index:
                    position -= 1
                reads.append([_REGISTER_INDEX[register], _signed(value), ready[position][1] if position >= 0 else None])
            branch = None
            if "branch_taken" in event.meta:
                recorded = branch_by_event.get(event.id)
                conditional = instruction.op != "jmp"
                # jlt/jge with three operands and loop compare inside the branch itself: they are
                # their own "producer". One-operand branches read the flags of an earlier instruction.
                own = conditional and (len(instruction.args) == 3 or instruction.op == "loop")
                branch = [bool(event.meta["branch_taken"]), recorded["calculated_taken"] if recorded else True,
                          bool(recorded and recorded["assumption_source"] == "user"),
                          None if not conditional else index if own else last_flags,
                          event.meta.get("target_pc"), instruction.pc + 1]
                # The two compared operands as 32-bit words: from the branch itself (three
                # operands), or from the cmp or test that set the flags a one-operand branch reads.
                compared = event if conditional and len(instruction.args) == 3 else None
                if conditional and not own and last_flags is not None:
                    producer = state.instructions_by_pc[runner.trace[last_flags].source_pc]
                    if producer.op in {"cmp", "test"} and len(producer.args) >= 2:
                        compared = issues[last_flags]
                if compared is not None:
                    source = state.instructions_by_pc[compared.meta["source_pc"]]
                    by_index = {item["index"]: item for item in compared.meta.get("resolved_args") or []}
                    branch.append([operand(source.args[k], by_index.get(k), compared.meta.get("register_reads", {}))
                                   for k in (0, 1)])
                if conditional and index in branch_keys:
                    branches[index] = (*branch_keys[index], branch[0])
            records.append([instruction.pc, times[0], times[1], times[2], times[3], reads,
                            writes_by_event[event.id], None, branch, None])
            last_run_of[instruction.pc] = index
        complete = cut is None
        for i, record in enumerate(records):
            record[9] = records[i + 1][0] if i + 1 < len(records) else (None if state.stopped and complete else state.pc if complete else None)

        if not complete:
            status, reason = "blocked", "A step has no concrete Q1 or RT time; the record stops before it."
        elif stop_reason is not None:
            status, reason = stop_reason
        else:
            status, reason = session.status, session.reason
            if runner.waiting is not None:
                reason = runner.waiting.get("reason", reason)   # without the session's "N sequencer(s) waiting" prefix
        q1_end, rt_end = _clock(state.q1_time_ns), _clock(state.rt_time_ns)
        if records and (not complete or q1_end is None or rt_end is None):
            q1_end, rt_end = max(record[2] for record in records), max(record[4] for record in records)
        execution = state.metadata.get("execution", {})
        payload = {
            "scenario": SCENARIO, "steps": records, "registers": [_signed(state.registers.get(name)) for name in REGISTERS],
            "status": status, "reason": reason, "truncated": status != "completed",
            "feedbackCount": pops, "q1End": q1_end, "rtEnd": rt_end,
        }
        return _Run(
            payload, records, branches, dict(forced),
            # text is the instruction as written, without its label or comment.
            instructions=[{"pc": item.pc, "line": item.source.line, "op": item.op,
                           "text": f"{item.op} {', '.join(arg.raw for arg in item.args)}".strip(),
                           "args": [arg.raw for arg in item.args]}
                          for item in program.instructions],
            labels=dict(program.labels),
            # What the engine itself says this kind of run assumes; shown in the view word for word.
            engine_model={"timingModel": execution.get("timing_model"),
                          "assumptions": list(execution.get("assumptions", []))},
        )

    # ---- requests --------------------------------------------------------------------------
    def _load(self) -> ProjectConfig:
        path = self.project
        config = load_single_file_config(path) if path.suffix.lower() == ".q1asm" else load_project_config(path)
        errors = [item for item in config.diagnostics if item.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)
        if not config.sequencers:
            raise ValueError("The project has no sequencers.")
        return config

    def info(self) -> dict:
        """The project's sequencers, read from disk, without executing anything."""
        config = self._load()
        scheduled = {receiver["target"] for receiver in config.external_inputs.get("feedback", [])}
        return {"project": str(self.project.resolve()),
                "sequencers": [{"id": item.id, "name": item.name, "file": str(item.file), "scheduled": item.id in scheduled}
                               for item in config.sequencers]}

    def open(self, sequencer: str | None = None, feedback: Any = None) -> dict:
        """Load the project from disk and record the base run of one sequencer."""
        path = self.project
        config = self._load()
        if sequencer is None:
            seq = config.sequencers[0]
        else:
            seq = next((item for item in config.sequencers if item.id == sequencer), None)
            if seq is None:
                raise ValueError(f"Unknown sequencer: {sequencer}")
        values = validate_feedback(feedback)
        scheduled = any(receiver["target"] == seq.id for receiver in config.external_inputs.get("feedback", []))
        text = seq.file.read_text(encoding="utf-8-sig")
        self._config, self._sequencer, self._text, self._feedback = config, seq, text, values
        self._runs = {}
        try:
            base = self._execute({})
            if not base.steps:
                raise ValueError(f"No instruction was executed: {base.payload['reason'] or base.payload['status']}")
        except Exception:
            self._config = None      # nothing is open after a failed record
            raise
        self._runs[BASE_RUN] = base
        triggers = sum(len(source["times_ns"]) for source in config.external_inputs.get("triggers", [])
                       if seq.id in source["targets"])
        return {
            "schema": SCHEMA_VERSION,
            "project": str(path.resolve()),
            "sequencers": [{"id": item.id, "name": item.name, "file": str(item.file)} for item in config.sequencers],
            "sequencer": seq.id,
            "sourceFile": str(seq.file),
            "source": self._text.splitlines(),
            "sourceSha256": hashlib.sha256(self._text.encode("utf-8")).hexdigest(),
            "instructions": base.instructions,
            "labels": base.labels,
            "registers": REGISTERS,
            "inputs": {"feedback": values, "scheduled": scheduled, "usesSchedule": values is None and scheduled,
                       "pops": base.payload["feedbackCount"], "triggers": triggers},
            "scenarios": [{"id": SCENARIO, "base": BASE_RUN, "flips": []}],
            "runs": {BASE_RUN: {**base.payload, "kind": "base", "forced": {}}},
            "maxSteps": self.max_steps,
            "engine": "q1timeline.playback.PlaybackSession",
            "engineModel": base.engine_model,
        }

    def flips(self, requests: Any, *, budget_ms: int = 200) -> dict:
        """Record runs that force one branch occurrence of an already recorded run the other way.

        Each request is {"run": id, "step": index}. Requests are served in order until the time
        budget is spent; the rest come back as "pending". At least one request is always served.
        """
        if self._config is None:
            raise ValueError("Open a sequencer before requesting flips.")
        if not isinstance(requests, list) or len(requests) > 1024:
            raise ValueError("requests must be a list of at most 1024 items.")
        if type(budget_ms) is not int or not 1 <= budget_ms <= 5000:
            raise ValueError("budget_ms must be an integer between 1 and 5000.")
        deadline = monotonic() + budget_ms / 1000
        runs: dict[str, dict] = {}
        failed: list[dict] = []
        pending: list[dict] = []
        served = 0
        for position, request in enumerate(requests):
            if not isinstance(request, dict) or type(request.get("step")) is not int or not isinstance(request.get("run"), str):
                raise ValueError("Each flip request needs a run id and a step index.")
            parent_id, step = request["run"], request["step"]
            run_id = f"{parent_id}~{step}"
            if run_id in self._runs:
                runs[run_id] = self._runs[run_id].payload
                continue
            if served and monotonic() >= deadline:
                pending = [{"run": item["run"], "step": item["step"]} for item in requests[position:]
                           if isinstance(item, dict)]
                break
            parent = self._runs.get(parent_id)
            if parent is None or step not in parent.branches:
                failed.append({"run": parent_id, "step": step, "reason": "Not a recorded conditional branch."})
                continue
            pc, occurrence, taken = parent.branches[step]
            forced = {**parent.forced, (pc, occurrence): not taken}
            served += 1
            try:
                variant = self._execute(forced)
            except ValueError as error:
                failed.append({"run": parent_id, "step": step, "reason": str(error)})
                continue
            if len(variant.steps) <= step or variant.steps[step][8] is None or variant.steps[step][8][0] == taken:
                failed.append({"run": parent_id, "step": step,
                               "reason": variant.payload["reason"] or "The forced run did not reach this branch."})
                continue
            shared = 0
            for mine, theirs in zip(parent.steps, variant.steps):
                if mine != theirs:
                    break
                shared += 1
            variant.payload = {**variant.payload, "kind": "flip", "base": parent_id, "shared": shared, "fork": step,
                               "steps": variant.steps[shared:],
                               "forced": {f"{key[0]}:{key[1]}": value for key, value in variant.forced.items()}}
            self._runs[run_id] = variant
            runs[run_id] = variant.payload
        return {"runs": runs, "failed": failed, "pending": pending}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="q1timeline.yml, or a single .q1asm file")
    parser.add_argument("--set-param", action="append", default=[])
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="print one record for --sequencer/--feedback and exit")
    parser.add_argument("--sequencer", default=None)
    parser.add_argument("--feedback", default=None, help="comma-separated feedback words for --once")
    args = parser.parse_args(argv)
    from q1timeline.cli import _parse_param_overrides

    def failure(error: Exception) -> str:
        if isinstance(error, ConfigLoadError):
            return "; ".join(item.message for item in error.diagnostics) or str(error)
        return str(error)

    try:
        recorder = LaneRecorder(args.project, params=_parse_param_overrides(args.set_param), max_steps=args.max_steps)
        if args.once:
            feedback = None if args.feedback is None else [int(item, 0) for item in args.feedback.split(",") if item.strip()]
            print(json.dumps(recorder.open(args.sequencer, feedback), ensure_ascii=True))
            return 0
    except (OSError, ValueError, ConfigLoadError) as exc:
        print(json.dumps({"id": None, "error": failure(exc)}), flush=True)
        return 2
    sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        request: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Decision lanes command must be an object")
            command = request.get("command")
            if command == "info":
                result = {"info": recorder.info()}
            elif command == "open":
                result = {"lanes": recorder.open(request.get("sequencer"), request.get("feedback"))}
            elif command == "flips":
                result = recorder.flips(request.get("requests"), budget_ms=request.get("budget_ms", 200))
            else:
                raise ValueError(f"Unknown decision lanes command: {command}")
            result["id"] = request.get("id")
        except (OSError, ValueError, TypeError, KeyError, ConfigLoadError) as exc:
            result = {"id": request.get("id") if isinstance(request, dict) else None, "error": failure(exc)}
        print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
