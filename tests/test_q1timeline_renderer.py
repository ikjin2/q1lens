from __future__ import annotations

import re

from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE
from q1timeline.render.html import render_html


def _ns(value: int) -> dict:
    return {"kind": "concrete", "value": value}


def test_svg_command_families_cover_all_supported_instructions() -> None:
    events = [
        {
            "id": op,
            "sequencer_id": "seq0",
            "lane": "debug.q1_issue",
            "kind": "q1_issue",
            "t0": _ns(index * 4),
            "t1": _ns(index * 4 + 4),
            "duration": _ns(4),
            "label": op,
            "confidence": "exact",
            "source": {},
            "meta": {"op": op.upper()},
        }
        for index, op in enumerate(INSTRUCTION_TABLE)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for op, spec in INSTRUCTION_TABLE.items():
        if op.startswith("fb_"):
            family = "feedback"
        elif spec.category == "branch" or op == "stop":
            family = "flow"
        elif spec.category in {"classical", "control"}:
            family = "q1"
        else:
            family = spec.category
        match = re.search(rf'class="([^"]+)" data-event-id="{op}"', html)
        assert match is not None, op
        assert f"command-family-{family}" in match[1].split(), op


def test_svg_families_follow_source_commands_and_preserve_synthetic_overlays() -> None:
    cases = [
        ("set", "q1_issue", "label: SET_SCOPE_EN 1", {}, "latched"),
        ("feedback", "feedback_com", "FB_COM_DATA 0,1,4", {}, "feedback"),
        ("acquire", "acquire", "acquire_weighed 0,0,0,0,4", {}, "rt"),
        ("marker", "marker_state", "upd_param 4", {}, "rt"),
        ("meta", "q1_issue", "wait 4", {"op": "wait_sync"}, "sync"),
        ("loop", "loop_block", "body: play 0,0,4", {}, "flow"),
        ("control-loop", "control_flow_loop", "play 0,0,4", {"op": "play"}, "flow"),
        ("generated-feedback", "feedback_pop", "", {}, "feedback"),
        ("unknown", "unknown_region", "play 0,0,4", {}, None),
        ("underflow", "underflow_warning", "wait 4", {}, None),
        ("incomplete", "analysis_incomplete", "fb_com_cfg 0,4", {}, None),
        ("unrecognized", "q1_issue", "future_op 4", {}, None),
    ]
    events = [
        {
            "id": event_id,
            "sequencer_id": "seq0",
            "lane": "debug.q1_issue",
            "kind": kind,
            "t0": _ns(index * 4),
            "t1": _ns(index * 4 + 4),
            "duration": _ns(4),
            "label": event_id,
            "confidence": "unknown",
            "source": {"raw": raw},
            "meta": meta,
        }
        for index, (event_id, kind, raw, meta, _family) in enumerate(cases)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for event_id, _kind, _raw, _meta, family in cases:
        match = re.search(rf'class="([^"]+)" data-event-id="{event_id}"', html)
        assert match is not None, event_id
        family_classes = [name for name in match[1].split() if name.startswith("command-family-")]
        assert family_classes == ([f"command-family-{family}"] if family else []), event_id


def test_svg_command_palette_uses_family_hues_without_kind_fill_overrides() -> None:
    html = render_html({"version": "0.1.0", "events": []})

    for family, light, dark in [
        ("q1", "#245fa8", "#80b7ff"),
        ("flow", "#9a4c18", "#f4a261"),
        ("latched", "#176b7e", "#6ec6d9"),
        ("rt", "#27763c", "#8bcf9a"),
        ("sync", "#7251a3", "#b9a7dc"),
        ("feedback", "#ad316c", "#ff6fb3"),
    ]:
        assert f"--q1asm-{family}-color: {light};" in html
        assert f"--q1asm-{family}-color: {dark};" in html
        assert f".command-family-{family} {{ --q1asm-command-color: var(--q1asm-{family}-color); }}" in html
    assert "fill: var(--q1asm-event-fill" in html
    assert "stroke: var(--q1asm-event-stroke" in html
    assert re.search(r"\.feedback-flow\s*\{[^}]*stroke: var\(--q1asm-feedback-color\)", html)
    for kind in ["play", "wait", "acquire", "upd_param", "feedback_pop", "control_flow_loop"]:
        assert not re.search(rf"\.kind-{kind} rect\s*\{{[^}}]*fill:", html)
    assert not re.search(r"\.branch-path-(?:taken|fallthrough) rect\s*\{[^}]*fill:", html)
    assert ".kind-underflow_warning rect { fill: #e26d6d; }" in html
    assert ".event.diagnostic-error rect { stroke: #dc2626;" in html


def test_svg_labels_match_command_accents_without_recoloring_diagnostics() -> None:
    rows = [
        ("play", "play", {}),
        ("underflow", "underflow_warning", {"status": "definite_underflow"}),
        ("incomplete", "analysis_incomplete", {"status": "analysis_incomplete"}),
    ]
    events = [
        {
            "id": event_id, "sequencer_id": "seq0", "lane": "rt.play", "kind": kind,
            "t0": _ns(index * 100), "t1": _ns(index * 100 + 100), "duration": _ns(100),
            "label": kind, "confidence": "unknown", "source": {"raw": "play 0,0,100"}, "meta": meta,
        }
        for index, (event_id, kind, meta) in enumerate(rows)
    ]
    html = render_html({"version": "0.1.0", "events": events, "diagnostics": [
        {"severity": "error", "category": "definite_underflow", "message": "Underflow", "related_events": ["play"]},
    ]}, highlight_event_id="play")

    play = re.search(r'class="([^"]+)" data-event-id="play"[^>]*>([\s\S]*?)</g>', html)
    assert play is not None
    assert {"command-family-rt", "is-selected", "has-diagnostic", "diagnostic-error"} <= set(play[1].split())
    assert '<text class="event-label"' in play[2]
    assert 'class="diagnostic-badge severity-error"' in play[2]
    assert "--q1asm-event-label-color: var(--q1asm-command-color);" in html
    assert re.search(r"\.event text\s*\{[^}]*fill: var\(--q1asm-event-label-color, #0f1720\)", html)
    assert "var(--q1asm-command-color) 15%" in html
    assert ".event.is-selected.has-diagnostic rect { stroke: #111827;" in html
    assert re.search(r"\.event \.diagnostic-badge text,[\s\S]*?\{ fill: #ffffff;", html)
    for event_id in ["underflow", "incomplete"]:
        overlay = re.search(rf'class="([^"]+)" data-event-id="{event_id}"', html)
        assert overlay is not None and "command-family-" not in overlay[1]
    assert ".status-definite_underflow rect { fill: #c72c2c; }" in html
    assert ".status-analysis_incomplete rect { fill: #8a8f98; stroke-dasharray: 5 3; }" in html


def test_parameters_panel_is_collapsed_between_timeline_and_analysis_details() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "DRIVE_DURATION", "value_json": "128", "overridden": True, "base_value_json": "64"},
        {"name": "GAIN_I", "value_json": "10000", "overridden": False},
    ]}})

    assert '<details id="q1timeline-parameters" class="analysis-parameters">' in html
    assert '<summary>Parameters · 2 · 1 overridden</summary>' in html
    assert html.index('id="timeline-root"') < html.index('id="q1timeline-parameters"')
    assert html.index('id="q1timeline-parameters"') < html.index('id="q1timeline-analysis-details"')
    assert '<code>DRIVE_DURATION</code>' in html
    assert '<tr data-parameter-index="0"><th scope="row"><code>DRIVE_DURATION</code>' in html
    assert '<tr data-parameter-index="1"><th scope="row"><code>GAIN_I</code>' in html
    assert '<code>128</code>' in html
    assert '← base 64' in html
    assert '<code>GAIN_I</code>' in html
    assert '<code>10000</code>' in html
    assert html.count('<span class="parameter-override">') == 1
    assert 'not live hardware or register values' in html
    assert '.parameters-table-scroll { max-height: 240px; overflow: auto; }' in html


def test_parameters_panel_preserves_exact_values_and_escapes_html() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "<script>name</script>", "value_json": '"</code><script>alert(1)</script>"',
         "overridden": True, "base_value_json": '"<base>"'},
        {"name": "LARGE", "value_json": "9007199254740993", "overridden": False},
        {"name": "NESTED", "value_json": '{"values": [1, null]}', "overridden": False},
    ]}})

    assert '&lt;script&gt;name&lt;/script&gt;' in html
    assert '&quot;&lt;/code&gt;&lt;script&gt;alert(1)&lt;/script&gt;&quot;' in html
    assert '← base &quot;&lt;base&gt;&quot;' in html
    assert '<script>alert(1)</script>' not in html
    assert '<tr data-parameter-index="0">' in html
    assert '<code>9007199254740993</code>' in html
    assert '<code>{&quot;values&quot;: [1, null]}</code>' in html


def test_parameters_panel_marks_added_and_equal_value_overrides() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "NEW", "value_json": "null", "overridden": True},
        {"name": "SAME", "value_json": "64", "overridden": True, "base_value_json": "64"},
    ]}})

    assert '<summary>Parameters · 2 · 2 overridden</summary>' in html
    assert 'added for this analysis' in html
    assert '<code>null</code>' in html
    assert '← base 64' in html


def test_parameters_panel_distinguishes_empty_snapshot_from_older_ir() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": []}})
    assert '<summary>Parameters · 0</summary>' in html
    assert 'No parameters configured for this analysis.' in html
    assert 'id="q1timeline-parameters"' not in render_html({"version": "0.1.0", "events": []})


def test_branch_region_renders_marker_without_block_in_debug_mode() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:e1",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(40),
                    "t1": _ns(64),
                    "duration": _ns(24),
                    "label": "jge",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {
                        "op": "jge",
                        "branch_id": "seq0:branch:1",
                        "condition": "R0 >= 1",
                        "assumed_branch_path": "taken",
                    },
                },
                {
                    "id": "seq0:branch:1",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch taken: R0 >= 1",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1, "raw": "jge @done # branch"},
                    "meta": {
                        "branch_id": "seq0:branch:1",
                        "condition": "R0 >= 1",
                        "assumed_branch_path": "taken",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="debug",
    )

    assert 'class="branch-marker' in html
    assert "q1-branch-collapsed" in html
    assert ".event.q1-branch-collapsed { display: none; }" in html
    assert ".event.branch-collapsed { display: none; }" in html
    assert "body.mode-normal .event.branch-collapsed" not in html
    assert "eventNode.classList.contains('branch-collapsed'))" in html
    assert "eventNode.classList.contains('q1-branch-collapsed'))" in html
    assert "Condition true: jump target" in html
    assert "Condition false: continue" in html
    assert "Compare true and false" not in html
    assert re.search(
        r'<g class="branch-marker-path-icon branch-marker-path-icon-taken"[^>]*aria-label="shown path: condition true"',
        html,
    )
    assert "Line 2: jge @done" in html
    assert "branch-marker-status" not in html
    assert "Show taken path" not in html
    assert "Show fallthrough path" not in html


def test_control_flow_graph_panel_renders_nodes_and_edges() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:e0",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(24),
                    "duration": _ns(24),
                    "label": "jl",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {
                        "op": "jl",
                        "branch_id": "seq0:branch:0",
                        "condition": "jl status flags",
                        "assumed_branch_path": "taken",
                    },
                },
            ],
            "diagnostics": [],
            "control_flow_graph": {
                "sequencers": [
                    {
                        "sequencer_id": "seq0",
                        "nodes": [
                            {
                                "id": "seq0:cfg:n0",
                                "label": "start",
                                "start_pc": 0,
                                "end_pc": 1,
                                "event_ids": ["seq0:e0"],
                                "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                            },
                            {
                                "id": "seq0:cfg:n1",
                                "label": "done",
                                "start_pc": 2,
                                "end_pc": 2,
                                "event_ids": [],
                                "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                            },
                            {
                                "id": "seq0:cfg:n2",
                                "label": "done",
                                "start_pc": 3,
                                "end_pc": 3,
                                "event_ids": [],
                                "source": {"file": "seq0.q1asm", "line": 4, "column": 1},
                            },
                        ],
                        "edges": [
                            {
                                "id": "seq0:cfg:e0",
                                "from_node_id": "seq0:cfg:n0",
                                "to_node_id": "seq0:cfg:n2",
                                "kind": "branch_taken",
                                "op": "jl",
                                "label": "jl @done",
                                "event_ids": ["seq0:e0"],
                                "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                            },
                            {
                                "id": "seq0:cfg:e1",
                                "from_node_id": "seq0:cfg:n0",
                                "to_node_id": "seq0:cfg:n1",
                                "kind": "branch_fallthrough",
                                "op": "jl",
                                "label": "else",
                                "event_ids": ["seq0:e0"],
                                "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                            },
                        ],
                    }
                ]
            },
        },
        default_mode="debug",
    )

    assert 'class="control-flow-graph"' in html
    assert "Control-flow graph" in html
    assert '<svg class="cfg-graph"' in html
    assert '<g class="cfg-graph-node" data-cfg-node-id="seq0:cfg:n0"' in html
    assert '<path class="cfg-graph-edge cfg-edge-branch_taken" data-cfg-edge-id="seq0:cfg:e0"' in html
    assert 'marker-end="url(#cfg-arrow)"' in html
    assert 'data-cfg-event-ids="seq0:e0"' in html
    assert 'data-cfg-node-id="seq0:cfg:n0"' in html
    assert 'data-cfg-edge-id="seq0:cfg:e0"' in html
    assert 'class="cfg-branch-map"' in html
    assert 'data-cfg-edge-id="seq0:cfg:e1"' in html
    assert "Branch map" in html
    assert "Condition true" in html
    assert "Condition false" in html
    assert "shown path" in html
    assert "jl status flags" in html
    assert "function selectCfgElement" in html
    assert "highlightRelatedCfgElementsForEvent" in html
    assert "start" in html
    assert "done" in html
    assert "branch taken" in html
    assert "jl @done" in html


def test_normal_mode_collapses_feedback_instruction_blocks_behind_flow_overlay() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:acquire",
                    "sequencer_id": "seq0",
                    "lane": "rt.acquire",
                    "kind": "acquire",
                    "t0": _ns(40),
                    "t1": _ns(80),
                    "duration": _ns(40),
                    "label": "acquire",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {
                        "feedback": {
                            "channel": "1",
                            "direction": "send",
                            "source": "acq#0/bin0",
                        }
                    },
                },
                {
                    "id": "seq0:pop",
                    "sequencer_id": "seq0",
                    "lane": "rt.feedback",
                    "kind": "feedback_pop",
                    "t0": _ns(120),
                    "t1": _ns(120),
                    "duration": _ns(0),
                    "label": "feedback pop",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {
                        "feedback": {
                            "channel": "1",
                            "direction": "receive",
                            "target": "$LEFT",
                        }
                    },
                },
            ],
            "feedback_flows": [
                {
                    "id": "feedback-flow-0",
                    "from_event_id": "seq0:acquire",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": "feedback ch 1: acq#0/bin0 -> $LEFT",
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert 'class="feedback-flow-group"' in html
    assert "feedback ch 1: acq#0/bin0 -&gt; $LEFT" in html
    assert "kind-acquire normal-feedback-collapsed" not in html
    assert re.search(r'class="[^"]*kind-feedback_pop[^"]*normal-feedback-collapsed', html)
    assert "body.mode-normal .event.normal-feedback-collapsed { display: none; }" in html
    assert "eventNode.classList.contains('normal-feedback-collapsed')" in html


def test_normal_mode_renders_q1_issue_as_expandable_detail_lane() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-wait",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(4),
                    "duration": _ns(4),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1, "raw": "    wait_sync 4"},
                    "meta": {"op": "wait_sync"},
                },
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(20),
                    "duration": _ns(20),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert 'data-lane="sequencer:seq0"' in html
    assert 'data-lane="sequencer:seq0 / q1_issue"' in html
    assert html.index('data-lane="sequencer:seq0"') < html.index('data-lane="sequencer:seq0 / q1_issue"')
    assert re.search(
        r'<g class="[^"]*q1-issue-lane[^"]*"[^>]*data-lane="sequencer:seq0 / q1_issue"[^>]*data-lane-role="q1-issue"[^>]*data-parent-lane="sequencer:seq0"[^>]*hidden[^>]*style="display:none"',
        html,
    )
    assert re.search(r'<text class="lane-label"[^>]*>Q1 core</text>', html)
    assert re.search(r'id="event-seq0-q1-wait"[^>]*class="[^"]*kind-q1_issue', html)
    assert re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync 4</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[^>]*q1-dense-collapsed', html)
    assert '[data-lane-role="q1-issue"] { display: none; }' in html
    assert '[data-lane-role="q1-issue"].q1-issue-expanded { display: inline; }' in html


def test_q1_issue_block_labels_use_the_command_after_source_labels() -> None:
    rows = [
        ("source", "entry : play 0,0,100 # wait", {}, "play"),
        ("metadata", "entry: play 0,0,100", {"op": "wait"}, "wait"),
        ("latched", "entry: set_mrk 1 // play", {}, "set_mrk"),
        ("fallback", "entry : # play", {}, "fallback"),
    ]
    events = [
        {
            "id": event_id, "sequencer_id": "seq0", "lane": "debug.q1_issue", "kind": "q1_issue",
            "t0": _ns(index * 100), "t1": _ns(index * 100 + 100), "duration": _ns(100),
            "label": event_id, "confidence": "exact", "source": {"raw": raw}, "meta": meta,
        }
        for index, (event_id, raw, meta, _expected) in enumerate(rows)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for event_id, _raw, _meta, expected in rows:
        event = re.search(rf'data-event-id="{event_id}"[^>]*>([\s\S]*?)</g>', html)
        assert event is not None
        assert re.search(rf'<text class="event-label"[^>]*>{expected}</text>', event[1])
        assert not re.search(r'<text class="event-label"[^>]*>entry', event[1])


def test_debug_mode_marks_q1_issue_lane_as_disclosure_lane() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-wait",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(4),
                    "duration": _ns(4),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {"op": "wait"},
                }
            ],
            "diagnostics": [],
        },
        default_mode="debug",
    )

    assert 'data-lane="sequencer:seq0 / debug.q1_issue"' in html
    assert re.search(
        r'<g class="[^"]*q1-issue-lane[^"]*"[^>]*data-lane="sequencer:seq0 / debug.q1_issue"[^>]*data-lane-role="q1-issue"[^>]*data-parent-lane="sequencer:seq0"[^>]*hidden[^>]*style="display:none"',
        html,
    )
    assert re.search(r'<text class="lane-label"[^>]*>Q1 core</text>', html)


def test_flow_and_branch_labels_do_not_render_boxy_text_halos() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r"\.branch-marker text\s*\{[^}]*stroke:\s*none;", html)
    assert re.search(r"\.feedback-flow-label\s*\{[^}]*stroke:\s*none;", html)
    assert not re.search(r"\.branch-marker text\s*\{[^}]*paint-order:\s*stroke;", html)
    assert not re.search(r"\.feedback-flow-label\s*\{[^}]*paint-order:\s*stroke;", html)


def test_confidence_fill_styles_do_not_color_branch_marker_hitboxes() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert ".confidence-assumed rect" not in html
    assert "body.vscode-dark .confidence-assumed rect" not in html
    assert ".event.confidence-assumed > rect" in html
    assert "body.vscode-dark .event.confidence-assumed > rect" in html


def test_event_inline_labels_use_short_tokens_without_losing_tooltip_detail() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:play",
                    "sequencer_id": "seq0",
                    "lane": "rt.play",
                    "kind": "play",
                    "t0": _ns(0),
                    "t1": _ns(80),
                    "duration": _ns(80),
                    "label": "play a very long waveform name that should not be inline",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:acquire",
                    "sequencer_id": "seq0",
                    "lane": "rt.acquire",
                    "kind": "acquire",
                    "t0": _ns(90),
                    "t1": _ns(170),
                    "duration": _ns(80),
                    "label": "acquire with verbose integration label",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r'<text class="event-label"[^>]*>play</text>', html)
    assert re.search(r'<text class="event-label"[^>]*>acq</text>', html)
    assert "play a very long waveform name that should not be inline" in html
    assert not re.search(r'<text class="event-label"[^>]*>play a very long waveform', html)


def test_short_rt_command_labels_fit_in_zoomed_widths() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:play",
                    "sequencer_id": "seq0",
                    "lane": "rt.play",
                    "kind": "play",
                    "t0": _ns(0),
                    "t1": _ns(20),
                    "duration": _ns(20),
                    "label": "wf#0",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:upd",
                    "sequencer_id": "seq0",
                    "lane": "rt.update",
                    "kind": "upd_param",
                    "t0": _ns(24),
                    "t1": _ns(42),
                    "duration": _ns(18),
                    "label": "upd_param",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:span",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(620),
                    "duration": _ns(620),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r'id="event-seq0-play"[\s\S]*?<text class="event-label"[^>]*>play</text>', html)
    assert re.search(r'id="event-seq0-upd"[\s\S]*?<text class="event-label"[^>]*>upd</text>', html)


def test_diagnostic_badges_are_focusable_hover_targets() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(40),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "info",
                    "category": "runtime_dependent_timing",
                    "message": "Timing depends on trigger arrival.",
                    "related_events": ["seq0:wait"],
                }
            ],
        },
        default_mode="normal",
    )

    assert 'class="diagnostic-badge severity-info"' in html
    assert 'role="button"' in html
    assert 'tabindex="0"' in html
    assert re.search(r"\.diagnostic-badge\s*\{[^}]*pointer-events:\s*all;", html)
    assert re.search(r"\.diagnostic-badge\s*\{[^}]*cursor:\s*help;", html)


def test_auxiliary_panels_collapse_details_while_errors_remain_visible() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(40),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "error",
                    "category": "invalid_instruction",
                    "message": "Invalid instruction.",
                    "related_events": ["seq0:wait"],
                },
                {
                    "severity": "info",
                    "category": "loop_truncated",
                    "message": "Loop L0 shown as compact block with iteration 0 preview only.",
                    "related_events": ["seq0:wait"],
                },
            ],
            "feedback_balance": {
                "status": "balanced",
                "channels": {
                    "1": {
                        "channel": "1",
                        "status": "balanced",
                        "matched": 1,
                        "receives": 1,
                        "sends": 1,
                        "send_payloads": 1,
                        "unmatched_receives": 0,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert '<details class="feedback-balance"' in html
    assert '<details class="feedback-balance" data-default-open="false"' in html
    assert '<details class="feedback-balance" open' not in html
    assert "Feedback FIFO" in html
    assert "balanced" in html
    assert "1 channel" in html
    assert '<details id="confidence-legend" class="confidence-legend"' in html
    assert '<details id="confidence-legend" class="confidence-legend" data-default-open="false"' in html
    assert '<details id="confidence-legend" class="confidence-legend" open' not in html
    assert '<ol class="diagnostics-critical">' in html
    assert re.search(r'<ol class="diagnostics-critical">[\s\S]*severity-error[\s\S]*Invalid instruction', html)
    assert '<details class="diagnostics-secondary" data-default-open="false">' in html
    assert re.search(
        r'<details class="diagnostics-secondary" data-default-open="false">[\s\S]*severity-info[\s\S]*Loop preview was truncated',
        html,
    )
    assert "details[data-default-open=\"false\"]" in html


def test_feedback_balance_details_open_for_unbalanced_channels() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [],
            "diagnostics": [],
            "feedback_balance": {
                "status": "unbalanced",
                "channels": {
                    "1": {
                        "channel": "1",
                        "status": "under_produced",
                        "matched": 0,
                        "receives": 1,
                        "sends": 0,
                        "send_payloads": 0,
                        "unmatched_receives": 1,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert '<details class="feedback-balance" open' in html
    assert "under_produced" in html
    assert "unmatched receives=1" in html


def test_feedback_balance_item_shows_discarded_payloads() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [],
            "diagnostics": [],
            "feedback_balance": {
                "status": "balanced",
                "channels": {
                    "16": {
                        "channel": "16",
                        "status": "balanced",
                        "matched": 0,
                        "receives": 0,
                        "sends": 1,
                        "send_payloads": 1,
                        "discarded_payloads": 1,
                        "unmatched_receives": 0,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert "discarded payloads=1" in html


def test_loop_truncated_info_badges_are_represented_once_per_diagnostic() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-issue",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(8),
                    "duration": _ns(8),
                    "label": "move",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:wait-a",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(8),
                    "t1": _ns(48),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:wait-b",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(56),
                    "t1": _ns(96),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "info",
                    "category": "loop_truncated",
                    "message": "Loop L0 shown as compact block with iteration 0 preview only.",
                    "related_events": ["seq0:q1-issue", "seq0:wait-a", "seq0:wait-b"],
                }
            ],
        },
        default_mode="normal",
    )

    assert html.count('class="diagnostic-badge severity-info"') == 1
    assert 'id="event-seq0-wait-a"' in html
    assert re.search(r'id="event-seq0-wait-a"[\s\S]*class="diagnostic-badge severity-info"', html)
    assert not re.search(r'id="event-seq0-wait-b"[\s\S]*class="diagnostic-badge severity-info"', html)


def test_flow_and_branch_labels_use_compact_visible_text_with_full_tooltips() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:acquire",
                    "sequencer_id": "seq0",
                    "lane": "rt.acquire",
                    "kind": "acquire",
                    "t0": _ns(40),
                    "t1": _ns(80),
                    "duration": _ns(40),
                    "label": "acquire",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:pop",
                    "sequencer_id": "seq0",
                    "lane": "rt.feedback",
                    "kind": "feedback_pop",
                    "t0": _ns(120),
                    "t1": _ns(120),
                    "duration": _ns(0),
                    "label": "feedback pop",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(90),
                    "t1": _ns(90),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                },
            ],
            "feedback_flows": [
                {
                    "id": "feedback-flow-0",
                    "from_event_id": "seq0:acquire",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": "feedback ch 1: acq#0/bin0 -> $LEFT",
                },
                {
                    "id": "feedback-flow-1",
                    "from_event_id": "seq0:acquire",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": "feedback ch 1: acq#0/bin0 -> $GAIN",
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert len(re.findall(r'<text class="feedback-flow-label"[^>]*>fb ch 1</text>', html)) == 1
    assert re.search(
        r'<g class="branch-marker-path-icon branch-marker-path-icon-fallthrough"[^>]*aria-label="shown path: condition false"',
        html,
    )
    assert "Line 3: jl @stop_sequencer" in html
    assert not re.search(r'<text class="branch-marker-condition"', html)
    assert "feedback ch 1: acq#0/bin0 -&gt; $LEFT" in html
    assert "unresolved branch $CURSOR_GAIN &gt;= $MAX_GAIN" in html
