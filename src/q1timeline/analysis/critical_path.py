from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any


_SAFE_OMITTED_KINDS = {
    "latched_state_pending",
    "latched_state_applied",
    "q1_issue",
    "queue_depth",
    "slack",
    "underflow_warning",
}
_CONTROL_SUMMARY_KINDS = {"branch_region", "loop_block", "loop_iteration_preview", "unknown_region"}
_ANALYSIS_ONLY_KINDS = _SAFE_OMITTED_KINDS | _CONTROL_SUMMARY_KINDS | {"analysis_incomplete"}

_REASON_MESSAGES = {
    "analysis_incomplete": "Analysis could not fully model one or more instructions or regions.",
    "backward_or_overlapping_feedback_edge": (
        "A feedback dependency was omitted because the receiver starts before the sender ends."
    ),
    "dependency_cycle": "The concrete dependency graph contains a cycle.",
    "inconsistent_rt_packet_mirrors": "Events sharing one RT packet disagree on their aligned interval.",
    "invalid_input": "Input errors prevent a complete execution model.",
    "loop_summary": "A compact loop summary or preview does not represent every runtime iteration.",
    "no_concrete_execution_events": "No concrete aligned execution event is available.",
    "non_concrete_timing": "An execution event has no complete concrete aligned interval.",
    "non_exact_confidence": "A concrete execution event depends on a non-exact timing assumption.",
    "overlapping_sequencer_events": (
        "Two distinct execution nodes overlap on one sequencer, so their order was not inferred."
    ),
    "runtime_dependent_control_flow": "A branch or unknown region depends on a runtime choice or assumption.",
    "unaligned_sequencer": "A sequencer has no exact shared-time alignment.",
    "unmatched_feedback_receive": "A feedback receive has no matched send dependency.",
    "unrepresented_rt_packet": "An emitted RT packet has no concrete canonical execution event.",
    "unresolved_feedback_endpoint": "A feedback flow endpoint has no concrete execution node.",
}


@dataclass(frozen=True)
class _Node:
    event_id: str
    sequencer_id: str
    kind: str
    label: str
    start_ns: int
    end_ns: int
    duration_ns: int
    source_index: int
    merged_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Edge:
    from_event_id: str
    to_event_id: str
    kinds: tuple[str, ...]
    lag_ns: int


@dataclass(frozen=True)
class _Path:
    elapsed_ns: int
    feedback_edge_count: int
    event_ids: tuple[str, ...]
    edges: tuple[_Edge, ...]


@dataclass(frozen=True)
class _Score:
    event_id: str
    elapsed_ns: int
    feedback_edge_count: int
    edge_count: int
    start_event_id: str
    predecessor_event_id: str | None
    incoming_edge: _Edge | None


def critical_path_from_timeline(
    events: list[dict[str, Any]],
    feedback_flows: list[dict[str, Any]],
    *,
    alignment: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the longest defensible concrete dependency chain in TimelineIR data.

    Duration is the aligned wall-clock span covered by a dependency chain. It is
    equivalent to the selected event durations plus the non-negative gaps on
    the selected dependency edges. Presentation mirrors and events sharing one
    RT packet are never counted as independent work.
    """

    reasons: Counter[str] = Counter()
    omitted: Counter[str] = Counter()
    _inspect_global_uncertainty(events, feedback_flows, alignment, reasons)
    if diagnostics:
        reasons["analysis_incomplete"] = max(
            reasons["analysis_incomplete"],
            sum(diagnostic.get("category") == "analysis_incomplete"
                and (diagnostic.get("details") or {}).get("analysis_scope") != "rt_queue"
                for diagnostic in diagnostics),
        )
        reasons["invalid_input"] = sum(diagnostic.get("severity") == "error" for diagnostic in diagnostics)

    nodes, event_to_node, merged_count = _concrete_nodes(events, feedback_flows, reasons, omitted)
    counts: dict[str, int] = {
        "analyzed_event_count": len(nodes),
        "merged_event_count": merged_count,
        "omitted_event_count": sum(omitted.values()),
        "dependency_edge_count": 0,
        "feedback_edge_count": 0,
        "rejected_feedback_edge_count": 0,
    }
    if not nodes:
        reasons["no_concrete_execution_events"] += 1
        return _empty_result(
            status="unavailable",
            reasons=reasons,
            omitted=omitted,
            counts=counts,
        )

    edges_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    _add_sequencer_edges(nodes, edges_by_pair, reasons)
    rejected_feedback_edges = _add_feedback_edges(
        nodes,
        event_to_node,
        feedback_flows,
        edges_by_pair,
        reasons,
    )
    edges = _materialize_edges(nodes, edges_by_pair)
    counts["dependency_edge_count"] = len(edges)
    counts["feedback_edge_count"] = sum("feedback" in edge.kinds for edge in edges)
    counts["rejected_feedback_edge_count"] = rejected_feedback_edges

    topological_order, cyclic_node_count = _topological_order(nodes, edges)
    if cyclic_node_count:
        reasons["dependency_cycle"] += cyclic_node_count
        return _empty_result(
            status="unavailable",
            reasons=reasons,
            omitted=omitted,
            counts=counts,
            schedule=_schedule_summary(nodes),
        )

    path = _longest_path(nodes, edges, topological_order)
    nodes_by_id = {node.event_id: node for node in nodes}
    path_nodes = [nodes_by_id[event_id] for event_id in path.event_ids]
    work_duration = sum(node.duration_ns for node in path_nodes)
    dependency_wait = sum(edge.lag_ns for edge in path.edges)
    schedule = _schedule_summary(nodes)
    status = "partial" if any(reasons.values()) else "exact"
    return {
        "status": status,
        "definition": _duration_definition(),
        "duration_ns": path.elapsed_ns,
        "work_duration_ns": work_duration,
        "dependency_wait_ns": dependency_wait,
        "schedule_makespan_ns": schedule["makespan_ns"],
        "schedule_slack_ns": max(0, schedule["makespan_ns"] - path.elapsed_ns),
        "event_ids": list(path.event_ids),
        "sequencer_ids": [node.sequencer_id for node in path_nodes],
        "events": [_node_to_dict(node) for node in path_nodes],
        "dependency_edges": [_edge_to_dict(edge) for edge in path.edges],
        "reasons": _reason_list(reasons),
        "omitted_event_counts": dict(sorted(omitted.items())),
        "counts": counts,
        "schedule": schedule,
    }


def _inspect_global_uncertainty(
    events: list[dict[str, Any]],
    feedback_flows: list[dict[str, Any]],
    alignment: dict[str, Any] | None,
    reasons: Counter[str],
) -> None:
    if alignment is not None:
        sequencer_ids = sorted({str(event.get("sequencer_id", "")) for event in events})
        for sequencer_id in sequencer_ids:
            metadata = alignment.get(sequencer_id)
            if not isinstance(metadata, dict):
                reasons["unaligned_sequencer"] += 1
                continue
            if metadata.get("confidence") != "exact" or metadata.get("unaligned") is True:
                reasons["unaligned_sequencer"] += 1

    matched_receive_ids = {
        str(flow.get("to_event_id"))
        for flow in feedback_flows
        if isinstance(flow, dict) and isinstance(flow.get("to_event_id"), str)
    }
    for event in events:
        kind = str(event.get("kind", ""))
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else {}
        if feedback.get("direction") == "receive" and str(event.get("id", "")) not in matched_receive_ids:
            reasons["unmatched_feedback_receive"] += 1
        if kind == "unknown_region":
            reasons["runtime_dependent_control_flow"] += 1
        elif kind == "branch_region" and (
            event.get("confidence") != "exact"
            or meta.get("branch_taken") == "runtime_dependent"
            or "assumed_branch_path" in meta
        ):
            reasons["runtime_dependent_control_flow"] += 1
        elif kind == "loop_block":
            reasons["loop_summary"] += 1
        elif kind == "analysis_incomplete" and meta.get("analysis_scope") != "rt_queue":
            reasons["analysis_incomplete"] += 1


def _concrete_nodes(
    events: list[dict[str, Any]],
    feedback_flows: list[dict[str, Any]],
    reasons: Counter[str],
    omitted: Counter[str],
) -> tuple[list[_Node], dict[str, str], int]:
    flow_endpoint_ids = {
        str(flow[field])
        for flow in feedback_flows
        for field in ("from_event_id", "to_event_id")
        if isinstance(flow, dict) and isinstance(flow.get(field), str)
    }
    candidates: list[tuple[dict[str, Any], int, int, int]] = []
    event_packet_ids: dict[str, str] = {}
    emitted_packet_ids: set[str] = set()
    packet_members: dict[tuple[str, str], list[tuple[dict[str, Any], int, int, int]]] = defaultdict(list)

    for index, event in enumerate(events):
        event_id = str(event.get("id", ""))
        kind = str(event.get("kind", ""))
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        packet_id = meta.get("rt_packet_id")
        if isinstance(packet_id, str) and event_id:
            event_packet_ids[event_id] = packet_id
            if kind == "q1_issue" and meta.get("emits_rt_packet") is True:
                emitted_packet_ids.add(packet_id)
        if kind in _ANALYSIS_ONLY_KINDS:
            omitted[kind or "unknown"] += 1
            continue

        interval = _concrete_aligned_interval(event)
        if interval is None:
            reasons["non_concrete_timing"] += 1
            omitted["non_concrete_timing"] += 1
            continue
        start_ns, end_ns, duration_ns = interval
        if event.get("confidence") != "exact":
            reasons["non_exact_confidence"] += 1
        candidate = (event, index, start_ns, end_ns)
        if isinstance(packet_id, str):
            packet_members[(str(event.get("sequencer_id", "")), packet_id)].append(candidate)
        else:
            candidates.append(candidate)

    representative_by_packet: dict[str, str] = {}
    merged_count = 0
    for (_sequencer_id, packet_id), members in sorted(packet_members.items()):
        intervals = {(member[2], member[3]) for member in members}
        if len(intervals) > 1:
            reasons["inconsistent_rt_packet_mirrors"] += 1
        representative = min(
            members,
            key=lambda item: (
                0 if str(item[0].get("id", "")) in flow_endpoint_ids else 1,
                item[1],
                str(item[0].get("id", "")),
            ),
        )
        candidates.append(representative)
        representative_id = str(representative[0].get("id", ""))
        representative_by_packet[packet_id] = representative_id
        merged_count += len(members) - 1
        if len(members) > 1:
            omitted["rt_packet_mirror"] += len(members) - 1

    event_to_node: dict[str, str] = {}
    nodes: list[_Node] = []
    for event, index, start_ns, end_ns in sorted(candidates, key=lambda item: item[1]):
        event_id = str(event.get("id", ""))
        sequencer_id = str(event.get("sequencer_id", ""))
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        packet_id = meta.get("rt_packet_id")
        merged_event_ids = (event_id,)
        if isinstance(packet_id, str):
            merged_event_ids = tuple(
                str(member[0].get("id", ""))
                for member in packet_members[(sequencer_id, packet_id)]
            )
        node = _Node(
            event_id=event_id,
            sequencer_id=sequencer_id,
            kind=str(event.get("kind", "")),
            label=str(event.get("label", "")),
            start_ns=start_ns,
            end_ns=end_ns,
            duration_ns=end_ns - start_ns,
            source_index=index,
            merged_event_ids=merged_event_ids,
        )
        nodes.append(node)
        for merged_event_id in merged_event_ids:
            event_to_node[merged_event_id] = event_id

    for event_id, packet_id in event_packet_ids.items():
        representative_id = representative_by_packet.get(packet_id)
        if representative_id is not None:
            event_to_node[event_id] = representative_id
    missing_packet_ids = emitted_packet_ids - set(representative_by_packet)
    if missing_packet_ids:
        reasons["unrepresented_rt_packet"] += len(missing_packet_ids)
    return nodes, event_to_node, merged_count


def _concrete_aligned_interval(event: dict[str, Any]) -> tuple[int, int, int] | None:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    start_ns = _plain_int(meta.get("aligned_t0"))
    end_ns = _plain_int(meta.get("aligned_t1"))
    duration = event.get("duration")
    if not isinstance(duration, dict) or duration.get("kind") != "concrete":
        return None
    duration_ns = _plain_int(duration.get("value"))
    if start_ns is None or end_ns is None or duration_ns is None:
        return None
    if duration_ns < 0 or end_ns < start_ns or duration_ns != end_ns - start_ns:
        return None
    return start_ns, end_ns, duration_ns


def _plain_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _add_sequencer_edges(
    nodes: list[_Node],
    edges_by_pair: dict[tuple[str, str], set[str]],
    reasons: Counter[str],
) -> None:
    nodes_by_sequencer: dict[str, list[_Node]] = defaultdict(list)
    for node in nodes:
        nodes_by_sequencer[node.sequencer_id].append(node)
    for sequencer_nodes in nodes_by_sequencer.values():
        ordered = sorted(
            sequencer_nodes,
            key=lambda node: (node.start_ns, node.source_index, node.end_ns, node.event_id),
        )
        for before, after in zip(ordered, ordered[1:]):
            if after.start_ns < before.end_ns:
                reasons["overlapping_sequencer_events"] += 1
                continue
            edges_by_pair[(before.event_id, after.event_id)].add("sequencer_order")


def _add_feedback_edges(
    nodes: list[_Node],
    event_to_node: dict[str, str],
    feedback_flows: list[dict[str, Any]],
    edges_by_pair: dict[tuple[str, str], set[str]],
    reasons: Counter[str],
) -> int:
    nodes_by_id = {node.event_id: node for node in nodes}
    rejected = 0
    for flow in sorted(feedback_flows, key=lambda item: str(item.get("id", ""))):
        source_id = event_to_node.get(str(flow.get("from_event_id", "")))
        target_id = event_to_node.get(str(flow.get("to_event_id", "")))
        if source_id is None or target_id is None or source_id == target_id:
            reasons["unresolved_feedback_endpoint"] += 1
            rejected += 1
            continue
        source = nodes_by_id[source_id]
        target = nodes_by_id[target_id]
        if target.start_ns < source.end_ns:
            reasons["backward_or_overlapping_feedback_edge"] += 1
            rejected += 1
            continue
        edges_by_pair[(source_id, target_id)].add("feedback")
    return rejected


def _materialize_edges(
    nodes: list[_Node],
    edges_by_pair: dict[tuple[str, str], set[str]],
) -> list[_Edge]:
    nodes_by_id = {node.event_id: node for node in nodes}
    return [
        _Edge(
            from_event_id=source_id,
            to_event_id=target_id,
            kinds=tuple(sorted(kinds)),
            lag_ns=nodes_by_id[target_id].start_ns - nodes_by_id[source_id].end_ns,
        )
        for (source_id, target_id), kinds in sorted(edges_by_pair.items())
    ]


def _topological_order(nodes: list[_Node], edges: list[_Edge]) -> tuple[list[str], int]:
    nodes_by_id = {node.event_id: node for node in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node.event_id: 0 for node in nodes}
    for edge in edges:
        adjacency[edge.from_event_id].append(edge.to_event_id)
        indegree[edge.to_event_id] += 1
    ready = [
        (_node_sort_key(node), node.event_id)
        for node in nodes
        if indegree[node.event_id] == 0
    ]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _sort_key, event_id = heapq.heappop(ready)
        order.append(event_id)
        for target_id in adjacency[event_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                heapq.heappush(ready, (_node_sort_key(nodes_by_id[target_id]), target_id))
    return order, len(nodes) - len(order)


def _longest_path(nodes: list[_Node], edges: list[_Edge], topological_order: list[str]) -> _Path:
    nodes_by_id = {node.event_id: node for node in nodes}
    incoming: dict[str, list[_Edge]] = defaultdict(list)
    for edge in edges:
        incoming[edge.to_event_id].append(edge)
    best: dict[str, _Score] = {}
    for event_id in topological_order:
        node = nodes_by_id[event_id]
        current = _Score(
            event_id=event_id,
            elapsed_ns=node.duration_ns,
            feedback_edge_count=0,
            edge_count=0,
            start_event_id=event_id,
            predecessor_event_id=None,
            incoming_edge=None,
        )
        for edge in sorted(incoming[event_id], key=lambda item: (item.from_event_id, item.kinds)):
            predecessor = best[edge.from_event_id]
            candidate = _Score(
                event_id=event_id,
                elapsed_ns=predecessor.elapsed_ns + edge.lag_ns + node.duration_ns,
                feedback_edge_count=predecessor.feedback_edge_count + ("feedback" in edge.kinds),
                edge_count=predecessor.edge_count + 1,
                start_event_id=predecessor.start_event_id,
                predecessor_event_id=edge.from_event_id,
                incoming_edge=edge,
            )
            if _score_is_better(candidate, current):
                current = candidate
        best[event_id] = current
    selected: _Score | None = None
    for event_id in topological_order:
        candidate = best[event_id]
        if selected is None or _score_is_better(candidate, selected):
            selected = candidate
    assert selected is not None
    return _reconstruct_path(selected, best)


def _score_is_better(candidate: _Score, current: _Score) -> bool:
    if candidate.elapsed_ns != current.elapsed_ns:
        return candidate.elapsed_ns > current.elapsed_ns
    if candidate.feedback_edge_count != current.feedback_edge_count:
        return candidate.feedback_edge_count > current.feedback_edge_count
    candidate_key = (
        candidate.edge_count,
        candidate.start_event_id,
        candidate.event_id,
        candidate.predecessor_event_id or "",
    )
    current_key = (
        current.edge_count,
        current.start_event_id,
        current.event_id,
        current.predecessor_event_id or "",
    )
    return candidate_key < current_key


def _reconstruct_path(selected: _Score, scores: dict[str, _Score]) -> _Path:
    event_ids: list[str] = []
    edges: list[_Edge] = []
    current = selected
    while True:
        event_ids.append(current.event_id)
        if current.predecessor_event_id is None or current.incoming_edge is None:
            break
        edges.append(current.incoming_edge)
        current = scores[current.predecessor_event_id]
    event_ids.reverse()
    edges.reverse()
    return _Path(
        elapsed_ns=selected.elapsed_ns,
        feedback_edge_count=selected.feedback_edge_count,
        event_ids=tuple(event_ids),
        edges=tuple(edges),
    )


def _node_sort_key(node: _Node) -> tuple[int, int, str, str]:
    return node.start_ns, node.end_ns, node.sequencer_id, node.event_id


def _schedule_summary(nodes: list[_Node]) -> dict[str, int]:
    start_ns = min(node.start_ns for node in nodes)
    end_ns = max(node.end_ns for node in nodes)
    return {"start_ns": start_ns, "end_ns": end_ns, "makespan_ns": end_ns - start_ns}


def _empty_result(
    *,
    status: str,
    reasons: Counter[str],
    omitted: Counter[str],
    counts: dict[str, int],
    schedule: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "definition": _duration_definition(),
        "duration_ns": None,
        "work_duration_ns": None,
        "dependency_wait_ns": None,
        "schedule_makespan_ns": schedule["makespan_ns"] if schedule is not None else None,
        "schedule_slack_ns": None,
        "event_ids": [],
        "sequencer_ids": [],
        "events": [],
        "dependency_edges": [],
        "reasons": _reason_list(reasons),
        "omitted_event_counts": dict(sorted(omitted.items())),
        "counts": counts,
        "schedule": schedule,
    }


def _duration_definition() -> str:
    return (
        "Aligned wall-clock span from the first selected event start to the last selected event end; "
        "equal to selected event work plus non-negative dependency-edge gaps."
    )


def _reason_list(reasons: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"code": code, "count": count, "message": _REASON_MESSAGES[code]}
        for code, count in sorted(reasons.items())
        if count > 0
    ]


def _node_to_dict(node: _Node) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_id": node.event_id,
        "sequencer_id": node.sequencer_id,
        "kind": node.kind,
        "label": node.label,
        "start_ns": node.start_ns,
        "end_ns": node.end_ns,
        "duration_ns": node.duration_ns,
    }
    if len(node.merged_event_ids) > 1:
        result["merged_event_ids"] = list(node.merged_event_ids)
    return result


def _edge_to_dict(edge: _Edge) -> dict[str, Any]:
    return {
        "from_event_id": edge.from_event_id,
        "to_event_id": edge.to_event_id,
        "kinds": list(edge.kinds),
        "lag_ns": edge.lag_ns,
    }
