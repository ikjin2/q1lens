from __future__ import annotations

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm


def _timeline_ir(source: str) -> dict:
    program = parse_q1asm(source, file="cfg.q1asm")
    state = interpret_program(program, sequencer_id="seq0")
    return timeline_ir_from_states([state])


def test_timeline_ir_includes_static_control_flow_graph() -> None:
    ir = _timeline_ir(
        "start:\n"
        "    cmp R0, 1\n"
        "    jl @done\n"
        "    jmp @start\n"
        "done:\n"
        "    stop\n"
    )

    graph = ir["control_flow_graph"]["sequencers"][0]
    nodes_by_label = {node["label"]: node for node in graph["nodes"]}
    start = nodes_by_label["start"]
    fallthrough = nodes_by_label["pc 2"]
    done = nodes_by_label["done"]

    assert graph["sequencer_id"] == "seq0"
    assert start["start_pc"] == 0
    assert start["end_pc"] == 1
    assert done["start_pc"] == 3

    edges = graph["edges"]
    assert {
        (edge["from_node_id"], edge.get("to_node_id"), edge["kind"], edge["op"], edge["label"])
        for edge in edges
    } >= {
        (start["id"], done["id"], "branch_taken", "jl", "jl @done"),
        (start["id"], fallthrough["id"], "branch_fallthrough", "jl", "else"),
        (fallthrough["id"], start["id"], "jump", "jmp", "jmp @start"),
    }

    event_ids = {event["id"] for event in ir["events"]}
    assert set(start["event_ids"]) <= event_ids
    assert len(start["event_ids"]) >= 3

    taken_edge = next(edge for edge in edges if edge["label"] == "jl @done")
    assert set(taken_edge["event_ids"]) <= event_ids
    assert taken_edge["event_ids"]
    assert any(
        event["source"]["line"] == 3
        for event in ir["events"]
        if event["id"] in taken_edge["event_ids"]
    )


def test_control_flow_graph_resolves_concrete_register_jmp_target() -> None:
    ir = _timeline_ir(
        "move @target,R27\n"
        "nop\n"
        "nop\n"
        "jmp R27\n"
        "stop\n"
        "target: wait 12\n"
        "stop\n"
    )

    graph = ir["control_flow_graph"]["sequencers"][0]
    nodes_by_label = {node["label"]: node for node in graph["nodes"]}
    target = nodes_by_label["target"]
    edge = next(edge for edge in graph["edges"] if edge["op"] == "jmp")

    assert edge["target"] == "R27"
    assert edge["target_pc"] == 5
    assert edge["to_node_id"] == target["id"]
