from __future__ import annotations

from typing import Any

from q1timeline.analysis.interpreter import AnalysisState
from q1timeline.q1asm.ast import Arg, Instr, SourceLocation
from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS


TERMINAL_OPS = {"stop", "illegal"}


def control_flow_graph_from_states(states: list[AnalysisState]) -> dict[str, Any]:
    return {"sequencers": [_sequencer_graph(state) for state in states]}


def _sequencer_graph(state: AnalysisState) -> dict[str, Any]:
    instructions = [state.instructions_by_pc[pc] for pc in sorted(state.instructions_by_pc)]
    if not instructions:
        return {"sequencer_id": state.sequencer_id, "nodes": [], "edges": []}

    labels_by_pc = _labels_by_pc(state.labels)
    block_starts = _block_starts(state, instructions)
    nodes = [_node_for_block(state.sequencer_id, instructions, labels_by_pc, start_pc, block_starts) for start_pc in block_starts]
    node_by_start = {node["start_pc"]: node for node in nodes}
    edges = _edges_for_blocks(state, nodes, node_by_start)
    _attach_event_ids(state, nodes, edges)
    return {"sequencer_id": state.sequencer_id, "nodes": nodes, "edges": edges}


def _labels_by_pc(labels: dict[str, int]) -> dict[int, list[str]]:
    by_pc: dict[int, list[str]] = {}
    for label, pc in labels.items():
        by_pc.setdefault(pc, []).append(label)
    for names in by_pc.values():
        names.sort()
    return by_pc


def _block_starts(state: AnalysisState, instructions: list[Instr]) -> list[int]:
    pcs = [instruction.pc for instruction in instructions]
    valid_pcs = set(pcs)
    starts = {pcs[0], *state.labels.values()}
    for instruction in instructions:
        target = _branch_target(instruction, state)
        if target["pc"] in valid_pcs:
            starts.add(target["pc"])
        if _terminates_block(instruction) and instruction.pc + 1 in valid_pcs:
            starts.add(instruction.pc + 1)
    return sorted(pc for pc in starts if pc in valid_pcs)


def _node_for_block(
    sequencer_id: str,
    instructions: list[Instr],
    labels_by_pc: dict[int, list[str]],
    start_pc: int,
    block_starts: list[int],
) -> dict[str, Any]:
    pc_to_instruction = {instruction.pc: instruction for instruction in instructions}
    start_index = block_starts.index(start_pc)
    next_start = block_starts[start_index + 1] if start_index + 1 < len(block_starts) else None
    end_pc = (next_start - 1) if next_start is not None else instructions[-1].pc
    start_instruction = pc_to_instruction[start_pc]
    end_instruction = pc_to_instruction[end_pc]
    labels = labels_by_pc.get(start_pc, [])
    label = "/".join(labels) if labels else ("entry" if start_pc == instructions[0].pc else f"pc {start_pc}")
    return {
        "id": f"{sequencer_id}:cfg:n{start_pc}",
        "label": label,
        "labels": labels,
        "start_pc": start_pc,
        "end_pc": end_pc,
        "instruction_count": end_pc - start_pc + 1,
        "source": _source_to_dict(start_instruction.source),
        "source_end": _source_to_dict(end_instruction.source),
    }


def _edges_for_blocks(
    state: AnalysisState,
    nodes: list[dict[str, Any]],
    node_by_start: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for node in nodes:
        instruction = state.instructions_by_pc.get(int(node["end_pc"]))
        if instruction is None or instruction.op in TERMINAL_OPS:
            continue
        if instruction.op == "jmp":
            _append_target_edge(edges, state, node, instruction, node_by_start, kind="jump", label_prefix="jmp")
            continue
        if instruction.op == "loop":
            _append_target_edge(edges, state, node, instruction, node_by_start, kind="loop_taken", label_prefix="loop")
            _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="loop_exit", label="loop exit")
            continue
        if _is_conditional_branch(instruction):
            _append_target_edge(edges, state, node, instruction, node_by_start, kind="branch_taken", label_prefix=instruction.op)
            _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="branch_fallthrough", label="else")
            continue
        _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="fallthrough", label="next")
    return edges


def _append_target_edge(
    edges: list[dict[str, Any]],
    state: AnalysisState,
    node: dict[str, Any],
    instruction: Instr,
    node_by_start: dict[int, dict[str, Any]],
    *,
    kind: str,
    label_prefix: str,
) -> None:
    target = _branch_target(instruction, state)
    edge = _base_edge(edges, node, instruction, kind=kind, label=f"{label_prefix} {target['display']}".strip())
    edge["target"] = target["display"]
    if target["label"] is not None:
        edge["target_label"] = target["label"]
    if target["pc"] is not None:
        edge["target_pc"] = target["pc"]
        target_node = node_by_start.get(target["pc"])
        if target_node is not None:
            edge["to_node_id"] = target_node["id"]
    edges.append(edge)


def _append_fallthrough_edge(
    edges: list[dict[str, Any]],
    node: dict[str, Any],
    instruction: Instr,
    node_by_start: dict[int, dict[str, Any]],
    *,
    kind: str,
    label: str,
) -> None:
    target_pc = instruction.pc + 1
    target_node = node_by_start.get(target_pc)
    if target_node is None:
        return
    edge = _base_edge(edges, node, instruction, kind=kind, label=label)
    edge["target_pc"] = target_pc
    edge["to_node_id"] = target_node["id"]
    edges.append(edge)


def _base_edge(
    edges: list[dict[str, Any]],
    node: dict[str, Any],
    instruction: Instr,
    *,
    kind: str,
    label: str,
) -> dict[str, Any]:
    sequencer_id = str(node["id"]).split(":cfg:n", 1)[0]
    return {
        "id": f"{sequencer_id}:cfg:e{len(edges)}",
        "from_node_id": node["id"],
        "kind": kind,
        "op": instruction.op,
        "label": label,
        "source": _source_to_dict(instruction.source),
    }


def _branch_target(instruction: Instr, state: AnalysisState) -> dict[str, Any]:
    arg = _branch_target_arg(instruction)
    if arg is None:
        return {"display": "", "pc": None, "label": None}
    if arg.kind == "label":
        label = str(arg.value)
        return {"display": arg.raw, "pc": state.labels.get(label), "label": label}
    if arg.kind == "imm":
        return {"display": arg.raw, "pc": int(arg.value), "label": None}
    if arg.kind == "reg":
        return {"display": arg.raw, "pc": _event_target_pc(state, instruction), "label": None}
    return {"display": arg.raw, "pc": None, "label": None}


def _branch_target_arg(instruction: Instr) -> Arg | None:
    if not instruction.args:
        return None
    if instruction.op == "jmp":
        return instruction.args[0]
    if instruction.op == "jge":
        return instruction.args[0] if len(instruction.args) == 1 else _arg_at(instruction, 2)
    if instruction.op == "jlt":
        return _arg_at(instruction, 2)
    if instruction.op == "loop":
        return _arg_at(instruction, 1)
    if instruction.op in STATUS_BRANCH_OPS:
        return instruction.args[0]
    return None


def _arg_at(instruction: Instr, index: int) -> Arg | None:
    return instruction.args[index] if len(instruction.args) > index else None


def _event_target_pc(state: AnalysisState, instruction: Instr) -> int | None:
    for event in state.events:
        target_pc = event.meta.get("target_pc")
        if (
            event.kind == "q1_issue"
            and event.meta.get("op") == instruction.op
            and event.source.file == instruction.source.file
            and event.source.line == instruction.source.line
            and event.source.column == instruction.source.column
            and type(target_pc) is int
        ):
            return target_pc
    return None


def _terminates_block(instruction: Instr) -> bool:
    return instruction.op in TERMINAL_OPS or instruction.op == "jmp" or instruction.op == "loop" or _is_conditional_branch(instruction)


def _is_conditional_branch(instruction: Instr) -> bool:
    return instruction.op in {"jge", "jlt", *STATUS_BRANCH_OPS}


def _source_to_dict(source: SourceLocation) -> dict[str, Any]:
    return {
        "file": source.file,
        "line": source.line,
        "column": source.column,
        "raw": source.raw,
    }


def _attach_event_ids(
    state: AnalysisState,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    for node in nodes:
        node["event_ids"] = _event_ids_in_source_range(state, node.get("source"), node.get("source_end"))
    for edge in edges:
        edge["event_ids"] = _event_ids_on_source_line(state, edge.get("source"))


def _event_ids_in_source_range(
    state: AnalysisState,
    source_start: Any,
    source_end: Any,
) -> list[str]:
    if not isinstance(source_start, dict) or not isinstance(source_end, dict):
        return []
    file = str(source_start.get("file", ""))
    start_line = source_start.get("line")
    end_line = source_end.get("line")
    if type(start_line) is not int or type(end_line) is not int:
        return []
    lower, upper = sorted((start_line, end_line))
    return [
        event.id
        for event in state.events
        if event.source.file == file and lower <= event.source.line <= upper
    ]


def _event_ids_on_source_line(state: AnalysisState, source: Any) -> list[str]:
    if not isinstance(source, dict):
        return []
    file = str(source.get("file", ""))
    line = source.get("line")
    if type(line) is not int:
        return []
    return [
        event.id
        for event in state.events
        if event.source.file == file and event.source.line == line
    ]
