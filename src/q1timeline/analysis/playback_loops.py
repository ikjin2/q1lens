"""Loop annotations from bounded execution traces, without changing execution."""
from __future__ import annotations

from dataclasses import dataclass

from q1timeline.analysis.values import Value


@dataclass(frozen=True)
class ExecutedStep:
    pc: int
    next_pc: int | None
    event_start: int
    event_end: int
    issue_index: int
    rt_start: Value
    rt_end: Value
    branch_taken: bool = False
    branch_target: int | None = None
    jump: bool = False
    indirect: bool = False
    continuation: bool = False
    guard_only: bool = False


def observed_loops(sequencer_id: str, trace: list[ExecutedStep], *, stopped: bool) -> list[dict]:
    if not trace:
        return []
    entries: dict[int, list[int]] = {}
    candidates: set[tuple[int, int]] = set()
    calls: list[int] = []
    call_edges: set[tuple[int, int]] = set()
    return_edges: set[tuple[int, int]] = set()
    callees: set[int] = set()
    for index, step in enumerate(trace):
        entries.setdefault(step.pc, []).append(index)
        if step.branch_target in entries:
            candidates.add((step.pc, step.branch_target))
        if not step.jump or step.next_pc == step.pc + 1:
            continue
        # Confirm a call only when an indirect jump returns to the known
        # continuation of the nearest outstanding invocation. No register name
        # or address ordering is special; unconfirmed ordinary jumps stay jumps.
        matched = next((position for position in range(len(calls) - 1, -1, -1)
                        if step.indirect and trace[calls[position]].pc + 1 == step.next_pc), None)
        if matched is not None:
            call = trace[calls[matched]]
            call_edges.add((call.pc, call.next_pc))
            return_edges.add((step.pc, step.next_pc))
            callees.add(call.next_pc)
            del calls[matched:]
        elif step.continuation:
            calls.append(index)
    # A paused invocation of an already observed routine is not a new loop.
    call_edges.update((trace[index].pc, trace[index].next_pc) for index in calls
                      if trace[index].next_pc in callees)
    candidates.difference_update(call_edges | return_edges)
    successors: dict[int, set[int]] = {}
    for step in trace:
        edge = (step.pc, step.next_pc)
        if edge in call_edges:
            successors.setdefault(step.pc, set()).add(step.pc + 1)
        elif step.next_pc is not None and edge not in return_edges:
            successors.setdefault(step.pc, set()).add(step.next_pc)

    backedges: dict[int, set[int]] = {}
    for header in {target for _, target in candidates}:
        # Dominance on the observed PC graph rejects returns and irreducible
        # cycles. Bodies below use actual spans, so shared callees need not be
        # dominated by an outer header to belong to its executed iteration.
        reachable: set[int] = set()
        # Analyze callers through their observed continuation and routines from
        # their own entry. Mixing return destinations from different calls would
        # invent paths bypassing an outer header when a callee is also used before it.
        pending = list(({trace[0].pc} | callees) - {header})
        while pending:
            pc = pending.pop()
            if pc == header or pc in reachable:
                continue
            reachable.add(pc)
            pending.extend(successors.get(pc, ()))
        tails = {source for source, target in candidates if target == header and source not in reachable}
        if tails:
            backedges[header] = tails

    result = []
    for header, tails in backedges.items():
        body = {header}
        returns: dict[int, int] = {}
        exits: dict[int, int] = {}
        start = None
        for index, step in enumerate(trace):
            if step.pc == header:
                start = index
            if start is not None and step.pc in tails and step.branch_target == header:
                (returns if step.branch_taken else exits)[start] = index + 1
                body.update(item.pc for item in trace[start:index + 1])
        invocations: list[list[int]] = []
        for entry in entries[header]:
            if invocations and returns.get(invocations[-1][-1]) == entry:
                invocations[-1].append(entry)
            else:
                invocations.append([entry])
        for invocation_index, invocation in enumerate(invocations):
            spans = [(entry, returns[entry]) for entry in invocation if entry in returns]
            if not spans:
                continue  # A single visit is not observed repetition.
            boundary = (invocations[invocation_index + 1][0]
                        if invocation_index + 1 < len(invocations) else len(trace))
            last_entry = invocation[-1]
            complete = False
            if (last_entry not in returns and last_entry in exits
                    and trace[exits[last_entry] - 1].next_pc not in body):
                spans.append((last_entry, exits[last_entry]))
                complete = True
            elif last_entry not in returns:
                exit_index = next((index for index in range(last_entry, boundary)
                                   if trace[index].pc in body and trace[index].next_pc not in body), None)
                if exit_index is not None and (stopped or boundary < len(trace)
                                               or trace[exit_index].pc in tails):
                    # A failed top-of-loop comparison is not another body
                    # iteration. Keep those Q1 checks outside the folded span;
                    # zero-duration waits and register writes still count.
                    if not all(step.guard_only for step in trace[last_entry:exit_index + 1]):
                        spans.append((last_entry, exit_index + 1))
                    complete = True
                # An unconfirmed new path in a paused iteration stays visible.
            first = trace[spans[0][0]]
            tail = trace[spans[0][1] - 1]
            result.append({
                "loop_id": f"{sequencer_id}:playback-loop:{first.issue_index}:{tail.pc}",
                "start_pc": header, "end_pc": tail.pc, "complete": complete,
                "ranges": [(trace[start].event_start, trace[end - 1].event_end,
                            trace[start].rt_start, trace[end - 1].rt_end) for start, end in spans],
            })
    return sorted(result, key=lambda item: (item["ranges"][0][0], -item["ranges"][-1][1]))
