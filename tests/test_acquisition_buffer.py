from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.hardware_readiness import analyze_hardware_readiness
from q1timeline.analysis.acquisition_buffer import _Span, _required_work
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.cli import main
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS
from q1timeline.watch.watcher import run_watch_once


def check(source: str, *, module_type: str | None = "QRM", sequencers: int | None = 1):
    state = interpret_program(parse_q1asm(source, file="readout.q1asm"), sequencer_id="readout")
    analyze_hardware_readiness(state, module_type=module_type, acquisition_sequencers=sequencers)
    findings = [item for item in state.diagnostics if item.category.startswith("acquisition_buffer_")]
    return state.metadata["hardware_readiness"]["checks"]["acquisition_buffer"], findings


@pytest.mark.parametrize("count", [2, 7])
def test_documented_short_burst_is_silent(count):
    summary, findings = check("acquire 0,0,4\n" * count + "stop\n")
    assert findings == []
    assert summary["status"] == "pass"
    assert summary["required_burst_credits"] <= 7


def test_finite_burst_beyond_documented_guarantee_warns_instead_of_claiming_overflow():
    summary, findings = check("acquire 0,0,4\n" * 9 + "stop\n")
    assert [(d.category, d.severity) for d in findings] == [("acquisition_buffer_pressure", "warning")]
    assert summary["required_burst_credits"] == 9
    assert "overflow_time_ns" not in findings[0].details


def test_idle_time_replenishes_burst_budget():
    summary, findings = check(("acquire 0,0,4\n" * 7 + "wait 3000\n") * 3 + "stop\n")
    assert findings == []
    assert summary["required_burst_credits"] == 7


def test_fast_forever_loop_is_an_error_with_known_hardware():
    summary, findings = check("wait_sync 4\nshot: acquire 0,0,40\nwait 80\njmp @shot\n")
    assert [(d.category, d.severity) for d in findings] == [("acquisition_buffer_sustained_overload", "error")]
    assert findings[0].source.line == 2
    assert findings[0].details["period_ns"] == 120
    assert findings[0].details["service_period_ns"] == 300
    assert summary["status"] == "error"


def test_unknown_hardware_downgrades_sustained_overload_to_warning():
    _, findings = check("shot: acquire 0,0,120\njmp @shot\n", module_type=None)
    assert [d.severity for d in findings] == ["warning"]
    assert findings[0].details["module_type"] is None


@pytest.mark.parametrize("period", [300, 304])
def test_sustainable_loop_at_or_above_service_period_is_silent(period):
    _, findings = check(f"shot: acquire 0,0,{period}\njmp @shot\n")
    assert findings == []


def test_short_loop_wrap_gap_is_buffered_when_average_rate_is_sustainable():
    # Starts at 100 and 900 ns, then at 1100 ns: the wrap gap is only 200 ns.
    summary, findings = check("wait 100\nshot: acquire 0,0,40\nwait 760\nacquire 0,1,40\nwait 160\njmp @shot\n")
    assert findings == []
    assert summary["required_burst_credits"] == 2


def test_finite_repetitions_preserve_buffer_debt_and_do_not_expand_large_loops():
    summary, findings = check("move 1000000000,R0\nnop\nshot: acquire 0,0,120\nloop R0,@shot\nstop\n")
    assert [d.severity for d in findings] == ["warning"]
    assert summary["required_burst_credits"] > 7


def test_loop_debt_carries_into_the_following_burst():
    _, findings = check("move 4,R0\nnop\nshot: acquire 0,0,4\nwait 36\nloop R0,@shot\n" + "acquire 0,1,4\n" * 6 + "stop\n")
    assert [d.severity for d in findings] == ["warning"]


def test_multiple_sequencers_change_the_service_period():
    source = "shot: acquire 0,0,500\njmp @shot\n"
    assert check(source, sequencers=1)[1] == []
    _, findings = check(source, sequencers=3)
    assert [d.severity for d in findings] == ["warning"]
    assert findings[0].details["conservative_service_period_ns"] == 600


def test_unknown_peer_count_warns_only_if_the_documented_buffer_cannot_cover_the_slow_case():
    source = "shot: acquire 0,0,500\njmp @shot\n"
    _, findings = check(source, sequencers=None)
    assert [d.severity for d in findings] == ["warning"]
    assert check("acquire 0,0,4\n" * 7 + "stop\n", sequencers=None)[1] == []


def test_runtime_timing_is_warning_not_a_definite_failure():
    _, findings = check("shot: acquire 0,0,40\nwait R1\njmp @shot\n")
    assert findings and all(d.severity == "warning" for d in findings)


def test_variable_duration_across_iterations_is_not_proved_as_sustained_overload():
    _, findings = check("move 40,R1\nnop\nshot: acquire 0,0,40\nwait R1\nadd R1,1000,R1\nnop\njmp @shot\n")
    assert findings and all(d.severity == "warning" for d in findings)


def test_unknown_timing_stays_silent_when_a_finite_loop_fits_in_the_burst_allowance():
    summary, findings = check("move 7,R0\nnop\nshot: acquire 0,0,4\nwait R1\nloop R0,@shot\nstop\n")
    assert findings == []
    assert summary["required_burst_credits"] == 7


@pytest.mark.parametrize("op", sorted(STATUS_BRANCH_OPS))
def test_unresolved_status_branch_does_not_claim_a_finite_burst_bound(op):
    summary, findings = check(f"shot: acquire 0,0,4\nwait R1\n{op} @shot\nstop\n")
    assert summary["status"] == "warning"
    assert [d.severity for d in findings] == ["warning"]


def test_compact_repeat_math_matches_an_independent_explicit_queue():
    rng = random.Random(7)
    for _ in range(150):
        service = rng.choice([300, 600, 900])
        period, count = rng.randint(20, 5000), rng.randint(1, 40)
        offsets = sorted(rng.sample(range(period), rng.randint(1, min(period, 7))))
        spans = [_Span(0, [(0, None)]), _Span(12, [(offset, None) for offset in offsets], period, count),
                 _Span(12 + period*count + 4, [(0, None)])]
        arrivals = [0] + [12 + iteration*period + offset for iteration in range(count) for offset in offsets] + [12 + period*count + 4]
        work = peak = previous = 0
        for arrival in arrivals:
            work = max(0, work-(arrival-previous)) + service
            peak = max(peak, work)
            previous = arrival
        assert _required_work(spans, service) == (peak, None, None)


def test_finite_warning_points_to_peak_pressure_instead_of_a_later_recovered_acquisition():
    _, findings = check("acquire 0,0,4\n" * 9 + "wait 5000\nacquire 0,1,4\nstop\n")
    assert findings[0].source.line == 9


def test_showing_more_loop_preview_copies_does_not_add_acquisitions():
    program = parse_q1asm("move 2,R0\nnop\nshot: acquire 0,0,40\nwait 960\nloop R0,@shot\nstop\n", file="preview.q1asm")
    state = interpret_program(program, sequencer_id="preview")
    key = next(e.meta["loop_preview_key"] for e in state.events if e.kind == "loop_block")
    expanded = interpret_program(program, sequencer_id="preview", loop_preview_counts={key: 2})
    analyze_hardware_readiness(expanded, module_type="QRM", acquisition_sequencers=1)
    summary = expanded.metadata["hardware_readiness"]["checks"]["acquisition_buffer"]
    assert summary["required_burst_credits"] == 1
    assert summary["status"] == "pass"


def test_qtm_is_not_given_the_qrm_buffer_profile():
    summary, findings = check("acquire 0,0,4\n" * 9 + "stop\n", module_type="QTM")
    assert [d.severity for d in findings] == ["warning"]
    assert summary["status"] == "warning"
    assert findings[0].details["profile_confirmed"] is False


@pytest.mark.parametrize("extra_args", [[], ["--hardware-readiness"]])
def test_cli_reports_model_errors_and_preserves_diagnostics_artifacts(tmp_path: Path, extra_args):
    (tmp_path / "readout.q1asm").write_text("shot: acquire 0,0,120\njmp @shot\n")
    (tmp_path / "q1timeline.yml").write_text(
        "sequencers:\n  - id: readout\n    file: readout.q1asm\n    module: readout_module\n    module_type: QRM\n"
    )
    out = tmp_path / "timeline.json"
    result = main(["analyze", "--project", str(tmp_path / "q1timeline.yml"), "--out", str(out), "--no-render", *extra_args])
    assert result == 2
    ir = json.loads(out.read_text())
    assert ir["hardware_readiness"]["status"] == "error"
    assert any(d["category"] == "acquisition_buffer_sustained_overload" and d["severity"] == "error" for d in ir["diagnostics"])


@pytest.mark.parametrize("options", [{}, {"hardware_readiness": False}, {"hardware_readiness": True}])
@pytest.mark.parametrize("multiple", [False, True])
def test_api_always_checks_buffers_even_with_optional_hardware_hints_disabled(options, multiple):
    source = "acquire 0,0,4\n" * 9 + "stop\n"
    ir = analyze_documents({"readout.q1asm": source}, **options) if multiple else analyze_text(source, **options)
    findings = [d for d in ir["diagnostics"] if d["category"] == "acquisition_buffer_pressure"]
    assert [d["severity"] for d in findings] == ["warning"]
    if not options.get("hardware_readiness"):
        assert set(ir["hardware_readiness"]["sequencers"][0]["checks"]) == {"acquisition_buffer", "terminal_latched_state"}


def test_default_api_stays_silent_for_a_documented_buffered_burst():
    ir = analyze_text("acquire 0,0,4\n" * 7 + "stop\n")
    assert not any(d["category"].startswith("acquisition_buffer_") for d in ir["diagnostics"])
    assert ir["hardware_readiness"]["sequencers"][0]["checks"]["acquisition_buffer"]["status"] == "pass"


@pytest.mark.parametrize("source, expected_error", [
    ("shot: acquire 0,0,120\njmp @shot\n", True),
    ("acquire 0,0,4\n" * 7 + "stop\n", False),
])
def test_watch_checks_acquisition_buffers_without_an_opt_in(tmp_path, source, expected_error):
    (tmp_path / "readout.q1asm").write_text(source)
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n  - id: readout\n    file: readout.q1asm\n    module: m0\n    module_type: QRM\n")
    result = run_watch_once(project, tmp_path / "watch")
    assert result.ok is not expected_error
    diagnostics = json.loads(result.diagnostics.read_text())
    assert any(d["category"] == "acquisition_buffer_sustained_overload" for d in diagnostics) is expected_error
