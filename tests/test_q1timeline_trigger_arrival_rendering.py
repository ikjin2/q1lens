import re
from copy import deepcopy

import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.render.html import render_html


def _simulate(source):
    return analyze_text(source, sequencer_id="seq", execution_mode="simulation", simulation={
        "startup_delay_ns": 100,
        "inputs": {"triggers": [{"target": "seq", "time_ns": time, "address": address}
                                for time, address in [(120, 1), (120, 2), (120, 1), (140, 3)]]},
    })


def _markers(html):
    return re.findall(r'<g\b(?=[^>]*\bdata-kind="trigger_receive")[^>]*>.*?</g>', html.split("<template", 1)[0])


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_coincident_arrivals_share_one_point_and_preserve_original_event_ids(mode):
    ir = _simulate("wait 100\nstop")
    before = deepcopy(ir)
    first, second, duplicate, later = [event for event in ir["events"] if event["kind"] == "trigger_receive"]
    point, other = _markers(render_html(ir, default_mode=mode, highlight_event_id=second["id"]))
    assert f'data-event-id="{first["id"]}"' in point
    assert f'data-event-ids="{second["id"]} {duplicate["id"]}"' in point
    assert 'class="event is-selected' in point
    assert "<title>Trigger addresses 1 ×2, 2 · 20 ns</title>" in point
    assert point.count("<circle ") == 1 and 'r="3"' in point
    assert f'data-event-id="{later["id"]}"' in other
    assert "<title>Trigger address 3 · 40 ns</title>" in other
    assert ir == before


@pytest.mark.parametrize("alternative_consumer", [False, True])
def test_unique_wait_consumer_only_prefers_an_arrival_from_its_own_path(alternative_consumer):
    ir = _simulate("wait 20\nwait_trigger 2,4\nwait 80\nstop")
    first, second, duplicate, _ = [event for event in ir["events"] if event["kind"] == "trigger_receive"]
    wait, = [event for event in ir["events"] if event["kind"] == "wait_trigger"]
    assert wait["meta"]["input_event_ids"] == [second["id"]]
    if alternative_consumer:
        wait["lane"] = "branch_compare.taken.rt.wait_trigger"
        wait["meta"].update(branch_comparison_kind="rt_condition", branch_comparison_branch_id="branch",
                            branch_comparison_path="taken")
    primary, alias = (first, second) if alternative_consumer else (second, first)
    points = _markers(render_html(ir))
    assert len(points) == 2
    group, = [point for point in points if "Trigger addresses " in point]
    assert f'data-event-id="{primary["id"]}"' in group
    assert f'data-event-ids="{alias["id"]} {duplicate["id"]}"' in group
    assert "<title>Trigger addresses 1 ×2, 2 · 20 ns</title>" in group
