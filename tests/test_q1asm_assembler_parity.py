import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import RuntimeDependent
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.q1asm.parser import parse_q1asm


def _state(source: str, *, strict_q1asm: bool = False):
    return interpret_program(
        parse_q1asm(source, file="parity.q1asm"),
        sequencer_id="seq0",
        strict_q1asm=strict_q1asm,
    )


def _error_categories(source: str, *, strict_q1asm: bool = False) -> list[str]:
    return [
        diagnostic.category
        for diagnostic in _state(source, strict_q1asm=strict_q1asm).diagnostics
        if diagnostic.severity == "error"
    ]


def _signature_summary(op: str) -> set[tuple[tuple[str, ...], int]]:
    spec = get_instruction_spec(op)
    return {
        (tuple("/".join(sorted(arg_types)) for arg_types in signature.args), signature.q1_time_ns)
        for signature in spec.signatures
    }


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("stop", {((), 4), (("I",), 4), (("R",), 4)}),
        ("add", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("sub", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("and", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("or", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("xor", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("asl", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("asr", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("lsl", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("lsr", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("cmp", {(("I", "R"), 12), (("R", "I"), 12), (("R", "R"), 12)}),
        ("test", {(("I", "R"), 12), (("R", "I"), 12), (("R", "R"), 12)}),
        ("mulu16", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("muls16", {(("I", "R", "R"), 12), (("R", "I", "R"), 12), (("R", "R", "R"), 12)}),
        ("mulu32l", {(("I", "R", "R"), 20), (("R", "I", "R"), 20), (("R", "R", "R"), 20)}),
        ("mulu32h", {(("I", "R", "R"), 20), (("R", "I", "R"), 20), (("R", "R", "R"), 20)}),
        ("muls32", {(("I", "R", "R", "R"), 24), (("R", "I", "R", "R"), 24), (("R", "R", "R", "R"), 24)}),
        ("muls32l", {(("I", "R", "R"), 20), (("R", "I", "R"), 20), (("R", "R", "R"), 20)}),
        ("muls32h", {(("I", "R", "R"), 20), (("R", "I", "R"), 20), (("R", "R", "R"), 20)}),
        ("jge", {(("I/L",), 24), (("R",), 24), (("R", "I", "I/L"), 24), (("R", "I", "R"), 24)}),
        ("jlt", {(("R", "I", "I/L"), 24), (("R", "I", "R"), 24)}),
        ("loop", {(("R", "I/L"), 24), (("R", "R"), 24)}),
        ("set_awg_gain", {(("I", "I"), 4), (("R", "R"), 4)}),
        ("set_awg_offs", {(("I", "I"), 4), (("R", "R"), 4)}),
        ("set_cond", {(("I", "I", "I", "I"), 4), (("R", "R", "R", "I"), 4)}),
        ("set_digital", {(("I", "I", "I"), 4), (("R", "I", "R"), 4)}),
        ("reset_netzero", {((), 4)}),
        ("play", {(("I", "I", "I"), 4), (("R", "R", "I"), 4)}),
        ("acquire_weighted", {(("I", "I", "I", "I", "I"), 4), (("I", "R", "R", "R", "I"), 4)}),
        ("acquire_timetags", {(("I", "I", "I", "I", "I"), 8), (("I", "R", "I", "R", "I"), 8)}),
        ("fb_acq_iq_shift", {(("I", "I"), 4), (("R", "I"), 4)}),
        ("fb_acq_tb_cfg", {(("I", "I", "I", "I"), 4), (("R", "I"), 4)}),
        ("fb_acq_tb_extra", {(("I", "I", "I"), 4), (("R", "I"), 4)}),
        ("fb_acq_tb_mock", {(("I", "I", "I", "I"), 4), (("R", "I"), 4)}),
        ("fb_com_cfg", {(("I", "I", "I", "I"), 4), (("R", "I"), 4)}),
        ("fb_com_extra", {(("I", "I", "I"), 4), (("R", "I"), 4)}),
        ("fb_pull_data", {(("R", "R"), 8)}),
        ("fb_cmd", {(("I", "I", "I"), 4), (("I", "R", "I"), 4)}),
    ],
)
def test_current_docs_signatures_and_q1_runtimes_are_encoded(
    op: str, expected: set[tuple[tuple[str, ...], int]]
) -> None:
    assert _signature_summary(op) == expected


def test_parser_accepts_assembler_comment_and_trailing_comma_leniency() -> None:
    program = parse_q1asm(
        "// comment\n"
        "  // indented comment\n"
        "wait 4,,,\n"
        "play 0,1,4,\n"
        "move 1,R0,\n"
        "target: stop\n",
        file="parser-leniency.q1asm",
    )

    assert [instruction.op for instruction in program.instructions] == ["wait", "play", "move", "stop"]
    assert [[arg.raw for arg in instruction.args] for instruction in program.instructions] == [
        ["4"],
        ["0", "1", "4"],
        ["1", "R0"],
        [],
    ]
    assert program.diagnostics == []


@pytest.mark.parametrize(
    ("source", "expected_ops"),
    [
        ("/* q1asm_windows treats the whole line as a comment */ stop\nwait 4\n", ["wait"]),
        ("target: /* q1asm_windows keeps the label but drops the body */ wait 4\nstop\n", ["stop"]),
    ],
)
def test_parser_rejects_line_start_block_comment_before_instruction(
    source: str, expected_ops: list[str]
) -> None:
    program = parse_q1asm(source, file="block-comment.q1asm")

    assert [diagnostic.category for diagnostic in program.diagnostics] == ["syntax_error"]
    assert [instruction.op for instruction in program.instructions] == expected_ops


@pytest.mark.parametrize(
    "source",
    [
        "target: /* invalid body */ wait 4\nstop\n",
        "target: stop; stop\n",
    ],
)
def test_parser_does_not_bind_label_when_same_line_body_is_rejected(source: str) -> None:
    program = parse_q1asm(source, file="malformed-label-body.q1asm")

    assert [diagnostic.category for diagnostic in program.diagnostics] == ["syntax_error"]
    assert program.labels == {}


@pytest.mark.parametrize(
    "source",
    [
        "stop; stop\n",
        "nop; stop\n",
        "target: stop; stop\n",
    ],
)
def test_parser_rejects_semicolon_suffix_after_zero_argument_instruction(source: str) -> None:
    program = parse_q1asm(source, file="semicolon-suffix.q1asm")

    assert [diagnostic.category for diagnostic in program.diagnostics] == ["syntax_error"]
    assert program.instructions == []


def test_parser_rejects_at_prefixed_label_definition() -> None:
    program = parse_q1asm("@target: wait 4\nstop\n", file="at-label-definition.q1asm")

    assert [instruction.op for instruction in program.instructions] == ["stop"]
    assert program.labels == {}
    assert [diagnostic.category for diagnostic in program.diagnostics] == ["syntax_error"]


def test_parser_accepts_utf8_bom_before_first_instruction() -> None:
    program = parse_q1asm("\ufeffwait_sync 4\nstop\n", file="bom.q1asm")

    assert [instruction.op for instruction in program.instructions] == ["wait_sync", "stop"]
    assert program.instructions[0].source.line == 1
    assert program.instructions[0].source.column == 1
    assert program.instructions[0].source.raw == "wait_sync 4"


@pytest.mark.parametrize(
    "op",
    ["jz", "jnz", "jo", "jno", "js", "jns", "jg", "jl", "jle", "ja", "jae", "jb", "jbe"],
)
def test_assembler_supported_status_branches_are_not_unknown(op: str) -> None:
    state = _state(f"cmp R0,0\n{op} @target\ntarget: stop\n")

    assert "unknown_instruction" not in {diagnostic.category for diagnostic in state.diagnostics}
    assert any(event.kind == "q1_issue" and event.meta["op"] == op for event in state.events)


def test_default_unresolved_forward_branch_assumes_taken_path_to_continue_timeline() -> None:
    state = _state(
        "jge R0,1,@taken\n"
        "wait 4\n"
        "stop\n"
        "taken: wait 8\n"
        "stop\n"
    )

    waits = [event for event in state.events if event.kind == "wait"]
    branch_events = [event for event in state.events if event.kind == "branch_region"]

    assert [event.source.line for event in waits] == [4]
    assert str(waits[0].duration) == "8"
    assert not any(event.kind == "unknown_region" and event.meta.get("reason") == "unresolved_branch" for event in state.events)
    assert branch_events[0].meta["assumed_branch_path"] == "taken"
    assert any(
        diagnostic.category == "unresolved_branch"
        and diagnostic.severity == "info"
        and diagnostic.details.get("assumed_branch_path") == "taken"
        for diagnostic in state.diagnostics
    )


def test_default_unresolved_status_branch_assumes_taken_path_to_continue_timeline() -> None:
    state = _state(
        "cmp R0,0\n"
        "jz @taken\n"
        "wait 4\n"
        "stop\n"
        "taken: wait 8\n"
        "stop\n"
    )

    waits = [event for event in state.events if event.kind == "wait"]
    branch_events = [event for event in state.events if event.kind == "branch_region"]

    assert [event.source.line for event in waits] == [5]
    assert str(waits[0].duration) == "8"
    assert not any(event.kind == "unknown_region" and event.meta.get("reason") == "status_branch" for event in state.events)
    assert branch_events[0].meta["assumed_branch_path"] == "taken"


def test_taken_backward_status_branch_previews_next_wait_loop_iteration() -> None:
    source = (
        "wait_loop:\n"
        "    wait 65535\n"
        "    sub R31,1,R31\n"
        "    jnz @wait_loop\n"
        "    stop\n"
    )
    branch_id = "seq0:branch:wait_loop.q1asm:4:jnz:wait_loop"
    state = interpret_program(
        parse_q1asm(source, file="wait_loop.q1asm"),
        sequencer_id="seq0",
        branch_assumptions={branch_id: "taken"},
    )

    waits = [event for event in state.events if event.kind == "wait"]

    assert [(event.source.line, str(event.t0), str(event.t1)) for event in waits] == [
        (2, "0", "65535"),
        (2, "65535", "131070"),
    ]
    assert any(event.kind == "loop_block" for event in state.events)
    assert not any(event.meta.get("reason") == "unsupported_backward_conditional_branch" for event in state.events)
    assert not any(diagnostic.details.get("reason") == "unsupported_backward_conditional_branch" for diagnostic in state.diagnostics)


def test_jmp_register_with_concrete_label_target_continues_flow() -> None:
    state = _state(
        "move @target,R27\n"
        "nop\n"
        "nop\n"
        "jmp R27\n"
        "stop\n"
        "target: wait 12\n"
        "stop\n"
    )

    q1_ops = [event.meta.get("op") for event in state.events if event.kind == "q1_issue"]
    jmp_issue = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jmp")

    assert q1_ops == ["move", "nop", "nop", "jmp", "wait", "stop"]
    assert jmp_issue.meta["target_pc"] == 5
    assert not any(
        event.kind == "unknown_region" and event.meta.get("reason") == "register_branch_target"
        for event in state.events
    )


def test_jmp_register_return_to_unvisited_backward_target_continues_flow() -> None:
    state = _state(
        "move @after_call,R27\n"
        "jmp @subroutine\n"
        "after_call: wait 12\n"
        "stop\n"
        "subroutine: wait 4\n"
        "jmp R27\n"
    )

    q1_ops = [event.meta.get("op") for event in state.events if event.kind == "q1_issue"]
    return_issue = next(
        event
        for event in state.events
        if event.kind == "q1_issue" and event.source.line == 6 and event.meta.get("op") == "jmp"
    )

    assert q1_ops == ["move", "jmp", "wait", "jmp", "wait", "stop"]
    assert return_issue.meta["target_pc"] == 2
    assert not any(
        event.kind == "unknown_region" and event.meta.get("reason") == "unsupported_backward_numeric_branch"
        for event in state.events
    )


def test_jmp_register_subroutine_can_return_to_multiple_call_sites() -> None:
    state = _state(
        "move @first_return,R27\n"
        "jmp @subroutine\n"
        "first_return: move @second_return,R27\n"
        "jmp @subroutine\n"
        "second_return: wait 12\n"
        "stop\n"
        "subroutine: wait 4\n"
        "jmp R27\n"
    )

    q1_ops = [event.meta.get("op") for event in state.events if event.kind == "q1_issue"]
    return_targets = [
        event.meta.get("target_pc")
        for event in state.events
        if event.kind == "q1_issue" and event.source.line == 8 and event.meta.get("op") == "jmp"
    ]

    assert q1_ops == ["move", "jmp", "wait", "jmp", "move", "jmp", "wait", "jmp", "wait", "stop"]
    assert return_targets == [2, 4]
    assert not any(
        event.kind == "unknown_region" and event.meta.get("reason") == "unsupported_backward_numeric_branch"
        for event in state.events
    )


def test_jmp_register_loop_to_visited_target_stays_collapsed() -> None:
    state = _state(
        "move @loop_body,R27\n"
        "loop_body: wait 4\n"
        "jmp R27\n"
        "stop\n"
    )

    q1_ops = [event.meta.get("op") for event in state.events if event.kind == "q1_issue"]
    loop_jump = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jmp")

    assert q1_ops == ["move", "wait", "jmp"]
    assert loop_jump.meta["target_pc"] == 1
    assert any(
        event.kind == "unknown_region" and event.meta.get("reason") == "unsupported_backward_numeric_branch"
        for event in state.events
    )


@pytest.mark.parametrize(
    "source",
    [
        "mulu16 R0,1,R1\nstop\n",
        "muls16 R0,R1,R2\nstop\n",
        "mulu32 R0,R1,R2,R3\nstop\n",
        "mulu32l R0,1,R1\nstop\n",
        "mulu32h R0,R1,R2\nstop\n",
        "muls32 R0,R1,R2,R3\nstop\n",
        "muls32l R0,1,R1\nstop\n",
        "muls32h R0,R1,R2\nstop\n",
        "test R0,1\nstop\n",
        "lsl R0,1,R1\nstop\n",
        "lsr R0,R1,R2\nstop\n",
        "reset_netzero\nstop\n",
        "sw_req 1\nstop\n",
    ],
)
def test_assembler_supported_classical_and_control_opcodes_validate(source: str) -> None:
    assert "unknown_instruction" not in set(_error_categories(source))
    assert "invalid_argument_type" not in set(_error_categories(source))


@pytest.mark.parametrize(
    "source",
    [
        "acquire_weighted 0,0,1,2,4\nstop\n",
        "play_pulse R0,4\nstop\n",
        "fb_cmd 0,1,4\nstop\n",
    ],
)
def test_assembler_supported_rt_aliases_validate(source: str) -> None:
    assert _error_categories(source) == []


@pytest.mark.parametrize("op", ["add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"])
def test_classical_alu_accepts_assembler_immediate_first_form(op: str) -> None:
    assert _error_categories(f"{op} 1,R1,R2\nstop\n") == []


def test_upd_param_accepts_register_duration_source() -> None:
    assert get_instruction_spec("upd_param").validate(parse_q1asm("upd_param R0\n").instructions[0]) == []
    assert _error_categories("upd_param R0\nstop\n") == []


def test_set_digital_signature_matches_assembler() -> None:
    assert _error_categories("set_digital R0,1,R1\nstop\n") == []
    assert _error_categories("set_digital R0,R1,R2\nstop\n") == ["invalid_argument_type"]


@pytest.mark.parametrize(
    ("source", "op", "role", "value"),
    [
        ("play 1024,1,4\nstop\n", "play", "waveform_index", 1024),
        ("acquire 32,0,4\nstop\n", "acquire", "acquisition_index", 32),
        ("acquire 0,16777216,4\nstop\n", "acquire", "bin_index", 16777216),
        ("acquire_weighed 0,0,64,1,4\nstop\n", "acquire_weighed", "weight_index", 64),
        ("acquire_ttl 0,0,2,4\nstop\n", "acquire_ttl", "input_index", 2),
        ("acquire_timetags 0,0,0,2048,4\nstop\n", "acquire_timetags", "tag_index", 2048),
        ("set_ph_delta 1000000001\nstop\n", "set_ph_delta", "phase_delta", 1000000001),
        ("set_scope_en 2\nstop\n", "set_scope_en", "scope_enable", 2),
        ("set_latch_en 2,4\nstop\n", "set_latch_en", "enable_flag", 2),
        ("wait_trigger 16,4\nstop\n", "wait_trigger", "trigger_index", 16),
        ("upd_thres 4,0,4\nstop\n", "upd_thres", "threshold_index", 4),
        ("upd_thres 0,-1,4\nstop\n", "upd_thres", "threshold_value", -1),
        ("fb_pop_data 256,R0\nstop\n", "fb_pop_data", "feedback_pop_tag", 256),
        ("fb_com_data 256,0,4\nstop\n", "fb_com_data", "feedback_channel", 256),
        ("fb_cmd 256,0,4\nstop\n", "fb_cmd", "feedback_channel", 256),
        ("fb_com_cfg 2,0,1,4\nstop\n", "fb_com_cfg", "write_combine_flag", 2),
        ("fb_com_extra 0,65536,4\nstop\n", "fb_com_extra", "extra_payload_bytes", 65536),
        ("fb_acq_iq_id 256,4\nstop\n", "fb_acq_iq_id", "feedback_acquisition_channel", 256),
        ("fb_llp_tags_id 256,4\nstop\n", "fb_llp_tags_id", "feedback_channel", 256),
        ("fb_tdc_tdelta_id 256,4\nstop\n", "fb_tdc_tdelta_id", "feedback_channel", 256),
        ("fb_acq_iq_shift 64,4\nstop\n", "fb_acq_iq_shift", "shift_count", 64),
        ("fb_acq_tb_mock 1,1,2,4\nstop\n", "fb_acq_tb_mock", "mock_data", 2),
    ],
)
def test_assembler_operand_ranges_are_enforced(source: str, op: str, role: str, value: int) -> None:
    state = _state(source)
    invalid = [
        diagnostic
        for diagnostic in state.diagnostics
        if diagnostic.category == "invalid_argument_value"
        and diagnostic.details.get("op") == op
        and diagnostic.details.get("operand_role") == role
        and diagnostic.details.get("value") == value
    ]

    assert len(invalid) == 1


def test_fb_com_data_accepts_full_feedback_id_range() -> None:
    assert _error_categories("fb_com_data 128,0,4\nstop\n") == []
    assert _error_categories("fb_com_data 255,0,4\nstop\n") == []
    assert _error_categories("fb_cmd 255,0,4\nstop\n") == []


def test_fb_cmd_unresolved_channel_reports_feedback_channel_role() -> None:
    state = _state("fb_cmd $CH,0,4\nstop\n")

    assert any(
        diagnostic.category == "unresolved_symbol"
        and diagnostic.details.get("op") == "fb_cmd"
        and diagnostic.details.get("operand_role") == "feedback_channel"
        for diagnostic in state.diagnostics
    )


@pytest.mark.parametrize(
    ("source", "op", "argument_index"),
    [
        ("stop 2147483648\n", "stop", 0),
        ("set_cond 2,0,0,4\nstop\n", "set_cond", 0),
        ("set_cond 1,32768,0,4\nstop\n", "set_cond", 1),
        ("set_cond 1,0,8,4\nstop\n", "set_cond", 2),
        ("upd_thres 4,0,4\nstop\n", "upd_thres", 0),
        ("cmp 4294967296,R0\nstop\n", "cmp", 0),
        ("jge R0,4294967296,0\nstop\n", "jge", 1),
        ("jge 16384\nstop\n", "jge", 0),
        ("asl 2147483648,R0,R1\nstop\n", "asl", 0),
        ("asr 2147483648,R0,R1\nstop\n", "asr", 0),
        ("asl R0,-1,R1\nstop\n", "asl", 1),
        ("asr R0,-1,R1\nstop\n", "asr", 1),
        ("lsl -1,R0,R1\nstop\n", "lsl", 0),
        ("lsr -1,R0,R1\nstop\n", "lsr", 0),
        ("lsl R0,-1,R1\nstop\n", "lsl", 1),
        ("lsr R0,-1,R1\nstop\n", "lsr", 1),
        ("mulu16 65536,R0,R1\nstop\n", "mulu16", 0),
        ("muls16 -32769,R0,R1\nstop\n", "muls16", 0),
        ("mulu32l 4294967296,R0,R1\nstop\n", "mulu32l", 0),
        ("muls32 2147483648,R0,R1,R2\nstop\n", "muls32", 0),
    ],
)
def test_docs_immediate_operand_ranges_are_checked(source: str, op: str, argument_index: int) -> None:
    state = _state(source)

    assert any(
        diagnostic.category == "invalid_argument_value"
        and diagnostic.details.get("op") == op
        and diagnostic.details.get("argument_index") == argument_index
        for diagnostic in state.diagnostics
    )


def test_current_docs_feedback_shift_32_is_valid() -> None:
    assert _error_categories("fb_acq_iq_shift 32,4\nstop\n") == []


@pytest.mark.parametrize("source", ["move --1,R0\nstop\n", "wait --1\nstop\n"])
def test_malformed_double_sign_integer_literals_are_rejected(source: str) -> None:
    assert "syntax_error" in set(_error_categories(source))


@pytest.mark.parametrize(
    "source",
    [
        "wait 010\nstop\n",
        "move 1,R010\nstop\n",
        ".DEF D 010\nwait $D\nstop\n",
    ],
)
def test_leading_zero_decimal_like_literals_are_rejected(source: str) -> None:
    assert "syntax_error" in set(_error_categories(source))


@pytest.mark.parametrize(
    "source",
    [
        "wait 0\nstop\n",
        "wait 10\nstop\n",
        "wait 0x10\nstop\n",
        "move 1,R0\nstop\n",
        "move 1,R10\nstop\n",
    ],
)
def test_documented_decimal_hex_and_register_literals_still_parse(source: str) -> None:
    assert _error_categories(source) == []


def test_branch_compare_accepts_q1asm_windows_negative_twos_complement_literal() -> None:
    assert _error_categories("jlt R0,-1,@done\nstop\ndone: stop\n") == []


@pytest.mark.parametrize("op", ["asl", "asr"])
def test_signed_immediate_first_shifts_accept_q1asm_windows_negative_literals(op: str) -> None:
    assert _error_categories(f"move 1,R0\nnop\n{op} -1,R0,R1\nstop\n") == []


@pytest.mark.parametrize("op", ["fb_com_cfg", "fb_acq_tb_cfg", "fb_com_extra", "fb_acq_tb_extra", "fb_acq_tb_mock"])
def test_register_packed_feedback_forms_use_second_operand_as_duration(op: str) -> None:
    state = _state(f"{op} R0,1000\nstop\n")
    categories = [diagnostic.category for diagnostic in state.diagnostics if diagnostic.severity == "error"]
    event = next(event for event in state.events if event.kind == op)

    assert categories == []
    assert str(event.duration) == "1000"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("move 5,R0\nnop\nsub 1,R0,R1\nnop\nstop\n", 4),
        ("move 8,R0\nnop\nasl 1,R0,R1\nnop\nstop\n", 16),
        ("move 8,R0\nnop\nasr 1,R0,R1\nnop\nstop\n", 4),
        ("move 8,R0\nnop\nlsl 1,R0,R1\nnop\nstop\n", 16),
        ("move 8,R0\nnop\nlsr 1,R0,R1\nnop\nstop\n", 4),
    ],
)
def test_immediate_first_non_commutative_classical_ops_follow_docs(source: str, expected: int) -> None:
    state = _state(source)

    assert state.registers["R1"].value == expected
    assert not any(diagnostic.severity == "error" for diagnostic in state.diagnostics)


@pytest.mark.parametrize(
    ("source", "expected_wait"),
    [
        ("move 5,R0\nnop\ncmp R0,3\nnop\njg @taken\nwait 4\nstop\ntaken: wait 8\nstop\n", "8"),
        ("move 5,R0\nnop\ncmp 3,R0\nnop\njg @taken\nwait 4\nstop\ntaken: wait 10\nstop\n", "10"),
        ("move 2,R0\nnop\ncmp R0,3\nnop\njb @taken\nwait 4\nstop\ntaken: wait 12\nstop\n", "12"),
        ("move 4,R0\nnop\ntest 4,R0\nnop\njnz @taken\nwait 4\nstop\ntaken: wait 16\nstop\n", "16"),
        ("move 3,R0\nnop\ncmp R0,3\nnop\njge @taken\nwait 4\nstop\ntaken: wait 20\nstop\n", "20"),
        ("move -2147483648,R0\nnop\nlsl R0,1,R1\nnop\njo @taken\nwait 4\nstop\ntaken: wait 24\nstop\n", "24"),
        ("move 1,R0\nnop\nlsr R0,1,R1\nnop\njb @taken\nwait 4\nstop\ntaken: wait 28\nstop\n", "28"),
        ("move 1,R0\nnop\ncmp R0,3\nnop\njg @taken\nwait 24\nstop\ntaken: wait 8\nstop\n", "24"),
    ],
)
def test_status_branches_use_concrete_cmp_test_and_shift_flags(source: str, expected_wait: str) -> None:
    state = _state(source)
    waits = [event for event in state.events if event.kind == "wait"]

    assert [str(event.duration) for event in waits] == [expected_wait]
    assert "unresolved_branch" not in {
        diagnostic.category for diagnostic in state.diagnostics if diagnostic.severity in {"warning", "error"}
    }


@pytest.mark.parametrize(
    ("source", "op", "expected_q1_duration"),
    [
        ("move 5,R0\nnop\ncmp R0,3\nnop\njg @taken\nwait 4\nstop\ntaken: wait 8\nstop\n", "jg", "16"),
        ("move 1,R0\nnop\ncmp R0,3\nnop\njg @taken\nwait 4\nstop\ntaken: wait 8\nstop\n", "jg", "4"),
        ("move 3,R0\nnop\ncmp R0,3\nnop\njge @taken\nwait 4\nstop\ntaken: wait 8\nstop\n", "jge", "24"),
        ("move 1,R0\nnop\ncmp R0,3\nnop\njge @taken\nwait 4\nstop\ntaken: wait 8\nstop\n", "jge", "4"),
    ],
)
def test_concrete_status_branch_q1_issue_duration_matches_jump_or_continue(
    source: str, op: str, expected_q1_duration: str
) -> None:
    state = _state(source)
    issue = next(event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == op)

    assert str(issue.duration) == expected_q1_duration


def test_single_iteration_loop_uses_continue_q1_runtime() -> None:
    state = _state("move 1,R0\nnop\nbody: nop\nloop R0,@body\nstop\n")
    loop_issue = next(event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "loop")

    assert str(loop_issue.duration) == "4"


def test_compact_loop_final_hidden_iteration_uses_continue_q1_runtime() -> None:
    state = _state("move 2,R0\nnop\nbody: nop\nloop R0,@body\nstop\n")
    stop_issue = next(event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "stop")
    loop_block = next(event for event in state.events if event.kind == "loop_block")

    assert str(stop_issue.t0) == "44"
    assert loop_block.meta["loop_q1_jump_runtime_ns"] == 24
    assert loop_block.meta["loop_q1_continue_runtime_ns"] == 4


def test_fb_cmd_accepts_register_payload_and_emits_feedback_event() -> None:
    state = _state("move 7,R0\nnop\nfb_cmd 3,R0,4\nstop\n")

    assert _error_categories("fb_cmd 3,R0,4\nstop\n") == []
    event = next(event for event in state.events if event.kind == "feedback_com")
    assert event.meta["feedback"]["channel"] == "3"
    assert event.meta["feedback"]["source"] == "R0"


def test_fb_pull_data_writes_fifo_id_and_payload_destinations() -> None:
    state = _state("fb_pull_data R4,R5\nstop\n")

    assert isinstance(state.registers["R4"], RuntimeDependent)
    assert isinstance(state.registers["R5"], RuntimeDependent)
    assert state.registers["R4"].source == "fb_pull_data fifo id"
    assert state.registers["R5"].source == "fb_pull_data fifo payload"

    event = next(event for event in state.events if event.kind == "feedback_pop")
    assert event.meta["feedback"] == {
        "direction": "receive",
        "receive_mode": "fifo",
        "id_target": "R4",
        "target": "R5",
    }


def test_fb_pull_data_q1_issue_duration_accounts_for_two_register_writes() -> None:
    state = _state("fb_pull_data R4,R5\nstop\n")

    issue = next(event for event in state.events if event.kind == "q1_issue" and event.label == "fb_pull_data")
    assert str(issue.duration) == "8"


def test_fb_pull_data_feedback_flow_uses_fifo_send_channel() -> None:
    ir = analyze_documents(
        {
            "producer.q1asm": "wait_sync 4\nwait 100\nfb_com_data 16,123,4\nstop\n",
            "consumer.q1asm": "wait_sync 4\nwait 1000\nfb_pull_data R4,R5\nstop\n",
        },
        alignment_mode="none",
    )

    assert ir["feedback_flows"] == [
        {
            "id": "feedback-flow-0",
            "from_event_id": "producer:e5",
            "to_event_id": "consumer:e5",
            "channel": "16",
            "source": "123",
            "target": "R5",
            "label": "feedback ch 16: 123 -> R5",
        }
    ]
    assert set(ir["feedback_balance"]["channels"]) == {"16"}
    assert ir["feedback_balance"]["channels"]["16"]["receives"] == 1
    assert ir["feedback_balance"]["channels"]["16"]["matched"] == 1


def test_thresholded_bit_acquisition_feedback_produces_one_fifo_payload() -> None:
    ir = analyze_documents(
        {
            "qrm.q1asm": (
                "wait_sync 4\n"
                "fb_acq_tb_id 4,4\n"
                "acquire 0,0,200\n"
                "fb_acq_tb_id 0,4\n"
                "wait 600\n"
                "fb_pull_data R4,R5\n"
                "stop\n"
            ),
        },
        alignment_mode="none",
    )

    channels = ir["feedback_balance"]["channels"]
    acquire = next(event for event in ir["events"] if event["kind"] == "acquire")

    assert acquire["meta"]["feedback"]["data_type"] == "thresholded_bits"
    assert [(flow["channel"], flow["source"], flow["target"]) for flow in ir["feedback_flows"]] == [
        ("4", "acq#0/bin0", "R5")
    ]
    assert channels["4"]["send_payloads"] == 1
    assert channels["4"]["matched"] == 1
    assert channels["4"]["status"] == "balanced"


def test_fb_pop_data_discards_fifo_entries_before_matching_id() -> None:
    ir = analyze_documents(
        {
            "producer.q1asm": (
                "wait_sync 4\n"
                "wait 100\n"
                "fb_com_data 16,111,4\n"
                "wait 100\n"
                "fb_com_data 17,222,4\n"
                "stop\n"
            ),
            "consumer.q1asm": (
                "wait_sync 4\n"
                "wait 1000\n"
                "fb_pop_data 17,R5\n"
                "wait 100\n"
                "fb_pull_data R6,R7\n"
                "stop\n"
            ),
        },
        alignment_mode="none",
    )

    assert [
        (flow["channel"], flow["source"], flow["target"])
        for flow in ir["feedback_flows"]
    ] == [("17", "222", "R5")]
    channels = ir["feedback_balance"]["channels"]
    assert channels["16"]["discarded_payloads"] == 1
    assert channels["16"]["unconsumed_payloads"] == 0
    assert channels["16"]["status"] == "balanced"
    assert not any(
        diagnostic["category"] == "feedback_fifo_imbalance"
        and diagnostic["details"]["channel"] == "16"
        for diagnostic in ir["diagnostics"]
    )
    assert channels["fifo"]["unmatched_receives"] == 1


def test_fb_pop_data_discard_does_not_consume_other_receivers_queues() -> None:
    ir = analyze_documents(
        {
            "producer_a.q1asm": "wait_sync 4\nwait 100\nfb_com_data 19,190,4\nstop\n",
            "producer_b.q1asm": "wait_sync 4\nwait 200\nfb_com_data 18,180,4\nstop\n",
            "consumer_a.q1asm": "wait_sync 4\nwait 1000\nfb_pop_data 18,R0\nstop\n",
            "consumer_b.q1asm": "wait_sync 4\nwait 1000\nfb_pop_data 19,R1\nstop\n",
        },
        alignment_mode="none",
    )

    assert sorted(
        (flow["channel"], flow["source"], flow["target"])
        for flow in ir["feedback_flows"]
    ) == [("18", "180", "R0"), ("19", "190", "R1")]
    assert ir["feedback_balance"]["channels"]["19"]["discarded_payloads"] == 0


def test_set_cond_remains_explicitly_analysis_incomplete() -> None:
    state = _state("set_cond 1,0,0,4\nplay 0,1,8\nstop\n")

    assert any(
        diagnostic.category == "analysis_incomplete"
        and diagnostic.details.get("op") == "set_cond"
        for diagnostic in state.diagnostics
    )


@pytest.mark.parametrize(
    "source",
    [
        "set_cond @target,4,4,4\ntarget: stop\n",
        "set_latch_en @target,4\ntarget: stop\n",
        "set_scope_en @target\ntarget: stop\n",
        "upd_thres @target,0,4\ntarget: stop\n",
        "acquire_ttl 0,0,@target,4\ntarget: stop\n",
        "acquire_timetags 0,0,@target,0,4\ntarget: stop\n",
        "fb_com_cfg @target,0,1,4\ntarget: stop\n",
        "fb_com_extra @target,1,4\ntarget: stop\n",
        "fb_acq_tb_valid @target,4\ntarget: stop\n",
        "fb_acq_tb_mock 0,@target,0,4\ntarget: stop\n",
    ],
)
def test_labels_are_allowed_as_assembler_immediates_in_non_branch_roles(source: str) -> None:
    assert "invalid_argument_type" not in set(_error_categories(source))


@pytest.mark.parametrize(
    "source",
    [
        "move N,R0\nstop\n",
        "wait N\nstop\n",
        "wait {N}\nstop\n",
        "play 0,1,N\nstop\n",
        "play W0,W1,4\nstop\n",
    ],
)
def test_strict_q1asm_mode_rejects_unresolved_symbolic_operands(source: str) -> None:
    assert "unresolved_symbol" in set(_error_categories(source, strict_q1asm=True))


def test_strict_q1asm_mode_rejects_placeholder_defs() -> None:
    assert "unresolved_symbol" in set(_error_categories(".DEF N {VALUE}\nmove $N,R0\nstop\n", strict_q1asm=True))


def test_default_template_mode_still_allows_symbolic_duration() -> None:
    state = _state("wait {N}\nstop\n")
    wait_event = next(event for event in state.events if event.kind == "wait")

    assert wait_event.confidence == "symbolic"
    assert not any(
        diagnostic.severity == "error" and diagnostic.category == "unresolved_symbol"
        for diagnostic in state.diagnostics
    )


def test_default_template_mode_accepts_python_f_string_expression_operands() -> None:
    state = _state(
        "move {int(4 * f_center)}, R1\n"
        "wait {int(ro_duration % 65535)}\n"
        "fb_acq_iq_id {linq_channel}, 12\n"
        "stop\n"
    )
    wait_event = next(event for event in state.events if event.kind == "wait")

    assert wait_event.confidence == "symbolic"
    assert not any(diagnostic.severity == "error" for diagnostic in state.diagnostics)
