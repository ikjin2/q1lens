"""Export actual Q1Lens simulation events for the standalone execution-flow demo.

This module projects engine records; it does not execute Q1ASM itself. The only
instrumentation observes the engine's flag-writer identity and delegates the
flag update unchanged. Scenario inputs are explicit, synthetic values.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from q1timeline.analysis import interpreter as core
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs

DEMO = Path(__file__).resolve().parent
OUTPUT = ROOT / "output" / "execution-flow"
CASES = [
    ("retry", "Bounded retry", "피드백 값이 달라지면 어느 분기로 이동할까?", "q1asm-tutorials/11-control-flow-patterns/35-bounded-retry-loop/seq0.q1asm"),
    ("bins", "Round-robin bins", "같은 덧셈 뒤에 왜 이번에는 0을 쓸까?", "q1asm-tutorials/10-stateful-programs/32-round-robin-bin-writer/seq0.q1asm"),
    ("latched", "Latched sweep", "루프마다 바뀌는 gain 설정과 레지스터를 함께 확인하세요.", "../demo-execution-flow/examples/latched-loop.q1asm"),
    ("golden", "Golden section", "계산된 값과 아직 실행할 수 없는 부분을 구분해 보세요.", "golden-section/golden_section.q1asm"),
]


def signed(value):
    if value is None:
        return None
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def concrete(value):
    if isinstance(value, Concrete):
        return value.value
    if isinstance(value, dict) and value.get("kind") == "concrete":
        return value["value"]
    return None


def configurations(case_id):
    base = {"startup_delay_ns": 200, "max_time_ns": 3_000_000}
    if case_id == "latched":
        return [{"id": "normal", "title": "gain 1,000 → 5,000", "inputLabel": "외부 입력 없음 · Q1Lens 엔진에서 계산한 설정 sweep", "simulation": base}]
    if case_id == "retry":
        return [
            {"id": name, "title": title, "inputLabel": label,
             "simulation": {**base, "inputs": {"feedback": [
                 {"target": case_id, "time_ns": 0, "id": 40, "value": value} for value in values
             ]}}}
            for name, title, label, values in [
                ("normal", "0 → 0 → 1", "가상 피드백 [0, 0, 1] · 모두 0 ns에 도착", [0, 0, 1]),
                ("early", "0 → 1", "가상 피드백 [0, 1] · 모두 0 ns에 도착", [0, 1]),
                ("missing", "0 → 0 → 입력 없음", "가상 피드백 [0, 0] · 세 번째 입력 없음", [0, 0]),
            ]
        ]
    if case_id == "bins":
        return [{"id": "normal", "title": "4회 순환", "inputLabel": "가상 acquisition 결과 I=0, Q=0 · bin 0, 1, 2, 0",
                 "simulation": {**base, "inputs": {"acquisitions": [
                     {"sequencer": case_id, "acquisition_index": 0, "bin_index": index, "i": 0, "q": 0}
                     for index in (0, 1, 2, 0)
                 ]}}}]
    return [
        {"id": "no_measurement", "title": "측정 입력 없음", "inputLabel": "실측·가상 acquisition 입력 모두 없음", "simulation": base},
        {"id": "provided_measurement", "title": "첫 측정 입력 제공", "inputLabel": "가상 I=2,000,000, Q=1,000,000 · self-feedback 지연 가정 164 ns",
         "simulation": {**base, "inputs": {"acquisitions": [
             {"sequencer": case_id, "acquisition_index": 0, "bin_index": 0, "i": 2_000_000, "q": 1_000_000}
         ]}, "routes": [{"source": case_id, "target": case_id, "id": 50, "latency_ns": 164}]}},
    ]


def run_engine(case_id, program, params, simulation, choices=None):
    flag_updates = {}
    original = core._set_alu_flags

    def observe_flags(state, instruction, flags):
        original(state, instruction, flags)
        flag_updates[state.q1_issue_index] = instruction.pc

    core._set_alu_flags = observe_flags
    try:
        state, = simulate_programs({case_id: program}, params=params, simulation=simulation,
                                   branch_choices=choices)
    finally:
        core._set_alu_flags = original
    return state, flag_updates


def project_scenario(case_id, program, params, config, choices=None):
    if choices is not None and not isinstance(choices, list):
        raise ValueError("choices must be a list of {pc, occurrence, taken} objects")
    selected = {}
    for choice in choices or []:
        if (not isinstance(choice, dict) or set(choice) != {"pc", "occurrence", "taken"}
                or type(choice["pc"]) is not int or type(choice["occurrence"]) is not int
                or type(choice["taken"]) is not bool):
            raise ValueError("Each choice requires integer pc/occurrence and boolean taken")
        key = (case_id, choice["pc"], choice["occurrence"])
        if key in selected:
            raise ValueError("A branch occurrence may only have one choice")
        selected[key] = choice["taken"]
    state, flag_updates = run_engine(case_id, program, params, config["simulation"], selected)
    issues = [event for event in state.events if event.kind == "q1_issue"]
    events = {event.id: event for event in state.events}
    instructions = {instruction.pc: instruction for instruction in program.instructions}
    writes_by_event = defaultdict(list)
    writes_by_register = defaultdict(list)
    latched_by_event = defaultdict(list)
    for event in state.events:
        if event.kind == "latched_state_pending":
            latched_by_event[event.meta.get("q1_issue_event_id")].append(event)
    for write in state.register_history:
        writes_by_event[write.event_id].append(write)
        writes_by_register[write.register].append(write)

    def writer_at(register, time):
        return next((write for write in reversed(writes_by_register[register])
                     if concrete(write.time) is not None and concrete(write.time) <= time), None)

    def operand_record(register, value, time):
        writer = writer_at(register, time)
        return {"register": register, "value": signed(concrete(value)), "raw": concrete(value),
                "writerId": writer.event_id if writer else None,
                "readyAt": concrete(writer.time) if writer else None}

    def arguments(event):
        recorded = event.meta.get("resolved_args", [])
        if recorded:
            return [{"index": item["index"], "raw": item["raw"], "kind": item["kind"],
                     "value": signed(concrete(item["value"])), "unsigned": concrete(item["value"])} for item in recorded]
        instruction = instructions[event.meta["source_pc"]]
        result = []
        for index, arg in enumerate(instruction.args):
            register = core._register_name(arg, state)
            value = event.meta.get("register_snapshot", {}).get(register) if register else None
            raw_value = concrete(value)
            if arg.kind == "imm":
                raw_value = int(arg.value)
            elif arg.kind == "label":
                raw_value = program.labels.get(str(arg.value))
            result.append({"index": index, "raw": arg.raw, "kind": arg.kind,
                           "value": signed(raw_value), "unsigned": raw_value})
        return result

    def line_at(pc):
        return instructions[pc].source.line if pc in instructions else None

    steps = []
    latched_values = {}
    occurrence = defaultdict(int)
    previous_flag_event = None
    for position, event in enumerate(issues):
        meta = event.meta
        instruction = instructions[meta["source_pc"]]
        start, end = concrete(event.t0), concrete(event.t1)
        reads = [operand_record(register, value, start) for register, value in meta.get("register_reads", {}).items()]
        writes = [{"register": write.register,
                   "before": signed(concrete(write.meta.get("previous_value"))),
                   "after": signed(concrete(write.value)), "beforeRaw": concrete(write.meta.get("previous_value")),
                   "afterRaw": concrete(write.value), "readyAt": concrete(write.time),
                   "expression": write.meta.get("provenance", {}).get("expression", "")}
                  for write in writes_by_event[event.id]]
        args = arguments(event)
        branch = None
        if "branch_taken" in meta:
            producer = event if len(instruction.args) == 3 or instruction.op == "loop" else previous_flag_event
            operands = arguments(producer)[:2] if producer else []
            producer_reads = producer.meta.get("register_reads", {}) if producer else {}
            for item in operands:
                producer_instruction = instructions[producer.meta["source_pc"]]
                register = core._register_name(producer_instruction.args[item["index"]], state)
                if register and register in producer_reads:
                    record = operand_record(register, producer_reads[register], concrete(producer.t0))
                    record["rawValue"] = record.pop("raw")
                    item.update(record)
            if instruction.op == "jmp":
                condition = "무조건 이동"
                producer, operands = None, []
            elif instruction.op == "loop" and writes:
                condition = f"{writes[0]['after']} ≠ 0"
                operands = reads
            elif len(instruction.args) == 3 and len(args) >= 2:
                operator = "≥" if instruction.op == "jge" else "<"
                condition = f"{args[0]['unsigned']} {operator} {args[1]['unsigned']}"
            elif producer and producer.meta["op"] == "cmp" and len(operands) == 2:
                operators = {"jl": "<", "jge": "≥", "jz": "=", "jnz": "≠"}
                condition = f"{operands[0]['value']} {operators.get(instruction.op, instruction.op)} {operands[1]['value']}"
            elif producer and instruction.op in {"jnz", "jz"} and writes_by_event[producer.id]:
                result = signed(concrete(writes_by_event[producer.id][0].value))
                condition = f"{result} {'≠' if instruction.op == 'jnz' else '='} 0"
            else:
                flags = meta.get("flags_before") or {}
                condition = " · ".join(f"{name.upper()}={int(value)}" for name, value in flags.items()) or "무조건 이동"
            branch = {"taken": meta["branch_taken"], "condition": condition,
                      "naturalTaken": meta.get("natural_branch_taken", meta["branch_taken"]),
                      "overridden": meta.get("branch_overridden", False),
                      "targetLine": line_at(meta.get("target_pc")),
                      "fallthroughLine": line_at(instruction.pc + 1),
                      "producerId": producer.id if producer else None, "operands": operands,
                      "flags": meta.get("flags_before") if instruction.op not in {"loop", "jmp"} and len(instruction.args) != 3 else None,
                      "comparisonMode": "unconditional" if instruction.op == "jmp" else
                      "decremented_counter" if instruction.op == "loop" else
                      "unsigned" if len(instruction.args) == 3 else "status_flags"}
        if meta["execution_index"] in flag_updates:
            previous_flag_event = event
        inputs = [events[item] for item in meta.get("input_event_ids", [])]
        input_info = None
        if inputs:
            receipt = inputs[0]
            input_info = {"label": f"가상 피드백 #{receipt.meta['input_index'] + 1}" if "input_index" in receipt.meta else "가상 acquisition에서 전달된 피드백",
                          "value": signed(receipt.meta["value"]), "raw": receipt.meta["value"], "kind": "scenario",
                          "eventId": receipt.id, "timeNs": concrete(receipt.t0), "channel": receipt.meta["id"]}
        occurrence[instruction.source.line] += 1
        latched = []
        for change in latched_by_event[event.id]:
            field, value = change.meta["field"], change.meta["value"]
            latched.append({"field": field, "before": latched_values.get(field), "after": value,
                            "readyAt": concrete(change.t0)})
            latched_values[field] = value
        steps.append({"id": event.id, "line": instruction.source.line, "pc": instruction.pc,
                      "op": instruction.op, "text": instruction.source.raw.split("#", 1)[0].strip(),
                      "occurrence": occurrence[instruction.source.line], "reads": reads, "writes": writes,
                      "pendingWrites": [{"register": write.register, "value": signed(concrete(write.value))}
                                        for write in state.pending_register_writes if write.event_id == event.id],
                      "resolvedArgs": args, "branch": branch, "input": input_info,
                      "latched": latched,
                      "nextLine": issues[position + 1].source.line if position + 1 < len(issues) else None,
                      "q1Start": start, "q1End": end,
                      "snapshot": {register: signed(concrete(value)) for register, value in meta.get("register_snapshot", {}).items()}})
    execution = state.metadata["execution"]
    source = execution.get("source")
    status_line = source["line"] if source else line_at(execution.get("pc"))
    pending = None
    if execution["status"] != "completed" and status_line:
        instruction = next(item for item in program.instructions if item.source.line == status_line)
        pending = {"line": status_line, "op": instruction.op, "text": instruction.source.raw.split("#", 1)[0].strip(),
                   "reason": execution.get("reason", "")}
    return {"id": config["id"], "title": config["title"], "inputLabel": config["inputLabel"],
            "status": execution["status"], "reason": execution.get("reason", ""),
            "statusLine": status_line, "pending": pending, "steps": steps,
            "choices": [{"pc": pc, "occurrence": occurrence, "taken": taken}
                        for (_, pc, occurrence), taken in selected.items()],
            "engine": "q1timeline.simulation.runtime.simulate_programs", "engineAssumptions": execution["assumptions"],
            "scenario": config["simulation"],
            "diagnostics": [{"category": d.category, "message": d.message, "line": d.source.line if d.source else None}
                            for d in state.diagnostics],
            "stalls": [{"reason": event.meta.get("reason"), "start": concrete(event.t0), "end": concrete(event.t1),
                        "pending": event.meta.get("pending", False), "line": event.source.line if event.source else None}
                       for event in state.events if event.kind == "q1_stall"]}


def loop_definitions(program):
    """Group static backward edges by their common entry instruction."""
    loops = {}
    instructions = {item.pc: item for item in program.instructions}
    for instruction in program.instructions:
        if instruction.op not in {"loop", "jmp", "jlt", "jl", "jge", "jnz", "jz"} or not instruction.args:
            continue
        arg = instruction.args[-1]
        target = program.labels.get(str(arg.value)) if arg.kind == "label" else arg.value if arg.kind == "imm" else None
        if not isinstance(target, int) or target > instruction.pc or target not in instructions:
            continue
        loop = loops.setdefault(target, {"id": str(target), "name": str(arg.value) if arg.kind == "label" else f"L{instructions[target].source.line}",
                                         "startPc": target, "endPc": instruction.pc, "endPcs": [],
                                         "startLine": instructions[target].source.line, "endLine": instruction.source.line})
        loop["endPc"] = max(loop["endPc"], instruction.pc)
        loop["endLine"] = max(loop["endLine"], instruction.source.line)
        loop["endPcs"].append(instruction.pc)
    return sorted(loops.values(), key=lambda loop: loop["startPc"])


def build_data():
    cases = []
    for case_id, title, description, relative in CASES:
        path = ROOT / "examples" / relative
        text = path.read_text(encoding="utf-8-sig")
        params_path = path.with_name("params.json")
        params = json.loads(params_path.read_text()) if params_path.exists() else {}
        program = parse_q1asm(text, file=str(path))
        aliases = core._register_aliases(program.defs)
        cases.append({"id": case_id, "title": title, "description": description,
                      "source": {"path": str(path), "lines": text.splitlines()}, "aliases": aliases,
                      "params": params, "loops": loop_definitions(program),
                      "scenarios": [project_scenario(case_id, program, params, config) for config in configurations(case_id)]})
    return {"cases": cases, "provenance": "Exported from this checkout's Q1Lens simulation engine; no browser execution engine."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true", help="Build only (the default).")
    parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    data = build_data()
    (OUTPUT / "data.js").write_text("window.EXECUTION_FLOW_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    for name in ("index.html", "style.css", "history.js", "app.js"):
        if (DEMO / name).exists():
            shutil.copyfile(DEMO / name, OUTPUT / name)
    print(OUTPUT / "index.html")
    for case in data["cases"]:
        for scenario in case["scenarios"]:
            print(case["id"], scenario["id"], scenario["status"], len(scenario["steps"]), scenario["reason"])


if __name__ == "__main__":
    main()
