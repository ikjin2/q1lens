from __future__ import annotations

from types import SimpleNamespace

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.values import Concrete


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
