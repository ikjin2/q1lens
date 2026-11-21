"""Check every shipped Q1ASM example at each Concrete issue boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.project import AnalysisConfig, load_project_config
from q1timeline.q1asm import core
from q1timeline.q1asm.parser import parse_q1asm, parse_q1asm_file


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).with_name("fixtures") / "concrete_corpus.tsv"


def examples():
    roots = [ROOT / "examples", *(ROOT.glob("demo*/examples"))]
    cases = {}
    configured = set()
    for root in roots:
        for manifest in sorted(root.rglob("q1timeline.y*ml")):
            if any(part.startswith(".") for part in manifest.relative_to(root).parts):
                continue  # Local schedule compilation products, e.g. .qbs_timeline.
            project = load_project_config(manifest)
            for seq in project.sequencers:
                key = f"{manifest.relative_to(ROOT).as_posix()}#{seq.id}"
                cases[key] = seq.file, project.params, project.concrete_inputs(seq, project.params), project.analysis.max_steps
                configured.add(seq.file)
    for root in roots:
        for source in root.rglob("*.q1asm"):
            if not any(part.startswith(".") for part in source.relative_to(root).parts) and source not in configured:
                cases[source.relative_to(ROOT).as_posix()] = source, {}, None, AnalysisConfig().max_steps
    return cases


CASES = examples()
SCHEDULES = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "examples").rglob("qbstimeline.y*ml")
                   if not any(part.startswith(".") for part in p.relative_to(ROOT).parts))


def trace_rows(live):
    packets = {p.id: p for p in live.rt_packets}
    applied = {e.meta["rt_packet_id"]: e.meta["applied_state"] for e in live.events
               if "applied_state" in e.meta and "rt_packet_id" in e.meta}
    rows = []
    for event in live.events:
        if event.kind != "q1_issue":
            continue
        meta = event.meta
        packet = packets.get(meta.get("rt_packet_id"))
        rows.append((
            meta.get("compiled_pc", meta["source_pc"]), meta["op"], event.t0.value, event.t1.value,
            {k: v["value"] for k, v in meta["register_snapshot"].items() if v["kind"] == "concrete"},
            meta["flags_after"],
            (packet.op, packet.rt_t0.value, packet.rt_t1.value, applied.get(packet.id, {})) if packet else None,
        ))
    return rows


def state_registers(state):
    return {f"R{i}": word for i, word in enumerate(state.registers) if word is not None}


def compare_step(before, after, packet, row):
    pc, op, q1_start, q1_end, registers, flags, rt = row
    assert (before.pc, before.q1_ns, after.q1_ns) == (pc, q1_start, q1_end)
    assert state_registers(before) == registers
    assert (after.flags.as_dict() if after.flags else None) == flags
    if rt is None:
        assert packet is None and after.rt_ns == before.rt_ns
    else:
        assert packet is not None
        assert (packet.op, packet.rt_t0, packet.rt_t1) == rt[:3]
        assert packet.op == op
        assert (packet.q1_issue_t0, packet.q1_issue_t1) == (q1_start, q1_end)
        captured = {w.field: list(w.value) if isinstance(w.value, tuple) else w.value for w in packet.applied}
        assert captured == rt[3]


def digest(rows):
    encoded = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def endpoint(live):
    execution = live.metadata["execution"]
    pc = execution.get("compiled_pc", execution.get("pc", "-"))
    return "/".join(map(str, (execution["status"], execution["steps"], pc, execution.get("stop_code", "-"),
                              live.q1_time_ns.value, live.rt_time_ns.value)))


@pytest.fixture(scope="module")
def golden():
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    return {case: (int(count), terminal, boundary, checksum, binding, expected_end)
            for case, count, terminal, boundary, checksum, binding, expected_end in
            (line.split("\t") for line in lines if not line.startswith("#"))}


def test_all_shipped_examples_have_an_independent_reference(golden):
    assert CASES.keys() == {key for key in golden if key.partition("#")[0] not in SCHEDULES}


def test_project_integer_strings_bind_in_defs_and_instruction_operands():
    program = parse_q1asm(".DEF A {X}\nmove $A,R0\nnop\nadd R0,Y,R1\nnop\nstop R1")
    params = {"X": " +0b101 ", "Y": "0007"}
    state = core.CoreState()
    code = core.prepare_program(program, params=params)
    while not state.halted:
        state, _ = core.step(state, code)
    live = interpret_program(program, sequencer_id="s", params=params, execution_mode="concrete")
    assert state.stop_code == live.metadata["execution"]["stop_code"] == 12
    with pytest.raises(ValueError, match="requires a concrete operand"):
        core.prepare_program(program, params={"X": True, "Y": 7})


@pytest.mark.parametrize("case", sorted(CASES))
def test_example_concrete_core_trace(case, golden, monkeypatch):
    check_concrete_core_trace(*CASES[case], golden[case], monkeypatch)


@pytest.mark.parametrize("manifest", SCHEDULES)
def test_compiled_schedule_core_traces(manifest, tmp_path, golden, monkeypatch):
    subprocess.run(
        [sys.executable, "-m", "qbstimeline", "analyze", "--project", str(ROOT / manifest),
         "--out-dir", str(tmp_path), "--out", str(tmp_path / "qbs_ir.json")],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, check=True,
    )
    project = load_project_config(tmp_path / "q1timeline.yml")
    expected = {key for key in golden if key.startswith(manifest + "#")}
    assert expected and expected == {f"{manifest}#{seq.id}" for seq in project.sequencers}
    for seq in project.sequencers:
        check_concrete_core_trace(seq.file, project.params, project.concrete_inputs(seq, project.params),
                                  project.analysis.max_steps, golden[f"{manifest}#{seq.id}"], monkeypatch)


def check_concrete_core_trace(path, params, external, max_steps, reference, monkeypatch):
    program = parse_q1asm_file(path)
    calls = []
    step = core.step

    def record(before, *args, **kwargs):
        result = step(before, *args, **kwargs)
        after, packet = result
        calls.append((before, after, packet))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(core, "step", record)
        live = interpret_program(program, sequencer_id="s", params=params, external=external,
                                 execution_mode="concrete", max_steps=max_steps)
    rows = trace_rows(live)
    execution = live.metadata["execution"]
    assert len(calls) == len(rows) == execution["steps"]
    for call, row in zip(calls, rows):
        compare_step(*call, row)
    if calls:
        after = calls[-1][1]
        assert (after.q1_ns, after.rt_ns) == (live.q1_time_ns.value, live.rt_time_ns.value)
        assert state_registers(after) == {k: v.value for k, v in live.registers.items()}
        assert after.stop_code == execution.get("stop_code")
        assert after.fault == execution.get("fault", {}).get("flag")

    # Frozen prefixes retain the pre-migration control, register and RT behavior
    # with q1simulator 1.4.1 Q1 costs. Device support may extend the prefix.
    count, terminal, boundary, checksum, binding, expected_end = reference
    assert len(rows) >= count
    assert digest(rows[:count]) == checksum
    if terminal == "unsupported" and len(rows) > count:
        assert (rows[count][0], rows[count][1]) == (int(boundary.split("/")[0]), boundary.split("/")[1])
    else:
        assert execution["status"] == ("blocked" if terminal == "unsupported" else terminal)
        assert len(rows) == count
        assert str(execution.get("compiled_pc", execution.get("pc", "-"))) == boundary.split("/")[0]
        if terminal == "completed":
            assert execution.get("stop_code") == int(boundary.split("/")[1])
    if terminal == "unsupported":
        assert "has no supported concrete execution semantics" not in execution.get("reason", "")
        assert endpoint(live) == expected_end

    # Strict preparation needs bound inputs across the whole source. The
    # permissive runner above exercises the reached prefix even for these cases.
    if binding != "prepared":
        with pytest.raises(ValueError, match=binding):
            core.prepare_program(program, params=params)
        return
    code = core.prepare_program(program, params=params)
    strict = live
    if any(d.details.get("source_line_skipped") for d in live.diagnostics):
        strict = interpret_program(program, sequencer_id="s", params=params, external=external, execution_mode="concrete",
                                   strict_q1asm=True, max_steps=max_steps)
    state = core.CoreState()
    for row in trace_rows(strict):
        assert code[state.pc].op == row[1]
        next_state, packet = step(state, code, external=external)
        compare_step(state, next_state, packet, row)
        state = next_state
    assert state_registers(state) == {k: v.value for k, v in strict.registers.items()}
    assert state.stop_code == strict.metadata["execution"].get("stop_code")
    assert state.fault == strict.metadata["execution"].get("fault", {}).get("flag")
    assert (state.q1_ns, state.rt_ns) == (strict.q1_time_ns.value, strict.rt_time_ns.value)
    end = strict.metadata["execution"]
    if end["status"] in {"blocked", "step_limit"}:
        assert state.pc == end.get("compiled_pc", end["pc"])
    if end["status"] == "blocked":
        with pytest.raises(ValueError):
            step(state, code, external=external)


# These six finite examples exceed their normal project limit. DEF statistics mode
# executes them to stop without retaining hundreds of thousands of display events.
@pytest.mark.parametrize(("source", "count", "q1_ns", "rt_ns", "packets_hash"), [
    ("initial-buffer-demo/01-assumed-0_5ms.q1asm", 27784, 500016, 1004,
     "f9ca06957fbb871c67a650889749491f83cbb6566e15cec8a8114e533f8c5c11"),
    ("initial-buffer-demo/02-tentative-1_5ms.q1asm", 83340, 1500024, 1004,
     "851f6fee49eb3cb233cb004b79e8c5e800a9a00f1f61e64668b840e84b21fa5c"),
    ("initial-buffer-demo/03-underflow-2_5ms.q1asm", 138894, 2499996, 1004,
     "33a0f3f39785ddb331932da84c508f9a111f537ab2a5f93a1fc3cbe218471e23"),
    ("local-changes-review/08-rt-buffer/sequence.q1asm", 178576, 3214300, 1004,
     "d033ff32a59a2d27cb62422c69dbe28500d53d46088ef87541b8b4b685a09650"),
    ("q1asm-tutorials/05-common-mistakes/18-too-tight-loop/seq0.q1asm", 540674, 2392060, 32768,
     "b252bebc5f1abce361caddc43b5544ef1cf6b80c21e020e5442d58c523bf6679"),
    ("q1asm-tutorials/07-timing-pathologies/27-short-loop-underflow/seq0.q1asm", 540674, 2392060, 32768,
     "b252bebc5f1abce361caddc43b5544ef1cf6b80c21e020e5442d58c523bf6679"),
])
def test_long_countdown_reaches_its_reference_stop(source, count, q1_ns, rt_ns, packets_hash):
    program = parse_q1asm_file(ROOT / "examples" / source)
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete", prepared=prepared, max_steps=count)
    end = live.metadata["execution"]
    assert (end["status"], end["steps"], end["stop_code"]) == ("completed", count, 0)
    assert (live.q1_time_ns.value, live.rt_time_ns.value) == (q1_ns, rt_ns)
    assert {k: v.value for k, v in live.registers.items()} == {"R0": 0}
    assert live.alu_flags == {"zf": True, "nf": False, "cf": False, "of": False}
    assert digest([(p.op, p.duration.value, p.q1_issue_t0.value, p.q1_issue_t1.value, p.rt_t0.value, p.rt_t1.value)
                   for p in live.rt_packets]) == packets_hash
