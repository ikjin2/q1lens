from __future__ import annotations

from dataclasses import replace

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.control_flow_graph import control_flow_graph_from_states
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.ast import SourceLocation
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


def test_indirect_branches_on_same_line_keep_separate_observed_targets() -> None:
    state = interpret_program(parse_q1asm("jmp R1\njmp R2\nstop\nunused: loop R7,@unused"), sequencer_id="s")
    first, second = state.instructions_by_pc[0], state.instructions_by_pc[1]
    second = replace(second, source=replace(first.source, column=7))
    state.instructions_by_pc[1] = second
    template = state.events[0]
    state.events = [replace(template, id=str(i), kind="q1_issue", source=ins.source,
                            meta={"op": "jmp", "target_pc": pc, "compiled_target_pc": pc})
                    for i, (ins, pc) in enumerate([(first, 1), (second, 2), (first, 0)])]
    graph, = control_flow_graph_from_states([state])["sequencers"]
    first_edge, second_edge = graph["edges"][:2]
    assert "target_pc" not in first_edge
    assert second_edge["target_pc"] == 2
    state.compiled_addresses = None
    graph, = control_flow_graph_from_states([state])["sequencers"]
    assert [edge["target_pc"] for edge in graph["edges"][:2]] == [1, 2]


def test_event_links_preserve_execution_order_across_overlapping_and_reversed_source_ranges() -> None:
    program = parse_q1asm("start: nop\njmp @end\nother: nop\njmp @start\nend: stop")
    state = interpret_program(program, sequencer_id="seq")
    far = 10**9
    locations = [("a", far), ("b", 2), ("a", 4), ("a", 11), ("b", 2)]
    for pc, (file, line) in enumerate(locations):
        instruction = state.instructions_by_pc[pc]
        state.instructions_by_pc[pc] = replace(instruction, source=replace(instruction.source, file=file, line=line))
    template = state.events[0]
    state.events = [
        replace(template, id=f"e{i}", source=SourceLocation(file, line, 1, ""))
        for i, (file, line) in enumerate([("a", 11), ("b", 2), ("a", far), ("a", 4), ("a", 1), ("a", 11)])
    ]
    graph, = control_flow_graph_from_states([state])["sequencers"]
    assert [node["event_ids"] for node in graph["nodes"]] == [["e0", "e2", "e3", "e5"], ["e0", "e3", "e5"], ["e1"]]
    assert [edge["event_ids"] for edge in graph["edges"]] == [["e1"], ["e0", "e5"]]
