from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.values import Concrete
from q1timeline.cli import main


def test_after_first_wait_sync_aligns_following_events_to_sync_end() -> None:
    wait_sync = SimpleNamespace(
        id="seq0:e0",
        kind="wait_sync",
        t0=Concrete(10),
        t1=Concrete(14),
        meta={},
    )
    play = SimpleNamespace(
        id="seq0:e1",
        kind="play",
        t0=Concrete(20),
        t1=Concrete(40),
        meta={},
    )
    state = SimpleNamespace(
        sequencer_id="seq0",
        events=[wait_sync, play],
        instructions_by_pc={},
        labels={},
        metadata={},
        diagnostics=[],
    )

    result = align_timelines([state], mode="after_first_wait_sync")

    assert result.sequencer_offsets == {"seq0": Concrete(-14)}
    assert play.meta["aligned_t0"] == 6
    assert play.meta["aligned_t1"] == 26


def test_cli_accepts_after_first_wait_sync_without_changing_runtime_times(tmp_path: Path) -> None:
    source = tmp_path / "seq0.q1asm"
    source.write_text("wait_sync 4\nwait 20\nplay 0,1,20\nstop\n", encoding="utf-8")
    output = tmp_path / "timeline_ir.json"

    assert main([
        "analyze", str(source), "--align", "after_first_wait_sync",
        "--out", str(output), "--no-render",
    ]) == 0

    ir = json.loads(output.read_text(encoding="utf-8"))
    play = next(event for event in ir["events"] if event["kind"] == "play")
    assert play["t0"]["value"] == 24
    assert play["t1"]["value"] == 44
    assert play["meta"]["aligned_t0"] == 20
    assert play["meta"]["aligned_t1"] == 40
