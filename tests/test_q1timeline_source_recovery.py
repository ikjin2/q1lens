import json

import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.cli import main
from q1timeline.playback import PlaybackSession
from q1timeline.render.html import render_html


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("bad_line,category", [
    ("unknown_opcode", "unknown_instruction"),
    ("move 1 R0", "syntax_error"),
    ("wait 4; stop", "syntax_error"),
    ("play 0,1", "invalid_argument_count"),
    ("move 1,R64", "invalid_argument_type"),
    ("wait -1", "invalid_argument_value"),
    ("jmp @missing", "undefined_label"),
    ("move $MISSING,R0", "unresolved_symbol"),
])
def test_invalid_line_is_excluded_without_changing_remaining_timing(mode, bad_line, category):
    source = f"wait 1000\n{bad_line}\nwait 2000\nstop\n"
    ir = analyze_text(source, file="recovery.q1asm", execution_mode=mode)
    clean = analyze_text("wait 1000\nwait 2000\nstop\n", execution_mode=mode)

    issues = [event for event in ir["events"] if event["kind"] == "q1_issue"]
    clean_issues = [event for event in clean["events"] if event["kind"] == "q1_issue"]
    assert [event["source"]["line"] for event in issues] == [1, 3, 4]
    assert [(event["t0"], event["t1"]) for event in issues] == [
        (event["t0"], event["t1"]) for event in clean_issues
    ]
    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [1, 3]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == category]
    assert diagnostic["severity"] == "error"
    assert diagnostic["source"]["line"] == 2
    assert diagnostic["details"]["source_line_skipped"] is True
    assert "Timeline excludes 1 invalid source line" in render_html(ir)
    if mode != "analysis":
        assert ir["execution"]["recovery"]["status"] == "completed"


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_invalid_label_removal_also_excludes_branches_to_it(mode):
    ir = analyze_text("jmp @bad\nwait 1000\nbad: bogus\nwait 2000\nstop", execution_mode=mode)

    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [2, 4]
    assert {item["source"]["line"] for item in ir["diagnostics"]
            if item["details"].get("source_line_skipped")} == {1, 3}


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_label_only_target_and_loop_survive_removing_an_invalid_instruction(mode):
    ir = analyze_text(
        "move 2,R0\nnop\nbody:\nbogus\nwait 1000\nloop R0,@body\nwait 2000\nstop",
        execution_mode=mode,
    )

    assert any(event["kind"] == "wait" and event["source"]["line"] == 7 for event in ir["events"])
    assert not any(event["kind"] == "q1_issue" and event["source"]["line"] == 4 for event in ir["events"])
    if mode != "analysis":
        assert next(iter(ir["execution"].values()))["status"] == "completed"


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("source", ["bogus", "wait 1000\nbogus", "move 1 R0"])
def test_recovery_can_reach_end_of_file_without_a_stop(mode, source):
    ir = analyze_text(source, execution_mode=mode)

    assert any(item["details"].get("source_line_skipped") for item in ir["diagnostics"])
    assert not any("without an instruction" in item["message"] for item in ir["diagnostics"])
    if mode != "analysis":
        assert next(iter(ir["execution"].values()))["status"] == "completed"


def test_playback_runs_remaining_lines_and_keeps_error_diagnostics():
    session = PlaybackSession({"seq0": ("recovery.q1asm", "wait 1000\nbogus\nwait 2000\nstop")})
    session.run()
    ir = session.snapshot()

    assert ir["playback"]["status"] == "completed"
    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [1, 3]
    assert ir["diagnostics"][0]["details"]["source_line_skipped"] is True


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_label_aliases_use_addresses_of_the_remaining_instructions(mode):
    ir = analyze_text(
        ".DEF DEST @end\nbogus\njmp $DEST\nwait 1000\nend: wait 2000\nstop",
        execution_mode=mode,
    )

    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [5]


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_duplicate_label_line_does_not_remove_the_original_label(mode):
    ir = analyze_text(
        "jmp @entry\nwait 1000\nentry: wait 2000\nentry: wait 3000\nstop",
        execution_mode=mode,
    )

    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [3]
    assert {item["source"]["line"] for item in ir["diagnostics"]
            if item["details"].get("source_line_skipped")} == {4}


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_invalid_setter_preserves_previous_valid_hardware_state(mode):
    ir = analyze_text("set_mrk 1\nset_mrk 16\nupd_param 4\nstop", execution_mode=mode)

    update, = [event for event in ir["events"] if event["kind"] == "upd_param"]
    assert update["meta"]["applied_state"]["marker"] == 1
    assert not any(event["kind"] == "q1_issue" and event["source"]["line"] == 2 for event in ir["events"])


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_cli_keeps_nonzero_error_status_and_writes_renderable_recovered_timeline(tmp_path, capsys, mode):
    source = tmp_path / "recovery.q1asm"
    source.write_text("wait 1000\nbogus\nmove 1 R0\nplay 0,1\nwait 2000\nstop", encoding="utf-8")
    ir_path = tmp_path / "timeline_ir.json"
    html_path = tmp_path / "timeline.html"

    assert main(["analyze", str(source), "--out", str(ir_path), "--format", "vscode-json",
                 "--execution-mode", mode, "--no-render"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert all(item["details"].get("source_line_skipped") for item in payload["diagnostics"]
               if item["severity"] == "error")
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [1, 5]
    assert main(["render", "--ir", str(ir_path), "--out", str(html_path)]) == 0
    assert "Timeline excludes 3 invalid source lines" in html_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_excluded_loop_does_not_change_register_branch_address_semantics(mode):
    source = "loop R0\nmove @done,R0\nnop\njmp R0\nwait 1000\ndone: wait 2000\nstop"
    if mode == "playback":
        session = PlaybackSession({"seq0": ("recovery.q1asm", source)})
        assert session.run()["status"] == "completed"
        ir = session.snapshot()
    else:
        ir = analyze_text(source, execution_mode=mode)
    assert [event["source"]["line"] for event in ir["events"] if event["kind"] == "wait"] == [6]


def test_excluded_line_does_not_emit_a_register_read_hazard():
    ir = analyze_text("move 10,R0\nplay R0,1,4\nwait 1000\nstop")

    assert any(item["category"] == "invalid_argument_type" and item["details"].get("source_line_skipped")
               for item in ir["diagnostics"])
    assert not any(item["category"] == "register_not_ready" for item in ir["diagnostics"])
