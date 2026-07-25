from __future__ import annotations

import re

from q1timeline.render.html import render_html


def _ns(value: int) -> dict:
    return {"kind": "concrete", "value": value}


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
                    "meta": {"op": "wait"},
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
    assert re.search(r'<text class="lane-label"[^>]*>Q1 issue</text>', html)
    assert re.search(r'id="event-seq0-q1-wait"[^>]*class="[^"]*kind-q1_issue', html)
    assert re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync 4</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[^>]*q1-dense-collapsed', html)
    assert '[data-lane-role="q1-issue"] { display: none; }' in html
    assert '[data-lane-role="q1-issue"].q1-issue-expanded { display: inline; }' in html


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
    assert re.search(r'<text class="lane-label"[^>]*>Q1 issue</text>', html)


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
