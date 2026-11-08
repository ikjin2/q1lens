import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="value-identity.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("operation", ["xor R1,R7,R2", "sub R1,R7,R2", "xor R1,$ORIGINAL,R2", "sub $ORIGINAL,R1,R2"])
def test_copy_snapshot_compares_equal_to_its_unchanged_source(operation):
    state = analyze(f".DEF ORIGINAL R7\nmove R7,R1\nnop\n{operation}\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.alu_flags == {"zf": True, "nf": False, "cf": False, "of": False}


def test_copy_comparison_resolves_the_following_status_branch():
    state = analyze("move R7,R1\nnop\ncmp R1,R7\nnop\njz @same\n"
                    "wait 200\nstop\nsame: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation", [
    "move R7,R1", "add R7,0,R1", "add 0,R7,R1", "sub R7,0,R1",
    "and R7,4294967295,R1", "or R7,0,R1", "xor R7,0,R1",
    "lsl R7,0,R1", "lsr R7,0,R1", "asr R7,0,R1",
])
def test_exact_identity_operations_keep_the_source_snapshot(operation):
    state = analyze(f"{operation}\nnop\nxor R1,R7,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)


@pytest.mark.parametrize("operation,expected", [
    ("and R1,R7,R2", 0), ("and R7,R1,R2", 0),
    ("or R1,R7,R2", 0xFFFFFFFF), ("xor R1,R7,R2", 0xFFFFFFFF),
    ("add R1,R7,R2", 0xFFFFFFFF),
])
def test_not_snapshot_is_proven_complement_of_unchanged_original(operation, expected):
    state = analyze(f"not R7,R1\nnop\n{operation}\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(expected)
    assert state.alu_flags == {"zf": expected == 0, "nf": bool(expected & 0x80000000), "cf": False, "of": False}


@pytest.mark.parametrize("negation", ["not R7,R1", "xor R7,4294967295,R1", "xor -1,R7,R1"])
def test_bitwise_negations_toggle_the_source_snapshot(negation):
    state = analyze(f"{negation}\nnop\nnot R1,R2\nnop\nxor R2,R7,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


def test_complement_subtraction_keeps_its_proven_odd_result_bit():
    state = analyze("not R7,R1\nnop\nsub R7,R1,R2\nnop\nand R2,1,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(1)


@pytest.mark.parametrize("operation", ["and R1,R7,R2", "or R1,R7,R2"])
def test_equal_snapshot_logical_identities_keep_value_relation(operation):
    state = analyze(f"move R7,R1\nnop\n{operation}\nnop\nxor R2,R7,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("replacement", ["move R8,R7", "fb_pop_data 5,R7", "add R7,1,R7", "not R7,R7"])
def test_source_overwrite_does_not_reattach_an_old_copy(replacement):
    state = analyze(f"move R7,R1\nnop\n{replacement}\nnop\nxor R1,R7,R2\nnop\nstop\n")

    assert state.registers["R2"] != Concrete(0)


def test_pending_copy_snapshots_source_identity_before_a_later_write():
    state = analyze("move R7,R1\nmove R8,R7\nnop\nxor R1,R7,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_two_copies_remain_equal_after_the_original_changes():
    state = analyze("move R7,R1\nnop\nmove R1,R2\nmove R8,R7\nnop\nxor R1,R2,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


def test_distinct_feedback_reads_with_matching_descriptions_are_not_equal():
    state = analyze("fb_pop_data 5,R0\nfb_pop_data 5,R1\nnop\nxor R0,R1,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_hidden_loop_write_does_not_restore_the_initial_source_identity():
    source = ("move R7,R1\nmove 2,R0\nnop\nbody: add R7,1,R7\nnop\nwait 100\nloop R0,@body\n"
              "xor R1,R7,R2\nnop\njz @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")
    state = analyze(source)

    assert not isinstance(state.registers["R2"], Concrete)
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_direct_loop_counter_completion_does_not_reuse_an_old_unknown_origin():
    state = analyze("move R0,R1\nmove 2,R0\nnop\nbody: wait 100\nloop R0,@body\n"
                    "xor R1,R0,R2\nnop\nstop\n")

    assert state.registers["R0"] == Concrete(0)
    assert not isinstance(state.registers["R2"], Concrete)


def test_copy_identity_is_not_visible_before_the_copy_write_is_readable():
    state = analyze("move R7,R1\nxor R1,R7,R2\nnop\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("source", [
    ".DEF VALUE {VALUE}\nmove $VALUE,R1\nnop\nxor R1,$VALUE,R2\nnop\nstop\n",
    "move {VALUE},R1\nnop\nsub R1,{VALUE},R2\nnop\nstop\n",
    ".DEF VALUE {VALUE}\nnot $VALUE,R1\nnop\nnot R1,R1\nnop\nxor R1,$VALUE,R2\nnop\nstop\n",
])
def test_immutable_template_binding_can_be_related_to_its_copy(source):
    state = analyze(source)

    assert state.registers["R2"] == Concrete(0)


@pytest.mark.parametrize("operand", ["$VALUE", "{VALUE}"])
def test_synthetic_dollar_alias_uses_the_same_declared_parameter_binding(operand):
    state = analyze(f"move $VALUE,R1\nnop\nxor R1,{operand},R2\nnop\nstop\n", params={"VALUE": "unresolved_input"})

    assert state.registers["R2"] == Concrete(0)


def test_an_explicit_dollar_parameter_has_a_distinct_lookup_namespace():
    state = analyze("move $VALUE,R1\nnop\nxor R1,{VALUE},R2\nnop\nstop\n",
                    params={"VALUE": "unresolved_input", "$VALUE": "unresolved_input"})

    assert not isinstance(state.registers["R2"], Concrete)


def test_explicit_dollar_binding_is_still_equal_to_its_own_copy():
    state = analyze("move $VALUE,R1\nnop\nxor R1,$VALUE,R2\nnop\nstop\n",
                    params={"VALUE": "first_input", "$VALUE": "second_input"})

    assert state.registers["R2"] == Concrete(0)


def test_undeclared_dollar_and_placeholder_names_do_not_get_a_synthetic_alias():
    state = analyze("move $VALUE,R1\nnop\nxor R1,{VALUE},R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_distinct_parameter_bindings_do_not_become_equal_from_matching_symbolic_text():
    state = analyze("move $LEFT,R1\nnop\nxor R1,$RIGHT,R2\nnop\nstop\n",
                    params={"LEFT": "same_description", "RIGHT": "same_description"})

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("operand,definition", [("{R8}", ""), ("{SOURCE}", ".DEF SOURCE R8\n"), ("$SOURCE", ".DEF SOURCE R8\n")])
def test_hardware_register_templates_are_not_immutable_parameter_bindings(operand, definition):
    state = analyze(f"{definition}move {operand},R1\nnop\nmove R9,R8\nnop\nxor R1,{operand},R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("definitions,source", [
    (".DEF INPUT {X}", "{X}"),
    (".DEF INPUT {X}\n.DEF COPY {INPUT}", "$COPY"),
    (".DEF INPUT {X}\n.DEF COPY {INPUT}", "{X}"),
    (".DEF INPUT {X}\n.DEF COPY {INPUT}\n.DEF LAST {COPY}", "$LAST"),
    (".DEF INPUT {X}\n.DEF X {INPUT}", "{X}"),
])
def test_ordered_def_binding_links_keep_the_captured_immutable_value(definitions, source):
    state = analyze(f"{definitions}\nmove $INPUT,R1\nnop\nxor R1,{source},R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)


def test_a_later_definition_does_not_rebind_an_earlier_captured_reference():
    state = analyze(".DEF INPUT {X}\n.DEF X 1\nmove $INPUT,R1\nnop\nxor R1,{X},R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("reference,comparison,expected_zero", [
    ("{X}", "{X}", True), ("{$X}", "$X", True),
    ("{X}", "$X", False), ("{$X}", "{X}", False),
])
def test_def_capture_uses_the_exact_parameter_lookup_namespace(reference, comparison, expected_zero):
    state = analyze(f".DEF INPUT {reference}\nmove $INPUT,R1\nnop\nxor R1,{comparison},R2\nnop\nstop\n",
                    params={"X": "same_description", "$X": "same_description"})

    assert (state.registers["R2"] == Concrete(0)) is expected_zero


def test_a_def_link_through_a_hardware_reference_does_not_follow_live_register_writes():
    state = analyze(".DEF INPUT {R7}\nmove $INPUT,R1\nnop\nmove R8,R7\nnop\nxor R1,{R7},R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("operand,definition", [
    ("{R7}", ""), ("{$R7}", ""), ("{SOURCE}", ".DEF SOURCE R7\n"),
])
def test_exact_physical_read_names_share_the_register_snapshot(operand, definition):
    state = analyze(f"{definition}move {operand},R1\nnop\nxor R1,R7,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)


def test_raw_dollar_register_without_a_declared_alias_keeps_its_symbol_error():
    state = analyze("move R8,R7\nnop\nmove $R7,R1\nnop\nstop\n")

    assert any(item.category == "unresolved_symbol" and item.severity == "error" for item in state.diagnostics)
    assert "R1" not in state.registers


def test_exact_physical_placeholder_reads_keep_sparse_bit_facts():
    state = analyze("and R7,5,R1\nnop\nmove {R1},R2\nnop\nand R2,2,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("params", [{"R7": "parameter_input"}, {"$R7": "parameter_input"}])
def test_declared_register_shaped_parameters_do_not_read_hardware_bit_facts(params):
    state = analyze("and R8,5,R7\nnop\nmove $R7,R1\nnop\nand R1,2,R2\nnop\nstop\n", params=params)

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("prefix,comparison,branch", [
    ("and R7,5,R1", "cmp R1,2", "jnz"),
    ("not R7,R1", "cmp R1,R7", "jnz"),
    ("move R7,R1", "cmp R1,R7", "jz"),
])
def test_comparison_flags_combine_sparse_bits_and_value_relations(prefix, comparison, branch):
    state = analyze(f"{prefix}\nnop\n{comparison}\nnop\n{branch} @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_sparse_comparison_does_not_prove_equality_for_matching_distinct_register_facts():
    state = analyze("and R7,5,R1\nand R8,5,R2\nnop\ncmp R1,R2\nnop\njz @maybe\n"
                    "wait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)
