"""Compare the abstract ALU's claims with concrete issues of the same instruction."""

from dataclasses import dataclass
from itertools import product
from random import Random

import pytest

from q1timeline.analysis.interpreter import AnalysisState, _execute_instruction, _flush_pending_register_writes
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm import core, core_alu
from q1timeline.q1asm.parser import parse_q1asm


MASK = 0xFFFFFFFF
SHIFTS = {"asl", "asr", "lsl", "lsr"}


@dataclass(frozen=True)
class Domain:
    bounds: tuple[int, int]
    bits: tuple[int, int]  # Bits known to be one and zero.
    words: tuple[int, ...]


def interval(low, high, *, ones=0, zeros=0):
    words = tuple(x for x in range(low, high + 1) if x & ones == ones and x & zeros == 0)
    assert len(words) > 1
    return Domain((low, high), (ones, zeros), words)


def bit_cube(base, free_bits):
    words = [base]
    for bit in free_bits:
        assert base & (1 << bit) == 0
        words += [word | (1 << bit) for word in words]
    return Domain((0, MASK), (base, MASK ^ max(words)), tuple(words))


def sampled_interval(low, high):
    rng = Random(0x51A1)
    interior = (rng.randint(low + 1, high - 1) for _ in range(12))
    words = (low, *interior, high)
    return Domain((low, high), (0, 0), words)


def generated_word_domain(rng):
    free_bits = sorted(rng.sample(range(32), rng.randint(3, 32)))
    fixed_mask = MASK ^ sum(1 << bit for bit in free_bits)
    ones = rng.getrandbits(32) & fixed_mask

    # Inserting the free bits of an index from least to most significant
    # enumerates exactly the words satisfying the mask, in numeric order.
    def word(index):
        return ones | sum(((index >> pos) & 1) << bit for pos, bit in enumerate(free_bits))

    size = 1 << len(free_bits)
    span = rng.randint(3, (1 << rng.randint(2, len(free_bits))) - 1)
    first = rng.randrange(size - span)
    last = first + span
    lower = rng.randint(word(first - 1) + 1 if first else 0, word(first))
    upper = rng.randint(word(last), word(last + 1) - 1 if last + 1 < size else MASK)
    indices = {first, last, *rng.sample(range(first + 1, last), min(4, span - 1))}
    if free_bits[-1] == 31:
        indices.update(i for i in (size // 2 - 1, size // 2) if first <= i <= last)
    return Domain((lower, upper), (ones, fixed_mask ^ ones), tuple(word(i) for i in sorted(indices)))


def generated_shift_counts(rng):
    low, high = sorted(rng.sample(range(33), 2))
    a, b = rng.sample(range(low, high + 1), 2)
    fixed = rng.getrandbits(6) & ~(a ^ b)
    return interval(low, high, ones=a & fixed, zeros=~a & fixed)


# Exhaust small domains across width/sign boundaries and sparse bit patterns.
# Sample endpoints and reproducible interior words for one wide interval.
WORDS = (
    interval(0, 4), interval(0x7FFD, 0x8003), interval(0xFFFD, 0x10003),
    interval(0x7FFFFFFC, 0x80000003), interval(MASK - 4, MASK),
    interval(16, 47, ones=4, zeros=3), bit_cube(0x04000008, (1, 8, 15, 31)),
    sampled_interval(0x30000001, 0xDFFFFFFE),
)
COUNTS = (interval(0, 3), interval(28, 32), interval(0, 32, zeros=7))


def abstract_state(*domains):
    state = AnalysisState(sequencer_id="test", alu_flags=dict(zf=True, nf=False, cf=True, of=False))
    for index, domain in enumerate(domains):
        reg = f"R{index}"
        state.registers[reg] = Unknown(reg)
        state.register_bounds[reg] = domain.bounds
        state.register_known_bits[reg] = domain.bits
    return state


def instruction(op, *inputs):
    destinations = [] if op in {"cmp", "test"} else ["R2"]
    if op in {"mulu32", "muls32"}:
        destinations.append("R3")
    return parse_q1asm(f"{op} {','.join([*inputs, *destinations])}").instructions[0]


def check_transfer(item, state, concrete_inputs, *, seed=None):
    before_flags = state.alu_flags.copy() if state.alu_flags is not None else None
    _execute_instruction(state, item, {}, {}, {})
    assert not state.diagnostics
    writes = state.pending_register_writes
    assert [w.register for w in writes] == [item.args[i].value for i in core.destination_indexes(item.op)]
    for inputs in concrete_inputs:
        result = core.issue(item, inputs)
        context = (item.source.raw, inputs, seed, state.register_bounds, state.register_known_bits)
        assert len(writes) == len(result.words), context
        for write, word in zip(writes, result.words, strict=True):
            if isinstance(write.value, Concrete):
                assert write.value.value == word, context
            if write.unsigned_bounds is not None:
                low, high = write.unsigned_bounds
                assert low <= word <= high, (context, write)
            if write.known_bits is not None:
                ones, zeros = write.known_bits
                assert word & ones == ones and word & zeros == 0, (context, write)
        if result.flags is None:
            assert state.alu_flags == before_flags, context
        else:
            flags = result.flags.as_dict()
            for flag, predicted in (state.alu_flags or {}).items():
                assert flags[flag] == predicted, (context, flag)


@pytest.mark.parametrize("op", sorted(core_alu.ALU_OPS | {"move"}))
def test_abstract_alu_contains_all_concrete_issues(op):
    if op in {"not", "move"}:
        item = instruction(op, "R0")
        for left in WORDS:
            check_transfer(item, abstract_state(left), product(left.words))
    else:
        item = instruction(op, "R0", "R1")
        for left, right in product(WORDS, COUNTS if op in SHIFTS else WORDS):
            check_transfer(item, abstract_state(left, right), product(left.words, right.words))


@pytest.mark.parametrize("op", sorted(core_alu.ALU_OPS | {"move"}))
def test_generated_intervals_and_known_bits_contain_concrete_results(op):
    unary = op in {"not", "move"}
    item = instruction(op, *(("R0",) if unary else ("R0", "R1")))
    for case in range(256):
        seed = f"q1-abstract-alu/{op}/{case}"
        rng = Random(seed)
        domains = [generated_word_domain(rng)]
        if not unary:
            domains.append(generated_shift_counts(rng) if op in SHIFTS else generated_word_domain(rng))
        for domain in domains:
            low, high = domain.bounds
            ones, zeros = domain.bits
            assert len(set(domain.words)) >= 2, seed
            assert all(low <= x <= high and x & ones == ones and x & zeros == 0 for x in domain.words), seed
        check_transfer(item, abstract_state(*domains), product(*(d.words for d in domains)), seed=seed)


@pytest.mark.parametrize("op", sorted(core_alu.ALU_OPS - {"not"}))
@pytest.mark.parametrize("prefix", ["move R0,R1", "not R0,R1"])
def test_abstract_alu_with_copied_or_complementary_operands(op, prefix):
    # Use the real prefix to establish the relation. For shifts the second
    # operand must stay within 0..32 even when the first is its complement.
    complement = prefix.startswith("not")
    domains = WORDS
    if op in SHIFTS:
        domains = (interval(MASK - 32, MASK) if complement else interval(0, 32),)
    item = instruction(op, "R0", "R1")
    for left in domains:
        state = abstract_state(left)
        _execute_instruction(state, parse_q1asm(prefix).instructions[0], {}, {}, {})
        _flush_pending_register_writes(state)
        inputs = ((x, x ^ MASK if complement else x) for x in left.words)
        check_transfer(item, state, inputs)


@pytest.mark.parametrize("op", sorted(SHIFTS | {"sub", "cmp"}))
@pytest.mark.parametrize("immediate_first", [False, True])
def test_abstract_noncommutative_immediate_register_forms(op, immediate_first):
    args = ("1", "R0") if immediate_first else ("R0", "1")
    item = instruction(op, *args)
    for left in WORDS:
        inputs = ((1, x) if immediate_first else (x, 1) for x in left.words)
        check_transfer(item, abstract_state(left), inputs)


def test_partial_add_checks_informative_facts(monkeypatch):
    left = interval(16, 47, ones=4, zeros=3)
    right = interval(0, 4, zeros=1)
    state = abstract_state(left, right)
    item = instruction("add", "R0", "R1")

    def unexpected_issue(*args, **kwargs):
        pytest.fail("unknown register operands reached the concrete ALU")

    monkeypatch.setattr(core, "issue", unexpected_issue)
    _execute_instruction(state, item, {}, {}, {})

    write, = state.pending_register_writes
    assert not isinstance(write.value, Concrete)
    assert write.unsigned_bounds is not None and write.unsigned_bounds != (0, MASK)
    assert write.known_bits is not None and write.known_bits[1] & 1
    assert state.alu_flags is not None and state.alu_flags["cf"] is False
