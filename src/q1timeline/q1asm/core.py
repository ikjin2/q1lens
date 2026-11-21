"""Concrete, single-sequencer instruction semantics.

Clocks follow Concrete mode's analytical timing. A step issues a complete
source instruction, or just the branch when entering the second slot of a macro.
PCs and label values are compiled addresses.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import cast

from q1timeline.feedback_fifo import feedback_q1_completion
from q1timeline.q1asm import core_alu, core_rt
from q1timeline.q1asm.ast import Arg, Instr, Program
from q1timeline.q1asm.compiled_addresses import compile_addresses, next_compiled_pc
from q1timeline.q1asm.core_devices import (
    CoreBlocked, DEVICE_RANGES, DEVICE_RT_OPS, DEVICE_SETTERS, DeviceState, ExternalInputs, FeedbackOverflow,
    check_device_issue,
)
from q1timeline.q1asm.core_rt import rt_dequeue_spacing_ns
from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS, branch_duration, get_instruction_spec
from q1timeline.q1asm.linq_commands import PACKED_LINQ_OPERATIONS, raw_linq_operation
from q1timeline.q1asm.parser import parameter_integer


BRANCH_OPS = STATUS_BRANCH_OPS | {"loop", "jmp", "jge", "jlt"}
LOCAL_OPS = (BRANCH_OPS | core_alu.ALU_OPS | core_rt.LATCH_OPS | core_rt.RT_OPS
             | {"move", "nop", "stop", "illegal", "set_cond"})
SOURCE_OPS = LOCAL_OPS | DEVICE_RT_OPS | DEVICE_SETTERS.keys() | {"fb_pop_data", "fb_pull_data", "sw_req"}
ILLEGAL_INSTRUCTION_FLAG = "SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION"
_WORD_MASK = 0xFFFFFFFF
_SIGN_BIT = 1 << 31
FLAG_NAMES = ("zf", "nf", "cf", "of")
FlagCase = tuple[bool, bool, bool, bool]
# q1simulator 1.4.1 CoreClock schedules RT at 0 ns after Q1 starts at -40 ns.
# Store elapsed Q1 time so the first instruction starts at zero in the UI.
RT_FIFO_CAPACITY = 32
Q1_START_LEAD_NS = 40
Q1_CORE_SYNC_OVERHEAD_NS = 200


def queued_q1_completion(fetch_end_ns: int, released_rt_slot_ns: int) -> int:
    return max(fetch_end_ns, released_rt_slot_ns + Q1_START_LEAD_NS)


@dataclass(frozen=True)
class Flags:
    zf: bool | None = None
    nf: bool | None = None
    cf: bool | None = None
    of: bool | None = None

    def as_dict(self) -> dict[str, bool]:
        return {key: value for key in FLAG_NAMES if (value := getattr(self, key)) is not None}


@dataclass(frozen=True)
class Issue:
    q1_duration: int
    words: tuple[int, ...] = ()
    flags: Flags | None = None
    wait_duration: int | None = None
    stop_code: int | None = None
    taken: bool | None = None
    fault: str | None = None
    latch_write: core_rt.LatchWrite | None = None
    condition: tuple[int, int | None, int | None, int] | None = None

    @property
    def word(self) -> int | None:
        return self.words[0] if self.words else None


def possible_flags(flags: dict[str, bool] | None) -> frozenset[FlagCase]:
    known = flags or {}
    return frozenset(product(*[(known[name],) if name in known else (False, True) for name in FLAG_NAMES]))


def branch_on_flags(
    op: str, flags: dict[str, bool] | None, cases: frozenset[FlagCase] | None = None,
) -> bool | None:
    """Resolve a status jump when all possible flag combinations agree."""
    if cases is not None or (flags is not None and len(flags) < len(FLAG_NAMES)):
        possibilities = cases if cases is not None else possible_flags(flags)
        decisions = {branch_on_flags(op, dict(zip(FLAG_NAMES, values))) for values in possibilities}
        return decisions.pop() if len(decisions) == 1 else None
    if flags is None:
        return None
    zf, nf, cf, of = (flags[name] for name in FLAG_NAMES)
    return {
        "jz": zf, "jnz": not zf, "jo": of, "jno": not of, "js": nf, "jns": not nf,
        "jg": not zf and nf == of, "jl": nf != of, "jle": zf or nf != of,
        # Qblox's Core actions text for jge appears to repeat jg.
        "jge": nf == of, "ja": not cf and not zf, "jae": not cf, "jb": cf, "jbe": cf or zf,
    }.get(op)


def destination_indexes(op: str) -> tuple[int, ...]:
    if op in {"move", "not", "fb_pop_data"}:
        return (1,)
    if op == "fb_pull_data":
        return (0, 1)
    if op in {"mulu32", "muls32"}:
        return (2, 3)
    if op in core_alu.ALU_OPS and op not in {"test", "cmp"}:
        return (2,)
    return ()


def branch_effect(
    instruction: Instr, inputs: tuple[int, ...], flags: dict[str, bool] | None,
) -> tuple[bool | None, tuple[int, ...], dict[str, bool] | None]:
    """Return the branch decision, loop counter write and resulting flags."""
    if instruction.op == "loop":
        bits = core_alu.sub_flags(inputs[0], 1)
        return bits["zf"] ^ True, ((inputs[0] - 1) & _WORD_MASK,), bits
    if instruction.op == "jmp":
        return True, (), None
    if len(instruction.args) == 3:
        bits = core_alu.sub_flags(inputs[0], inputs[1])
        return (bits["cf"] if instruction.op == "jlt" else bits["cf"] ^ True), (), bits
    return branch_on_flags(instruction.op, flags), (), None


def issue(
    instruction: Instr, operands: tuple[int | None, ...] | int, flags: dict[str, bool] | None = None,
) -> Issue:
    """Compute one issue's effects from resolved read operands in source order."""
    op = instruction.op
    cost = get_instruction_spec(op).q1_time_model(instruction)
    inputs = (operands,) if isinstance(operands, int) else operands
    if op == "set_cond":
        core_rt.validate_condition(instruction, inputs)
        return Issue(cost, condition=(cast(int, inputs[0]), inputs[1], inputs[2], cast(int, inputs[3])))
    # Only an inactive set_cond may ignore an unresolved mask or operator.
    values = cast(tuple[int, ...], inputs)
    if op == "move":
        return Issue(cost, words=(values[0] & _WORD_MASK,))
    if op in core_alu.ALU_OPS:
        words, bits = core_alu.evaluate(instruction, values)
        return Issue(cost, words=words, flags=Flags(**bits))
    if op in BRANCH_OPS:
        taken, words, bits = branch_effect(instruction, values, flags)
        if taken is None:
            raise ValueError(f"{op} requires known status flags")
        return Issue(branch_duration(cost, taken), words=words, flags=Flags(**bits) if bits else None, taken=taken)
    if op in core_rt.RT_OPS:
        return Issue(cost, wait_duration=core_rt.rt_duration(instruction, values))
    if op in DEVICE_RT_OPS:
        check_device_issue(op, values)
        duration = values[-1]
        if op == "fb_acq_tb_mock" or (op == "fb_cmd" and raw_linq_operation(values[0]) == "fb_acq_tb_mock"):
            # Firmware does not specify whether this operand is count or duration.
            if not 0 <= duration <= 65535:
                raise ValueError(f"Invalid {op} fourth operand {duration}")
        else:
            duration = core_rt.rt_duration(instruction, values)
        if op == "fb_cmd" and raw_linq_operation(values[0]) == "upd_thres":
            cost = get_instruction_spec("upd_thres").q1_time_model(instruction)
        return Issue(cost, wait_duration=duration)
    if op in core_rt.LATCH_OPS:
        return Issue(cost, latch_write=core_rt.latch_write(instruction, values))
    if op in DEVICE_SETTERS:
        return Issue(cost, latch_write=core_rt.LatchWrite(DEVICE_SETTERS[op], True))
    if op in {"fb_pop_data", "fb_pull_data"}:
        return Issue(cost)  # The FIFO provides the destination words when the read completes.
    if op == "sw_req":
        raise CoreBlocked("Execution behavior for sw_req is unverified")
    if op == "stop":
        operand = values[0] & _WORD_MASK if values else 0
        return Issue(cost, stop_code=operand - (1 << 32) if operand >= _SIGN_BIT else operand)
    if op == "illegal":
        return Issue(cost, fault=ILLEGAL_INSTRUCTION_FLAG)
    if op == "nop":
        return Issue(cost)
    raise ValueError(f"Unsupported core instruction {op}")


def reference_sync_adjustment(wait_after: int, selected_dwell: int) -> int:
    # q1simulator rejects zero dwell; Q1Lens keeps its zero-duration dequeue slot.
    if wait_after == 0:
        return 0
    # wait_sync always executes the requested dwell in q1simulator, even under set_cond.
    return Q1_CORE_SYNC_OVERHEAD_NS + rt_dequeue_spacing_ns(wait_after) - rt_dequeue_spacing_ns(selected_dwell)


@dataclass(frozen=True)
class RegisterWrite:
    index: int
    value: int


@dataclass(frozen=True)
class CoreState:
    """State at an issue boundary. An unwritten register has value None."""

    pc: int = 0
    registers: tuple[int | None, ...] = (None,) * 64
    # ALU writes become readable after the following instruction has issued.
    pending_writes: tuple[RegisterWrite, ...] = ()
    flags: Flags | None = None
    q1_ns: int = 0
    rt_ns: int = 0
    stop_code: int | None = None
    fault: str | None = None
    latches: core_rt.Latches = core_rt.Latches()
    condition: tuple[int, int | None, int | None, int] = (0, 0, 0, 0)
    devices: DeviceState = DeviceState()
    rt_fifo_starts: tuple[int, ...] = ()
    reference_sync_ns: int = 0

    @property
    def halted(self) -> bool:
        return self.stop_code is not None or self.fault is not None


@dataclass(frozen=True)
class RtPacket:
    q1_issue_t0: int
    q1_issue_t1: int
    rt_t0: int
    rt_t1: int
    op: str = "wait"
    operands: tuple[int, ...] = ()
    applied: tuple[core_rt.LatchWrite, ...] = ()
    skipped: bool = False
    device_op: str | None = None
    device_operands: tuple[int, ...] = ()


WaitPacket = RtPacket


@dataclass(frozen=True)
class Transition:
    state: CoreState
    packet: RtPacket | None
    issue: Issue
    retired_writes: tuple[RegisterWrite, ...]

    def __iter__(self):
        yield self.state
        yield self.packet


def _register_index(arg: Arg) -> int:
    # The parser normalizes decimal and hexadecimal spellings to R<decimal>.
    return int(str(arg.value)[1:])


def _bind_operand(arg: Arg, bindings: dict[str, Arg], labels: dict[str, int]) -> Arg:
    if arg.kind in {"symbol", "placeholder"}:
        arg = bindings.get(str(arg.value), arg)
    if arg.kind == "label":
        name = str(arg.value)
        if name not in labels:
            raise ValueError(f"Unknown label {arg.raw}")
        return Arg("imm", labels[name], arg.raw)
    if arg.kind == "reg":
        if not arg.raw.startswith("R") or not 0 <= _register_index(arg) < 64:
            raise ValueError(f"Invalid register {arg.raw}")
    elif arg.kind != "imm":
        raise ValueError(f"Core requires a concrete operand, got {arg.raw}")
    return arg


def prepare_program(program: Program, *, params: dict[str, object] | None = None) -> tuple[Instr, ...]:
    """Bind concrete project parameters, DEFs and labels, then index by compiled PC."""
    for diagnostic in program.diagnostics:
        if diagnostic.severity == "error":
            raise ValueError(diagnostic.message)
    for item in program.instructions:
        if item.op not in SOURCE_OPS:
            raise ValueError(f"Unsupported core instruction {item.op}")
    by_pc = {item.pc: item for item in program.instructions}
    layout = compile_addresses(by_pc, program.labels)
    labels = program.labels
    items = program.instructions
    if layout is not None:
        labels = layout.labels
        items = [target.interior or by_pc[target.source_pc] for _, target in sorted(layout.targets.items())]
    bindings = {}
    for name, raw in (params or {}).items():
        word = parameter_integer(raw)
        if word is not None:
            bindings[name] = Arg("imm", word, str(raw))
    for name, arg in program.defs.items():
        if arg.kind in {"symbol", "placeholder"}:
            arg = bindings.get(str(arg.value), arg)
        bindings[f"${name}"] = arg
        if arg.kind != "reg":
            bindings[name] = arg
    code: list[Instr] = []
    for item in items:
        args = [_bind_operand(arg, bindings, labels) for arg in item.args]
        bound = replace(item, args=args)
        diagnostics = get_instruction_spec(item.op).validate(bound)
        if diagnostics:
            raise ValueError(diagnostics[0].message)
        ranges = core_alu.operand_ranges(item.op)
        if item.op not in PACKED_LINQ_OPERATIONS or len(args) != 2:
            ranges.extend(DEVICE_RANGES.get(item.op, ()))
        if item.op == "sw_req":
            ranges.append((0, "software_request", -_SIGN_BIT, _SIGN_BIT - 1))
        elif item.op == "fb_pop_data":
            ranges.append((0, "feedback_pop_tag", 0, 65535))
        for index, role, low, high in ranges:
            arg = args[index]
            if arg.kind == "imm" and not low <= int(arg.value) <= high:
                raise ValueError(f"{item.op} {role} outside {low}..{high}: {arg.raw}")
        if item.op in {"jlt", "jge"} and len(args) == 3:
            compare = args[1]
            if compare.kind == "imm" and not -_SIGN_BIT <= int(compare.value) <= _WORD_MASK:
                raise ValueError(f"{item.op} comparison outside 32-bit immediate range: {compare.raw}")
        if item.op in BRANCH_OPS:
            target = args[-1]
            if target.raw.startswith("@") and int(target.value) >= len(items):
                raise ValueError(f"Branch label {target.raw} has no instruction")
        code.append(bound)
    return tuple(code)


def _read(arg: Arg, state: CoreState, *, allow_uninitialized: bool = False) -> int | None:
    if arg.kind == "imm":
        return int(arg.value)
    if arg.kind != "reg":
        if allow_uninitialized:
            return None
        raise ValueError(f"Unresolved operand {arg.raw}")
    index = _register_index(arg)
    if any(write.index == index for write in state.pending_writes):
        raise ValueError(f"Register {arg.raw} is not ready")
    value = state.registers[index]
    if value is None and not allow_uninitialized:
        raise ValueError(f"Register {arg.raw} is uninitialized")
    return value


def step(
    state: CoreState, code: tuple[Instr, ...], *, instruction: Instr | None = None, external: ExternalInputs | None = None,
) -> Transition:
    """Issue one instruction from prepared code, or raise ValueError before issue."""
    if state.halted:
        raise ValueError("Core has already stopped")
    if not 0 <= state.pc < len(code):
        raise ValueError(f"No instruction at compiled PC {state.pc}")
    item = code[state.pc] if instruction is None else instruction
    if external is not None:
        external.check_fifo(state.devices.feedback_cursor, state.q1_ns)

    args = item.args
    destinations = destination_indexes(item.op)
    inputs = tuple(_read(arg, state, allow_uninitialized=item.op == "set_cond" and i in (1, 2))
                   for i, arg in enumerate(args) if i not in destinations)
    if item.op in {"mulu32", "muls32"} and args[2].value == args[3].value:
        raise ValueError("Full-product multiply requires distinct destination registers")
    if item.op == "loop" and args[1].kind == "reg" and args[0].value == args[1].value:
        raise ValueError("loop target register is not ready after counter decrement")
    if item.op == "stop" and args and args[0].kind == "imm" and not -_SIGN_BIT <= int(args[0].value) < _SIGN_BIT:
        raise ValueError(f"stop code outside signed 32-bit range: {args[0].value}")
    result = issue(item, inputs, state.flags.as_dict() if state.flags else None)
    devices = state.devices
    if item.op in {"fb_pop_data", "fb_pull_data"}:
        selector = cast(int, inputs[0]) if item.op == "fb_pop_data" else None
        devices, available_ns, words = devices.receive(external or ExternalInputs(), selector, state.q1_ns)
        end_ns = feedback_q1_completion(state.q1_ns + result.q1_duration, available_ns)
        result = replace(result, words=words, q1_duration=end_ns - state.q1_ns)
    target = inputs[-1] if result.taken is not None else None
    if target is not None and not args[-1].raw.startswith("@") and not 0 <= target <= 16383:
        raise ValueError(f"Branch target outside address range: {target}")
    next_pc = next_compiled_pc(state.pc, item, taken=result.taken, target=target,
                               fault=result.fault is not None, code_size=len(code))

    registers = list(state.registers)
    for write in state.pending_writes:
        registers[write.index] = write.value
    if not core_alu.register_write_delay_slots(item.op):
        registers[_register_index(args[0])] = result.words[0]
        pending = ()
    else:
        pending = tuple(RegisterWrite(_register_index(args[i]), word)
                        for i, word in zip(destinations, result.words, strict=True))

    latches = state.latches
    if result.latch_write is not None:
        latches = latches.write(result.latch_write)
    q1_end = state.q1_ns + result.q1_duration
    rt_fifo_starts = state.rt_fifo_starts
    reference_sync_ns = state.reference_sync_ns
    if result.wait_duration is not None or item.op == "stop":
        if len(rt_fifo_starts) == RT_FIFO_CAPACITY:
            q1_end = queued_q1_completion(q1_end, rt_fifo_starts[0])
            rt_fifo_starts = rt_fifo_starts[1:]
        if result.wait_duration is not None:
            rt_fifo_starts += (state.rt_ns + reference_sync_ns,)
    if external is not None:
        external.check_fifo(devices.feedback_cursor, q1_end)
    rt_ns = state.rt_ns
    packet = None
    if result.wait_duration is not None:
        values = cast(tuple[int, ...], inputs)
        enabled, mask, operator, _ = state.condition
        # Count arrivals at this RT edge before changing the counting-enable latch.
        if external is not None or devices.latch_enabled or enabled:
            devices = devices.advance_counters(external or ExternalInputs(), rt_ns)
        taken = devices.condition_taken(cast(int, mask), cast(int, operator), external or ExternalInputs()) if enabled else True
        selected = core_rt.select_packet(item.op, result.wait_duration, state.condition, taken)
        assert selected is not None  # condition_taken raises CoreBlocked on missing receiver inputs.
        start = rt_ns
        if selected.wait == "wait_trigger":
            devices, start = devices.await_trigger(external or ExternalInputs(), values[0], rt_ns)
        effects = core_rt.execute_packet(item.op, values, selected, latches, devices, time_ns=rt_ns)
        if effects.limitation:
            raise CoreBlocked(effects.limitation)
        devices, latches = effects.devices, effects.latches
        packet = RtPacket(state.q1_ns, q1_end, rt_ns, start + selected.dwell_ns, item.op, values,
                          effects.applied, selected.skipped, effects.op, effects.operands)
        if item.op == "wait_sync":
            reference_sync_ns += reference_sync_adjustment(result.wait_duration, selected.dwell_ns)
        rt_ns = selected.next_dequeue(start)
    next_state = replace(state,
        pc=next_pc, registers=tuple(registers), pending_writes=pending,
        flags=result.flags or state.flags, q1_ns=q1_end, rt_ns=rt_ns, stop_code=result.stop_code,
        fault=result.fault, latches=latches, condition=result.condition or state.condition, devices=devices,
        rt_fifo_starts=rt_fifo_starts, reference_sync_ns=reference_sync_ns,
    )
    return Transition(next_state, packet, result, state.pending_writes)
