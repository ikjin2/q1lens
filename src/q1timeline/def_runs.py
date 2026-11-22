"""Run and sweep numeric Q1ASM DEFs over the extension's JSON-lines bridge."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter
from collections.abc import Collection
from dataclasses import asdict, dataclass
from decimal import Decimal
from math import sqrt
from pathlib import Path
from random import Random
from time import monotonic

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.hardware_readiness import analyze_project_hardware_readiness
from q1timeline.analysis.interpreter import (
    AnalysisState, PreparedDefProgram, _physical_read_register, _register_aliases,
    diagnose_program_source, interpret_program, prepare_def_program,
)
from q1timeline.analysis.underflow import analyze_underflows
from q1timeline.analysis.values import Concrete, Symbolic, resolve_def_values, symbol_table_from_params, value_to_json
from q1timeline.cli import _load_json_override, _parse_param_overrides
from q1timeline.ir.serialize import _diagnostic_to_dict, timeline_ir_from_states
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.ast import Program, duplicate_def_names, numeric_defs, with_def_values
from q1timeline.q1asm.compiled_addresses import compile_addresses
from q1timeline.q1asm.parser import parse_integer_literal, parse_q1asm
from q1timeline.sequence import load_sequence_data, load_sequence_names
from q1timeline.simulation.runtime import simulate_programs


MAX_CASES = 4096
MAX_RANDOM_CASES = 25_000
MAX_STATISTICS_CASES = 1_000_000
REGISTERS = frozenset(f"R{i}" for i in range(64))


def _referenced_registers(program: Program, params: dict) -> list[str]:
    scope = AnalysisState("source", register_aliases=_register_aliases(program.defs),
                          symbol_origins={"defs": program.defs, "params": params})
    registers = {_physical_read_register(arg, scope) for instruction in program.instructions for arg in instruction.args}
    return sorted(registers & REGISTERS, key=lambda name: int(name[1:]))


def _def_rows(program: Program, params: dict, strict: bool) -> list[dict]:
    layout = compile_addresses({item.pc: item for item in program.instructions}, program.labels)
    resolved = resolve_def_values({name: arg for name, arg in program.defs.items() if arg.kind != "reg"},
                                  symbol_table_from_params(params), labels=layout.labels if layout else program.labels)
    duplicates = duplicate_def_names(program)
    rows = []
    for name, arg in program.defs.items():
        source = program.def_sources[name]
        row = {"name": name, "literal": arg.raw, "value": None, "line": source.line, "column": source.column}
        if name in duplicates:
            row["reason"] = "Duplicate DEF declaration."
        elif arg.kind == "reg":
            row["reason"] = "Register alias."
        elif arg.kind == "label":
            row["reason"] = "Label address."
        elif arg.kind == "placeholder" and strict:
            row["reason"] = "Strict Q1ASM disallows template DEFs."
        elif isinstance(resolved[name], Concrete):
            row["value"] = str(resolved[name].value)
        elif arg.kind == "placeholder" and isinstance(resolved[name], Symbolic) and resolved[name].unresolved_symbols:
            row["needs_input"] = True
        else:
            row["reason"] = "The template does not resolve to an integer with the current parameters."
        rows.append(row)
    return rows


def _literal(raw, label: str) -> int:
    if isinstance(raw, str):
        try:
            return parse_integer_literal(raw.strip())
        except ValueError:
            pass
    raise ValueError(f"{label} must be a Q1ASM integer literal (decimal or hexadecimal).")


def sweep_plan(axes: list, defs: Collection[str], *, statistics_only: bool = False) -> dict:
    if not isinstance(axes, list) or not 1 <= len(axes) <= 2:
        raise ValueError("Select one or two DEFs to sweep.")
    ranges = []
    names = set()
    total = 1
    for axis in axes:
        name = axis["name"]
        if name not in defs or name in names:
            raise ValueError("Sweep axes must be distinct editable numeric DEFs.")
        names.add(name)
        lower, upper, step = (_literal(axis[key], f"{name} {key}") for key in ("min", "max", "step"))
        if lower > upper or step <= 0:
            raise ValueError(f"{name} needs min ≤ max and a positive step.")
        count = (upper - lower) // step + 1
        total *= count
        ranges.append({"name": name, "count": str(Decimal(count)), "min": str(lower), "step": str(step),
                       "last": str(lower + (count - 1) * step)})
    limit = MAX_STATISTICS_CASES if statistics_only else MAX_CASES
    return {"axes": ranges, "total": str(Decimal(total)), "allowed": total <= limit, "limit": limit,
            "sample_limit": MAX_STATISTICS_CASES if statistics_only else MAX_RANDOM_CASES}


@dataclass
class _Moments:
    count: int = 0
    total: int = 0
    squares: int = 0
    minimum: int | None = None
    maximum: int | None = None

    def add(self, value: int) -> None:
        self.count += 1
        self.total += value
        self.squares += value * value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def result(self) -> dict | None:
        if not self.count:
            return None
        # Subtract before converting to floats so narrow spreads near uint32
        # limits retain their full population variance.
        variance = (self.count * self.squares - self.total**2) / self.count**2
        return {"count": self.count, "min": self.minimum, "max": self.maximum,
                "mean": self.total / self.count, "deviation": sqrt(variance)}


@dataclass(frozen=True)
class _Observation:
    registers: tuple[str, ...]
    time: bool


class _SweepStatistics:
    def __init__(self, selection: str | _Observation):
        self.legacy = isinstance(selection, str)
        observation = _Observation((selection,), True) if isinstance(selection, str) else selection
        self.statuses = Counter()
        self.completed = 0
        self.registers = {name: (_Moments(), _Moments()) for name in observation.registers}
        self.time = _Moments() if observation.time else None

    def add(self, case: dict) -> None:
        status = case["status"]
        self.statuses[status] += 1
        self.completed += case["engine_completed"]
        for name, (signed, unsigned) in self.registers.items():
            word = case["word"] if self.legacy else case["words"][name]
            if word is not None:
                unsigned.add(word)
                signed.add(word if word < 2**31 else word - 2**32)
        if (self.time is not None and case["engine_completed"] and status in {"completed", "undetermined"}
                and case["q1_ns"] is not None):
            self.time.add(case["q1_ns"])

    def result(self) -> dict:
        result = {"count": sum(self.statuses.values()), "statuses": dict(self.statuses), "engine_completed": self.completed}
        registers = {name: {"signed": signed.result(), "unsigned": unsigned.result()}
                     for name, (signed, unsigned) in self.registers.items()}
        if self.legacy:
            result.update(next(iter(registers.values())))
        else:
            result["registers"] = registers
        if self.time is not None:
            result["time"] = self.time.result()
        return result


class RunSnapshot:
    def __init__(self, args, *, include_all_defs: bool = True):
        project = Path(args.project).resolve()
        project_bytes = project.read_bytes()
        self.config = config = load_project_config(project)
        mode = args.execution_mode or config.analysis.execution_mode
        self.mode = "concrete" if mode == "analysis" else mode
        self.max_steps = config.analysis.max_steps if args.max_steps is None else args.max_steps
        if self.max_steps < 1:
            raise ValueError("The execution instruction limit must be positive.")
        self.strict = args.q1asm_strict
        self.hardware_readiness = args.hardware_readiness
        params = _load_json_override(Path(args.params)) if args.params else config.params
        self.params = {**params, **_parse_param_overrides(args.set_param)}
        self.programs = {}
        self.def_rows = {}
        self.def_values = {}
        self.sources = {}
        self.names = {}
        self.memories = {}
        self.external = {seq.id: config.concrete_inputs(seq, self.params) for seq in config.sequencers} if self.mode == "concrete" else {}
        self.diagnostics = list(config.diagnostics)
        self.include_all_defs = include_all_defs
        source_bytes = {}
        for seq in config.sequencers:
            file = seq.file.resolve()
            if file not in source_bytes:
                source_bytes[file] = file.read_bytes()
            source = source_bytes[file]
            self.programs[seq.id] = parse_q1asm(source.decode("utf-8"), file=str(file))
            rows = self._rows_for(self.programs[seq.id])
            self.def_rows[seq.id] = rows
            self.def_values[seq.id] = {row["name"]: int(row["value"]) if row["value"] is not None else None
                                       for row in rows if row["value"] is not None or row.get("needs_input")}
            self.sources[seq.id] = {"file": str(file), "uri": file.as_uri(), "sha256": hashlib.sha256(source).hexdigest()}
            if seq.sequence_json:
                self.names[seq.id] = load_sequence_names(seq.sequence_json)
                self.diagnostics.extend(self.names[seq.id].diagnostics)
                if self.mode == "simulation":
                    self.memories[seq.id] = load_sequence_data(seq.sequence_json)
        if project.read_bytes() != project_bytes:
            raise ValueError("The project changed while reading it. Try again.")
        identity = [str(project), hashlib.sha256(project_bytes).hexdigest(), asdict(config), self.sources,
                    {name: asdict(value) for name, value in self.names.items()}, self.memories, self.params,
                    self.mode, self.max_steps, self.strict, self.hardware_readiness]
        self.revision = hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest()

    def _rows_for(self, program: Program) -> list[dict]:
        rows = _def_rows(program, self.params, self.strict)
        if self.include_all_defs:
            return rows
        literals = numeric_defs(program)
        return [row for row in rows if row["name"] in literals]

    def info(self, source_drafts: dict[str, str] | None = None) -> dict:
        if source_drafts is not None and (
            not isinstance(source_drafts, dict) or not all(isinstance(source, str) for source in source_drafts.values())
        ):
            raise ValueError("Source drafts must map file paths to text.")
        drafts = {str(Path(file).resolve()): source for file, source in (source_drafts or {}).items()}
        sequencers = []
        for name, program in self.programs.items():
            source = self.sources[name]
            file = source["file"]
            rows = self.def_rows[name]
            if file in drafts:
                program = parse_q1asm(drafts[file], file=file)
                rows = self._rows_for(program)
            sequencers.append({"id": name, **source, "registers": _referenced_registers(program, self.params),
                               "defs": rows})
        return {"revision": self.revision, "mode": self.mode, "max_steps": self.max_steps, "sequencers": sequencers}

    def target(self, request: dict) -> tuple[str, Program]:
        target = request.get("sequencer")
        if target not in self.programs or request.get("uri") != self.sources[target]["uri"]:
            raise ValueError("The selected sequencer or source has changed. Reopen the DEF controls.")
        return target, self.programs[target]

    def inputs(self, request: dict) -> tuple[str, str | _Observation, dict[str, int]]:
        target, _ = self.target(request)
        if "registers" in request or "observe_time" in request:
            registers, time = request.get("registers"), request.get("observe_time")
            if ("register" in request or not isinstance(registers, list) or len(registers) > 64
                    or any(not isinstance(name, str) or name not in REGISTERS for name in registers)
                    or len(set(registers)) != len(registers) or not isinstance(time, bool)):
                raise ValueError("Select distinct observation registers R0 through R63 and whether to observe Q1 time.")
            if request.get("command") == "sweep" and not registers and not time:
                raise ValueError("Select at least one register or Q1 time to sweep.")
            selection = _Observation(tuple(registers), time)
        else:
            # Older extensions use a single register and always request Q1 time.
            selection = request.get("register")
            if not isinstance(selection, str) or selection not in REGISTERS:
                raise ValueError("Select an observation register R0 through R63.")
        defs = self.def_values[target]
        overrides = request.get("overrides", {})
        if not isinstance(overrides, dict) or overrides.keys() - defs.keys():
            raise ValueError("Only editable numeric DEFs in the selected sequencer can be overridden.")
        values = {name: value for name, value in defs.items() if value is not None}
        values.update({name: _literal(raw, name) for name, raw in overrides.items()})
        return target, selection, values

    def validate_sweep_source(self, target: str, values: dict[str, int]):
        for name, program in self.programs.items():
            if name == target:
                program = self._case_program(target, values)
            literal_operands = {(instruction.source.line, arg.raw) for instruction in program.instructions
                                for arg in instruction.args if arg.kind in {"imm", "label"}}
            for item in diagnose_program_source(program, params=self.params, strict_q1asm=self.strict):
                if item.severity != "error":
                    continue
                operand = str(item.details.get("operand", ""))
                # Resolved numeric values can depend on the sweep. Structural errors
                # and range errors on operands written literally are independent.
                if item.category == "invalid_argument_value" and operand and (
                        item.source is None or (item.source.line, operand) not in literal_operands):
                    continue
                if item.category == "unresolved_symbol" and not operand.startswith("$"):
                    continue
                raise ValueError(f"Fix the source before sweeping. {item.message}")

    def _case_program(self, target: str, values: dict[str, int]) -> Program:
        program = self.programs[target]
        if all(self.def_values[target].get(name) == value for name, value in values.items()):
            return program
        # Other selected inputs keep their fixed values, even if a template
        # references a DEF that changed for this case.
        return with_def_values(program, values)

    def prepare_statistics(self, target: str, values: dict[str, int], axes: set[str]) -> dict[str, PreparedDefProgram]:
        programs = {**self.programs, target: self._case_program(target, values)}
        return {name: prepare_def_program(program, params=self.params, strict_q1asm=self.strict,
                                          varying=axes if name == target else set())
                for name, program in programs.items()}

    def run(self, target: str, selection: str | _Observation, values: dict[str, int], *, detail: bool = False, case_id: int = 1,
            prepared: dict[str, PreparedDefProgram] | None = None) -> dict:
        programs = {**self.programs, target: self._case_program(target, values)}
        config = self.config
        if self.mode == "simulation":
            states = simulate_programs(programs, params=self.params, max_steps=self.max_steps,
                                       simulation=config.simulation, sequences=self.memories, strict_q1asm=self.strict,
                                       prepared_programs=prepared,
                                       hardware_parameters={seq.id: seq.hardware_parameters for seq in config.sequencers},
                                       sequencer_metadata=config.sequencer_metadata())
        else:
            states = [interpret_program(program, sequencer_id=name, params=self.params,
                                        waveform_names=self.names[name].waveforms if name in self.names else None,
                                        acquisition_names=self.names[name].acquisitions if name in self.names else None,
                                        execution_mode="concrete", max_steps=self.max_steps,
                                        strict_q1asm=self.strict, branch_policy=config.analysis.branch_policy,
                                        prepared=prepared[name] if prepared else None, external=self.external[name])
                      for name, program in programs.items()]
        for state, seq in zip(states, config.sequencers):
            state.metadata.update(module=seq.module, module_type=seq.module_type,
                                  sequencer_index=seq.sequencer_index, hardware_parameters=seq.hardware_parameters)
            if self.mode != "simulation" and seq.id in self.names:
                state.metadata["weight_lengths"] = self.names[seq.id].weight_lengths
        if self.mode != "simulation" and (prepared is None or any(program.needs_queue_analysis for program in prepared.values())):
            analyze_underflows(states)
        analyze_project_hardware_readiness(states, config.sequencers, include_optional_checks=self.hardware_readiness)
        observed = next(state for state in states if state.sequencer_id == target)
        execution = observed.metadata["execution"]
        related = states if self.mode == "simulation" else [observed]
        incomplete = next((state.metadata["execution"] for state in related
                           if state.metadata["execution"]["status"] != "completed"), None)
        diagnostics = [*self.diagnostics, *(item for state in related for item in state.diagnostics)]
        status = execution["status"]
        reason = execution.get("reason")
        if status == "completed" and incomplete:
            status, reason = incomplete["status"], incomplete.get("reason")
        if status == "completed" and any(item.severity == "error" for item in diagnostics):
            status = "error"
        legacy = isinstance(selection, str)
        observation = _Observation((selection,), True) if isinstance(selection, str) else selection
        words = dict.fromkeys(observation.registers)
        if status == "completed" and words:
            pending_registers = {write.register for write in observed.pending_register_writes}
            for name in words:
                word = observed.registers.get(name)
                if isinstance(word, Concrete) and name not in pending_registers:
                    words[name] = word.value & 0xFFFFFFFF
            if all(word is None for word in words.values()):
                status = "undetermined"
                if len(words) == 1:
                    name = next(iter(words))
                    reason = f"{name} has a pending final write." if name in pending_registers else f"{name} has no readable final value."
                else:
                    reason = "None of the selected registers has a readable final value."
        case = {"status": status, "engine_completed": incomplete is None}
        if legacy:
            case["word"] = words[selection]
        else:
            case["words"] = words
        if observation.time:
            q1_ns = observed.q1_time_ns
            case["q1_ns"] = q1_ns.value if isinstance(q1_ns, Concrete) else None
            if status == "completed" and not words and case["q1_ns"] is None:
                case.update(status="undetermined")
                reason = "The full Q1 time is unavailable."
        if prepared is not None and not detail:
            return case
        case.update(id=case_id, defs={name: str(value) for name, value in values.items()},
                    reason=reason, diagnostics=[_diagnostic_to_dict(item) for item in diagnostics])
        if not detail:
            return case
        alignment_mode = config.alignment_for_execution(self.mode)
        align_timelines(states, mode=alignment_mode, anchor_kinds=config.alignment_anchor_kinds)
        ir = timeline_ir_from_states(states, project={"root": str(config.root),
                                    "alignment_mode": alignment_mode, "alignment_anchor_kinds": list(config.alignment_anchor_kinds),
                                    "feedback_transport_delay_ns": config.analysis.feedback_transport_delay_ns},
                                    feedback_routes=config.simulation.get("routes", ()))
        pending = [{"register": write.register, "event_id": write.event_id, "value": value_to_json(write.value),
                    "ready_ns": write.meta.get("scheduled_ready_ns")}
                   for write in observed.pending_register_writes]
        return {"case": case, "ir": ir, "pending": pending}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--execution-mode", choices=("analysis", "concrete", "simulation"))
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--set-param", action="append", default=[])
    parser.add_argument("--params")
    parser.add_argument("--q1asm-strict", action="store_true")
    parser.add_argument("--hardware-readiness", action="store_true")
    args = parser.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    snapshot = None
    sweep = None
    include_all_defs = True
    for line in sys.stdin:
        request = json.loads(line)
        try:
            command = request["command"]
            statistics_only = request.get("statistics_only", False)
            if not isinstance(statistics_only, bool):
                raise ValueError("statistics_only must be a boolean.")
            if command == "info":
                include_all_defs = request.get("include_all_defs") is True
            if command in {"info", "run", "sweep"}:
                snapshot = RunSnapshot(args, include_all_defs=include_all_defs)
                sweep = None
                if command != "info" and snapshot.revision != request.get("revision"):
                    raise ValueError("The project, source, or execution settings changed. Reload DEFs and start a new run.")
            if command == "info":
                result = {"info": snapshot.info(request.get("source_drafts"))}
            elif command == "plan":
                target, _ = snapshot.target(request)
                result = {"plan": sweep_plan(request["axes"], snapshot.def_values[target], statistics_only=statistics_only)}
            elif command == "run":
                target, selection, values = snapshot.inputs(request)
                result = snapshot.run(target, selection, values, detail=True, case_id=request.get("case_id", 1))
            elif command == "sweep":
                target, selection, values = snapshot.inputs(request)
                plan = sweep_plan(request["axes"], snapshot.def_values[target], statistics_only=statistics_only)
                sample_count = request.get("sample_count")
                if "sample_count" in request:
                    if type(sample_count) is not int or not 1 <= sample_count <= plan["sample_limit"]:
                        raise ValueError(f"Sample count must be an integer from 1 to {plan['sample_limit']:,}.")
                    plan.update(sampling="random", total=str(sample_count), allowed=True)
                elif not plan["allowed"]:
                    raise ValueError(f"This sweep mode supports up to {plan['limit']:,} combinations.")
                for axis in plan["axes"]:
                    values[axis["name"]] = int(axis["min"])
                snapshot.validate_sweep_source(target, values)
                names = [axis["name"] for axis in plan["axes"]]
                ranges = [range(int(axis["min"]), int(axis["last"]) + 1, int(axis["step"])) for axis in plan["axes"]]
                if sample_count is not None:
                    rng = Random()
                    spans = [(span, int(axis["count"])) for span, axis in zip(ranges, plan["axes"])]
                    points = (tuple(span[rng.randrange(count)] for span, count in spans) for _ in range(sample_count))
                else:
                    points = ((x,) for x in ranges[0]) if len(ranges) == 1 else ((x, y) for x in ranges[0] for y in ranges[1])
                statistics = _SweepStatistics(selection) if statistics_only else None
                prepared = snapshot.prepare_statistics(target, values, set(names)) if statistics_only else None
                sweep = enumerate(points, 1)
                result = {"plan": plan, "defs": {name: str(value) for name, value in values.items()}}
            elif command == "batch" and sweep is not None:
                rows = []
                deadline = monotonic() + 0.12
                for case_id, point in itertools.islice(sweep, 512 if statistics else 16):
                    case = snapshot.run(target, selection, {**values, **dict(zip(names, point))},
                                        case_id=case_id, prepared=prepared)
                    if statistics is None:
                        rows.append(case)
                    else:
                        statistics.add(case)
                    if monotonic() >= deadline:
                        break
                result = {"summary": statistics.result()} if statistics else {"rows": rows}
            else:
                raise ValueError(f"Unknown DEF run request: {command}")
        except ConfigLoadError as exc:
            result = {"error": "; ".join(item.message for item in exc.diagnostics) or str(exc)}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result = {"error": str(exc)}
        print(json.dumps({"id": request["id"], **result}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
