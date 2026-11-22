"""Path exploration for the optional Z3 verifier."""
from __future__ import annotations

import operator
from dataclasses import dataclass, field, replace
from time import monotonic

import z3

from q1timeline.analysis.smt import Query, VerificationResult
from q1timeline.analysis.smt_queue import BufferedQueue, FixedStartQueue
from q1timeline.q1asm import core, core_alu, core_rt
from q1timeline.q1asm.ast import Instr
from q1timeline.q1asm.compiled_addresses import next_compiled_pc
from q1timeline.q1asm.instruction_table import branch_duration, get_instruction_spec


_RELATIONS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le,
              ">": operator.gt, ">=": operator.ge}


@dataclass(frozen=True)
class _State:
    condition: z3.BoolRef
    pc: int = 0
    steps: int = 0
    registers: dict[str, z3.BitVecRef] = field(default_factory=dict)
    busy: tuple[str, ...] = ()
    flags: dict[str, z3.BoolRef] | None = None
    matched: int = 0
    q1: int | z3.ArithRef = 0
    rt: int | z3.ArithRef = 0
    sync: int | z3.ArithRef = 0
    fifo: tuple = ()
    skip: bool | z3.BoolRef = False
    else_ns: int | z3.ArithRef = 0
    queue: BufferedQueue | FixedStartQueue | None = None
    prefill_delay: int | z3.ArithRef = 0
    skipped_issues: int | z3.ArithRef = 0


class _EndPath(Exception):
    """An infeasible, invalid, unsupported, or truncated continuation."""


class _Witness(Exception):
    def __init__(self, result):
        self.result = result


class Explorer:
    def __init__(self, code: tuple[Instr, ...], symbolic_args, domain,
                 query: Query, max_steps: int, max_paths: int, deadline: float | None):
        self.code = code
        self.symbolic_args = symbolic_args
        self.query = query
        self.max_steps = max_steps
        self.max_paths = max_paths
        self.paths = 1
        self.incomplete = None
        self.visited_observation = False
        # Both -1 and 4294967295 are legal DEF values. They become the same
        # 32-bit word only when the instruction reads its operand.
        self.inputs = {name: z3.BitVec(f"def_{name}", 33) for name in domain}
        self.words = {name: z3.Extract(31, 0, word) for name, word in self.inputs.items()}
        self.deadline = deadline
        self.solver = z3.Solver()
        self.solver.set(timeout=5000)
        for name, (low, high) in domain.items():
            self.solver.add(low <= self.inputs[name], self.inputs[name] <= high)

    def run(self):
        queue = None
        if self.query.kind == "underflow" and any(get_instruction_spec(i.op).emits_rt_packet for i in self.code):
            queue = BufferedQueue() if self.query.buffered_start else FixedStartQueue.start(self.query.value)
        frontier = [_State(z3.BoolVal(True), queue=queue)]
        while frontier:
            if self.deadline is not None and monotonic() >= self.deadline:
                return VerificationResult("unknown", "Verification timed out.")
            state = frontier.pop()
            try:
                if state.steps >= self.max_steps:
                    raise _EndPath(f"Reached the {self.max_steps} instruction limit.")
                if not 0 <= state.pc < len(self.code):
                    raise _EndPath("Execution reached a PC without an instruction or stop.")
                for index, successor in enumerate(self._step(state)):
                    if index:
                        if self.paths >= self.max_paths:
                            self.incomplete = f"Reached the {self.max_paths} path limit."
                            break
                        self.paths += 1
                    frontier.append(successor)
            except _EndPath as end:
                if end.args:
                    self.incomplete = str(end)
            except _Witness as witness:
                return witness.result
        if self.incomplete:
            return VerificationResult("unknown", self.incomplete)
        existential = self.query.kind in {"reachable", "path", "underflow"}
        if not existential and self.query.pc is not None and not self.visited_observation:
            return VerificationResult("proved", "The assertion label is unreachable for every input in the domain.")
        return VerificationResult("unreachable" if existential else "proved")

    def _model(self, condition, *, prune_unknown=False):
        if self.deadline is not None:
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise _EndPath("Verification timed out.")
            self.solver.set(timeout=max(1, min(5000, int(remaining * 1000))))
        status = self.solver.check(condition)
        if status == z3.sat:
            return self.solver.model()
        if status == z3.unknown:
            self.incomplete = f"Solver returned unknown: {self.solver.reason_unknown()}."
            if prune_unknown:
                raise _EndPath()
        return None

    def _restrict(self, state, test, reason=None):
        """Keep the feasible valid part and account for any unresolved invalid part."""
        test = z3.simplify(z3.And(test))
        if z3.is_true(test):
            return state
        if z3.is_false(test):
            if reason:
                self.incomplete = reason
            return None
        if reason and self._model(z3.And(state.condition, z3.Not(test))) is not None:
            self.incomplete = reason
        condition = z3.And(state.condition, test)
        if self._model(condition) is not None:
            return replace(state, condition=condition)
        return None

    def _require(self, state, test, reason):
        valid = self._restrict(state, test, reason)
        if valid is None:
            raise _EndPath()
        return valid

    def _witness(self, state, test, status, value=None):
        test = z3.simplify(z3.And(test))
        if z3.is_false(test):
            return
        model = self._model(z3.And(state.condition, test), prune_unknown=True)
        if model is not None:
            issue_count = state.steps + 1 + state.skipped_issues
            if z3.is_expr(issue_count):
                # Prefer an input whose independently replayed prefix fits the issue budget.
                small = self._model(z3.And(state.condition, test, issue_count <= self.max_steps))
                if small is not None:
                    model = small
                issue_count = model.eval(issue_count).as_long()
            observed = model.eval(value).as_long() if z3.is_expr(value) else value
            inputs = {name: model.eval(word, model_completion=True).as_signed_long()
                      for name, word in self.inputs.items()}
            raise _Witness(VerificationResult(status, def_value=next(iter(inputs.values()), None),
                                              register_value=observed, steps=issue_count, inputs=inputs))

    def _step(self, state):
        item = self.code[state.pc]
        op = item.op
        if self.query.kind in {"q1_time", "rt_time", "register"} and self.query.pc == state.pc:
            self.visited_observation = True
            if self.query.kind == "register" and self.query.register in state.busy:
                self.incomplete = f"{self.query.register} is not ready at line {item.source.line}."
            elif self.query.kind == "register" and self.query.register not in state.registers:
                self.incomplete = f"{self.query.register} has no readable value at line {item.source.line}."
            else:
                self._finish(state, ())
        if op not in core.LOCAL_OPS or op == "illegal":
            raise _EndPath(f"Unsupported instruction {op} on line {item.source.line}.")
        if self.query.kind == "underflow" and (op.startswith("acquire") or op in {"set_digital", "wait_sync"}):
            raise _EndPath(f"Simulation side effects of {op} are outside the fixed-start FIFO model.")
        destinations = core.destination_indexes(op)
        inputs = []
        for i, arg in enumerate(item.args):
            if i in destinations:
                continue
            if arg.kind == "reg":
                if arg.value in state.busy:
                    raise _EndPath(f"{arg.value} is not ready on line {item.source.line}.")
                if arg.value in state.registers:
                    value = state.registers[arg.value]
                elif op == "set_cond" and i in (1, 2):
                    state = self._require(state, inputs[0] == 0, f"{arg.value} is uninitialized.")
                    value = z3.BitVecVal(0, 32)
                else:
                    raise _EndPath(f"{arg.value} is uninitialized on line {item.source.line}.")
            elif i in self.symbolic_args[state.pc]:
                value = self.words[self.symbolic_args[state.pc][i]]
            else:
                value = z3.BitVecVal(int(arg.value), 32)
            inputs.append(value)
        if op in core.BRANCH_OPS:
            yield from self._branch(state, item, inputs)
            return

        words = ()
        dwell = None
        if op in core_alu.ALU_OPS:
            if op in {"asl", "asr", "lsl", "lsr"}:
                count = core_alu.shift_count(item, inputs)
                state = self._require(state, z3.ULE(count, 32), f"Invalid shift count on line {item.source.line}.")
            if len(destinations) == 2 and item.args[2].value == item.args[3].value:
                raise _EndPath("Full-product multiply requires distinct destination registers.")
            words, flags = core_alu.evaluate(item, tuple(inputs))
            state = replace(state, flags=flags)
        elif op == "move":
            words = inputs
        elif op == "set_cond":
            state = self._condition(state, item, inputs)
        elif op in core_rt.RT_OPS:
            for index, role, low, high in core_rt.OPERAND_RANGES.get(op, ()):
                state = self._require(state, z3.And(z3.UGE(inputs[index], low), z3.ULE(inputs[index], high)),
                                      f"Invalid {op} {role} on line {item.source.line}.")
            duration = inputs[-1]
            state = self._require(state, _valid_duration(duration), f"Invalid {op} duration on line {item.source.line}.")
            wait = z3.BV2Int(duration)
            dwell = z3.simplify(z3.If(state.skip, state.else_ns, wait))
            if op == "wait_sync":
                # Synchronization overhead changes FIFO release times for
                # subsequent Q1 issues, while the analytical RT clock uses dwell.
                sync_adjustment = z3.If(wait == 0, 0, core.Q1_CORE_SYNC_OVERHEAD_NS + wait - _spacing(dwell))
            else:
                sync_adjustment = 0
        elif op in core_rt.LATCH_OPS:
            for index, role, low, high in core_rt.OPERAND_RANGES.get(op, ()):
                if item.args[index].kind == "reg":
                    value = inputs[index]
                    if op == "set_mrk":
                        value = value & 15
                    number = z3.BV2Int(value, is_signed=op in {"set_awg_gain", "set_awg_offs", "set_freq"})
                    state = self._require(state, z3.And(low <= number, number <= high),
                                          f"{op} register {role} is outside its supported range.")
        writes = {item.args[i].value: value for i, value in zip(destinations, words, strict=True)}
        state = replace(state, registers={**state.registers, **writes} if writes else state.registers,
                        busy=tuple(writes))
        cost = get_instruction_spec(op).q1_time_model(item)
        state = self._timing(state, cost, dwell, stop=op == "stop")
        if dwell is not None:
            state = replace(state, sync=state.sync + sync_adjustment)
        self._observe_issue(state)
        if op == "stop":
            if self.query.pc is None:
                self._finish(state, inputs)
            return
        yield replace(state, steps=state.steps + 1, pc=state.pc + 1)

    def _condition(self, state, item, inputs):
        enable, mask, operation, duration = inputs
        valid = z3.And(z3.ULE(enable, 1), _valid_duration(duration),
                       z3.Or(enable == 0, z3.And(z3.ULE(mask, 32767), z3.ULE(operation, 5))))
        state = self._require(state, valid, f"Invalid or unverified set_cond on line {item.source.line}.")
        # No external inputs or device commands are modeled. All trigger
        # counts are zero, so only an empty vs nonempty mask can affect a decision.
        receiver = core.DeviceState()
        external = core.ExternalInputs()
        take = z3.Or(*[z3.And(operation == i, z3.If(mask == 0, receiver.condition_taken(0, i, external),
                                                    receiver.condition_taken(1, i, external))) for i in range(6)])
        return replace(state, skip=z3.And(enable != 0, z3.Not(take)), else_ns=z3.BV2Int(duration))

    def _branch(self, state, item, inputs):
        op = item.op
        target = inputs[-1]
        state = replace(state, busy=())
        # Before the first enqueue there is no armed RT deadline. A counted
        # delay containing only nops may be collapsed exactly, including the
        # zero counter's full 32-bit wrap. Preserve its time for witness replay.
        if op == "loop" and isinstance(state.queue, BufferedQueue) and not state.queue.dwells:
            dest = z3.simplify(target)
            if item.args[-1].raw.startswith("@") and z3.is_bv_value(dest) and dest.as_long() <= state.pc:
                body = self.code[dest.as_long():state.pc]
                if all(ins.op == "nop" for ins in body):
                    repeats = z3.BV2Int(inputs[0] - 1)
                    cost = get_instruction_spec(op).q1_time_model(item)
                    extra = sum(get_instruction_spec(ins.op).q1_time_model(ins) for ins in body) + cost
                    _, words, flags = core.branch_effect(item, (z3.BitVecVal(1, 32), target), state.flags)
                    state = self._timing(state, branch_duration(cost, False), None)
                    pc = next_compiled_pc(state.pc, item, taken=False, target=None, fault=False, code_size=len(self.code))
                    yield replace(state, registers={**state.registers, item.args[0].value: words[0]}, flags=flags,
                                  prefill_delay=state.prefill_delay + repeats * extra,
                                  skipped_issues=state.skipped_issues + repeats * (len(body) + 1),
                                  pc=pc, steps=state.steps + 1)
                    return
        if not item.args[-1].raw.startswith("@"):
            state = self._require(state, z3.ULE(target, 16383), f"Branch target outside the address range at PC {state.pc}.")
        condition, words, flags = core.branch_effect(item, tuple(inputs), state.flags)
        if condition is None:
            raise _EndPath(f"{op} requires known status flags.")
        if flags is not None:
            state = replace(state, flags=flags)
        if words:
            counter = item.args[0].value
            if item.args[1].kind == "reg" and item.args[1].value == counter:
                raise _EndPath("loop target register is not ready after counter decrement.")
            state = replace(state, registers={**state.registers, counter: words[0]})

        cost = get_instruction_spec(op).q1_time_model(item)
        for taken, test in ((False, z3.Not(condition)), (True, condition)):
            path = self._restrict(state, test)
            if path is None:
                continue
            if taken:
                path = self._restrict(path, z3.ULT(target, len(self.code)), f"Invalid taken target at PC {state.pc}.")
                if path is None:
                    continue
            path = self._timing(path, branch_duration(cost, taken), None)
            if (self.query.kind == "path" and path.matched < len(self.query.path)
                    and self.query.path[path.matched] == (path.pc, taken)):
                path = replace(path, matched=path.matched + 1)
            self._observe_issue(path)
            if not taken:
                pc = next_compiled_pc(path.pc, item, taken=False, target=None, fault=False, code_size=len(self.code))
                yield replace(path, pc=pc, steps=path.steps + 1)
            else:
                target_expr = z3.simplify(target)
                if z3.is_bv_value(target_expr):
                    yield replace(path, pc=target_expr.as_long(), steps=path.steps + 1)
                else:
                    # A register target may enter any assembled instruction,
                    # including a macro's second slot. Enumerate feasible
                    # addresses only; the ordinary path budget also bounds this.
                    remaining = path.condition
                    while (model := self._model(remaining)) is not None:
                        pc = model.eval(target).as_long()
                        yield replace(path, pc=pc, steps=path.steps + 1, condition=z3.And(remaining, target == pc))
                        remaining = z3.And(remaining, target != pc)

    def _timing(self, state, cost, dwell, *, stop=False):
        if state.queue is not None:
            if z3.is_int_value(dwell):
                dwell = dwell.as_long()
            queue, failed, when = state.queue.issue(cost, dwell=dwell, stop=stop)
            if when is not None:
                self._witness(state, failed, "reachable", when + state.prefill_delay)
            state = replace(state, queue=queue)
        if self.query.kind not in {"q1_time", "rt_time"}:
            return state
        q1 = state.q1 + cost
        fifo = state.fifo
        if (dwell is not None or stop) and len(fifo) == core.RT_FIFO_CAPACITY:
            release = fifo[0] + core.Q1_START_LEAD_NS
            q1 = z3.If(q1 >= release, q1, release)
            fifo = fifo[1:]
        rt = state.rt
        if dwell is not None:
            fifo += (rt + state.sync,)
            rt = rt + _spacing(dwell)
        return replace(state, q1=q1, rt=rt, fifo=fifo)

    def _observe_issue(self, state):
        if self.query.kind == "reachable" and state.pc == self.query.pc:
            self._witness(state, True, "reachable")
        if self.query.kind == "path" and state.matched == len(self.query.path):
            self._witness(state, True, "reachable")

    def _finish(self, state, inputs):
        kind = self.query.kind
        if kind == "register":
            if self.query.register not in state.registers:
                raise _EndPath(f"{self.query.register} has no readable final value.")
            word = state.registers[self.query.register]
            observed = z3.BV2Int(word)
            # Signed Z3 comparisons on 34 bits represent both unsigned words
            # and the permitted exclusive upper bound 2**32 as non-negative.
            property_holds = _RELATIONS[self.query.op](z3.ZeroExt(2, word), self.query.value)
        elif kind == "stop":
            observed = z3.BV2Int(inputs[0], is_signed=True) if inputs else 0
        elif kind in {"q1_time", "rt_time"}:
            observed = state.q1 if kind == "q1_time" else state.rt
        else:
            return
        if kind != "register":
            property_holds = _RELATIONS[self.query.op](observed, self.query.value)
        self._witness(state, z3.Not(property_holds), "counterexample", observed)


def _valid_duration(word):
    return z3.Or(word == 0, z3.And(z3.UGE(word, 4), z3.ULE(word, 65535)))


def _spacing(dwell):
    return z3.If(dwell == 0, 4, dwell)
