import pytest

from q1timeline.analysis.api import analyze_text


def test_wait_only_loop_proves_full_constant_wait_without_expanding_preview() -> None:
    ir = analyze_text("move 6,R1\nwait 4\nbody: wait 65532\nloop R1,@body\nstop\n")
    loop = next(event for event in ir["events"] if event["kind"] == "loop_block")
    stop = next(event for event in ir["events"] if event["kind"] == "stop")

    assert loop["meta"]["wait_only"] is True
    assert loop["meta"]["count"] == 6
    assert loop["meta"]["period"]["value"] == 65532
    assert loop["meta"]["visible_iteration_count"] == 1
    assert (loop["t0"]["value"], loop["t1"]["value"]) == (4, 65536)
    assert loop["t0"]["value"] + 6 * 65532 == stop["t0"]["value"] == 393196


def test_wait_only_loop_accepts_multiple_immediate_waits() -> None:
    ir = analyze_text("move 6,R1\nwait 4\nbody: wait 65532\nwait 4\nloop R1,@body\nstop\n")
    loop = next(event for event in ir["events"] if event["kind"] == "loop_block")

    assert loop["meta"]["wait_only"] is True
    assert loop["meta"]["period"]["value"] == 65536


@pytest.mark.parametrize(
    "body",
    [
        "set_awg_offs 1,0\nwait 65532",
        "upd_param 4\nwait 65532",
        "play 0,1,4\nwait 65532",
        "acquire 0,0,4\nwait 65532",
        "wait R1",
        "wait 4\njmp @skip\nset_mrk 1\nskip: wait 65532",
    ],
)
def test_state_changes_register_waits_and_branches_do_not_prove_wait_only(body: str) -> None:
    ir = analyze_text(f"move 6,R1\nwait 4\nbody: {body}\nloop R1,@body\nstop\n")
    loops = [event for event in ir["events"] if event["kind"] == "loop_block"]

    assert loops
    assert all("wait_only" not in loop["meta"] for loop in loops)


def test_unbounded_wait_loop_does_not_claim_finite_coverage() -> None:
    ir = analyze_text("body: wait 65532\njmp @body\n")
    loop = next(event for event in ir["events"] if event["kind"] == "loop_block")

    assert "wait_only" not in loop["meta"]
