from __future__ import annotations

from bisect import bisect_left, bisect_right
from itertools import pairwise
from typing import Any

from q1timeline.execution.state import AnalysisState
from q1timeline.q1asm.ast import Arg, Instr, SourceLocation
from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS


TERMINAL_OPS = {"stop", "illegal"}
CONDITIONAL_BRANCH_OPS = {"jge", "jlt", *STATUS_BRANCH_OPS}


def control_flow_graph_from_states(states: list[AnalysisState]) -> dict[str, Any]:
    return {"sequencers": [_sequencer_graph(state) for state in states]}


def _sequencer_graph(state: AnalysisState) -> dict[str, Any]:
    instructions = [state.instructions_by_pc[pc] for pc in sorted(state.instructions_by_pc)]
    if not instructions:
        return {"sequencer_id": state.sequencer_id, "nodes": [], "edges": []}

    labels_by_pc = _labels_by_pc(state.labels)
    targets = _branch_targets(state, instructions)
    block_starts = _block_starts(state, instructions, targets)
    nodes = [
        _node_for_block(state, labels_by_pc, start_pc, next_pc - 1, is_entry=start_pc == block_starts[0])
        for start_pc, next_pc in pairwise([*block_starts, instructions[-1].pc + 1])
    ]
    node_by_start = {node["start_pc"]: node for node in nodes}
    edges = _edges_for_blocks(state, nodes, node_by_start, targets)
    _attach_event_ids(state, nodes, edges)
    return {"sequencer_id": state.sequencer_id, "nodes": nodes, "edges": edges}


def _labels_by_pc(labels: dict[str, int]) -> dict[int, list[str]]:
    by_pc: dict[int, list[str]] = {}
    for label, pc in labels.items():
        by_pc.setdefault(pc, []).append(label)
    for names in by_pc.values():
        names.sort()
    return by_pc


def _block_starts(state: AnalysisState, instructions: list[Instr], targets: dict) -> list[int]:
    pcs = [instruction.pc for instruction in instructions]
    valid_pcs = set(pcs)
    starts = {pcs[0], *state.labels.values()}
    starts.update(target["pc"] for target in targets.values() if target["pc"] in valid_pcs)
    for instruction in instructions:
        if _terminates_block(instruction) and instruction.pc + 1 in valid_pcs:
            starts.add(instruction.pc + 1)
    return sorted(pc for pc in starts if pc in valid_pcs)


def _node_for_block(
    state: AnalysisState,
    labels_by_pc: dict[int, list[str]],
    start_pc: int,
    end_pc: int,
    *,
    is_entry: bool,
) -> dict[str, Any]:
    labels = labels_by_pc.get(start_pc, [])
    label = "/".join(labels) if labels else ("entry" if is_entry else f"pc {start_pc}")
    return {
        "id": f"{state.sequencer_id}:cfg:n{start_pc}",
        "label": label,
        "labels": labels,
        "start_pc": start_pc,
        "end_pc": end_pc,
        "instruction_count": end_pc - start_pc + 1,
        "source": _source_to_dict(state.instructions_by_pc[start_pc].source),
        "source_end": _source_to_dict(state.instructions_by_pc[end_pc].source),
    }


def _edges_for_blocks(
    state: AnalysisState,
    nodes: list[dict[str, Any]],
    node_by_start: dict[int, dict[str, Any]],
    targets: dict,
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for node in nodes:
        instruction = state.instructions_by_pc.get(int(node["end_pc"]))
        if instruction is None or instruction.op in TERMINAL_OPS:
            continue
        if instruction.op == "jmp":
            _append_target_edge(edges, targets[instruction.pc], node, instruction, node_by_start, kind="jump", label_prefix="jmp")
            continue
        if instruction.op == "loop":
            _append_target_edge(edges, targets[instruction.pc], node, instruction, node_by_start, kind="loop_taken", label_prefix="loop")
            _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="loop_exit", label="loop exit")
            continue
        if _is_conditional_branch(instruction):
            _append_target_edge(edges, targets[instruction.pc], node, instruction, node_by_start, kind="branch_taken", label_prefix=instruction.op)
            _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="branch_fallthrough", label="else")
            continue
        _append_fallthrough_edge(edges, node, instruction, node_by_start, kind="fallthrough", label="next")
    return edges


def _append_target_edge(
    edges: list[dict[str, Any]],
    target: dict[str, Any],
    node: dict[str, Any],
    instruction: Instr,
    node_by_start: dict[int, dict[str, Any]],
    *,
    kind: str,
    label_prefix: str,
) -> None:
    edge = _base_edge(edges, node, instruction, kind=kind, label=f"{label_prefix} {target['display']}".strip())
    edge["target"] = target["display"]
    if target["label"] is not None:
        edge["target_label"] = target["label"]
    for field in ("compiled_target_pc", "target_interior_op"):
        if field in target:
            edge[field] = target[field]
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


def _branch_targets(state: AnalysisState, instructions: list[Instr]) -> dict[int, dict[str, Any]]:
    branches = [(item, _branch_target_arg(item)) for item in instructions
                if item.op in CONDITIONAL_BRANCH_OPS or item.op in {"jmp", "loop"}]
    # Indirect jumps use the observed destinations from that exact source location.
    observations = {_branch_site(item.op, item.source): [] for item, arg in branches
                    if arg is not None and arg.kind not in {"label", "imm"}}
    if observations:
        for event in state.events:
            if event.kind == "q1_issue" and isinstance(op := event.meta.get("op"), str):
                matches = observations.get(_branch_site(op, event.source))
                if matches is not None:
                    matches.append(event.meta)
    return {item.pc: _branch_target(arg, state, observations.get(_branch_site(item.op, item.source), ()))
            for item, arg in branches}


def _branch_site(op: str, source: SourceLocation) -> tuple[str, str, int, int]:
    return op, source.file, source.line, source.column


def _branch_target(arg: Arg | None, state: AnalysisState, observations) -> dict[str, Any]:
    if arg is None:
        return {"display": "", "pc": None, "label": None}
    target = {"display": arg.raw, "pc": None, "label": None}
    if arg.kind == "label":
        label = str(arg.value)
        target.update(pc=state.labels.get(label), label=label)
        if state.compiled_addresses is not None and label in state.compiled_addresses.labels:
            target["compiled_target_pc"] = state.compiled_addresses.labels[label]
        return target
    if arg.kind == "imm":
        address = int(arg.value)
        if state.compiled_addresses is None:
            target["pc"] = address
        else:
            target["compiled_target_pc"] = address
            compiled = state.compiled_addresses.targets.get(address)
            if compiled is not None:
                if compiled.interior is None:
                    target["pc"] = compiled.source_pc
                else:
                    target["target_interior_op"] = compiled.interior.op
        return target
    return {**target, **_observed_branch_target(state, observations)}


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


def _observed_branch_target(state: AnalysisState, observations) -> dict[str, Any]:
    targets = set()
    for meta in observations:
        pc = meta.get("target_pc")
        address = meta.get("compiled_target_pc")
        interior = meta.get("target_interior_op")
        if state.compiled_addresses is None and type(pc) is int:
            return {"pc": pc}
        targets.add((pc, address, interior))
    if len(targets) != 1:
        return {}
    pc, address, interior = targets.pop()
    target = {"pc": pc if type(pc) is int and interior is None else None}
    if type(address) is int:
        target["compiled_target_pc"] = address
    if isinstance(interior, str):
        target["target_interior_op"] = interior
    return target


def _terminates_block(instruction: Instr) -> bool:
    return instruction.op in TERMINAL_OPS or instruction.op == "jmp" or instruction.op == "loop" or _is_conditional_branch(instruction)


def _is_conditional_branch(instruction: Instr) -> bool:
    return instruction.op in CONDITIONAL_BRANCH_OPS


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
    # Match ranges on occupied source lines, then append IDs in execution order.
    targets_by_file: dict[str, dict[int, list[list[str]]]] = {}
    for event in state.events:
        targets_by_file.setdefault(event.source.file, {}).setdefault(event.source.line, [])
    lines_by_file = {file: sorted(targets) for file, targets in targets_by_file.items()}
    for node in nodes:
        source = node["source"]
        file, start, end = source["file"], source["line"], node["source_end"]["line"]
        ids = node["event_ids"] = []
        lines = lines_by_file.get(file)
        if lines and type(start) is int and type(end) is int:
            lower, upper = sorted((start, end))
            for line in lines[bisect_left(lines, lower):bisect_right(lines, upper)]:
                targets_by_file[file][line].append(ids)
    for edge in edges:
        source = edge["source"]
        ids = edge["event_ids"] = []
        targets = targets_by_file.get(source["file"], {}).get(source["line"])
        if targets is not None and type(source["line"]) is int:
            targets.append(ids)
    for event in state.events:
        for ids in targets_by_file[event.source.file][event.source.line]:
            ids.append(event.id)
