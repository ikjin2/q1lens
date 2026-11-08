import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="state-join.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("setter,field,value", [
    ("set_mrk 3", "marker", 3),
    ("set_awg_gain 100,200", "awg_gain", [100, 200]),
    ("set_awg_offs 100,200", "awg_offset", [100, 200]),
    ("set_freq 400", "frequency", 400),
    ("set_ph 100", "phase", 100),
    ("set_ph_delta 100", "phase_delta", 100),
    ("reset_ph", "phase", 0),
    ("set_digital 1,0,0", "digital", [1, 0, 0]),
    ("set_scope_en 1", "scope_enable", 1),
])
def test_equal_latched_settings_get_one_common_pending_proof_with_both_sources(setter, field, value):
    source = f"jge R7,1,@other\n{setter}\njmp @join\nother: {setter}\njoin: upd_param 4\nstop"
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    joined, = [event for event in state.events if event.kind == "latched_state_pending" and event.meta.get("branch_join_sources")]
    update, = [event for event in state.events if event.kind == "upd_param"]

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(4)
    assert joined.meta["field"] == field
    assert joined.meta["value"] == value
    assert joined.meta["branch_join_sources"].keys() == {"taken", "fallthrough"}
    assert update.meta["applied_state"][field] == value
    assert update.meta["applied_setter_event_ids"][field] == joined.id
    assert not state.latched_state.pending_since


def test_different_pending_values_merge_to_unknown_with_both_sources_and_known_elapsed_time():
    state = analyze("jge R7,1,@other\nset_mrk 3\njmp @join\nother: set_mrk 4\njoin: upd_param 4\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(4)
    joined, = [event for event in state.events if event.kind == "latched_state_pending" and event.meta.get("branch_join_sources")]
    update, = [event for event in state.events if event.kind == "upd_param"]
    assert joined.meta["value"]["kind"] == "unknown"
    assert joined.meta["branch_join_sources"].keys() == {"taken", "fallthrough"}
    assert update.meta["applied_state"]["marker"]["kind"] == "unknown"
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["all_paths_safe"]


def test_different_pending_consumption_does_not_invent_a_common_application():
    state = analyze("set_mrk 3\njge R7,1,@other\nwait 100\njmp @join\nother: play 0,1,100\njoin: upd_param 4\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_rt_join_proven")


def test_equal_final_frequency_keeps_different_applied_nco_histories_uncertain():
    state = analyze("jge R7,1,@other\nset_freq 100\nupd_param 40\nset_freq 200\nupd_param 60\njmp @join\n"
                    "other: set_freq 150\nupd_param 40\nset_freq 200\nupd_param 60\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(140)
    assert state.nco_phase_uncertain
    region = next(event for event in state.events if event.meta.get("reason") == "branch_outputs_unknown")
    assert region.meta["nco_phase_uncertain"]
    assert region.meta["latched_state_at_join"]["frequency"] == 200


def test_different_waveforms_keep_the_prefix_uncertain_and_the_common_suffix_exact():
    state = analyze("set_mrk 3\njge R7,1,@other\nplay 0,1,100\njmp @join\nother: play 2,3,100\n"
                    "join: play 4,5,40\nstop", waveform_names={2: "chosen I", 3: "chosen Q", 4: "common I", 5: "common Q"})
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    region, = [event for event in state.events if event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown"]
    prefix = [event for event in state.events if event.kind == "play" and event.meta.get("branch_join_path")]
    suffix = [event for event in state.events if event.kind == "play" and not event.meta.get("branch_join_path")]

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(140)
    assert (region.t0, region.t1, region.duration) == (Concrete(0), Concrete(100), Concrete(100))
    assert region.meta["output_state_unknown"] and region.meta["branch_timing_known"]
    assert region.meta["latched_state_at_join"]["marker"] == 3
    assert region.meta["pending_fields_at_join"] == []
    assert [event.label for event in prefix] == ["chosen I", "chosen Q"]
    assert all(event.confidence == "assumed" for event in prefix)
    assert [event.label for event in suffix] == ["common I", "common Q"]
    assert all(event.t0 == Concrete(100) and event.confidence == "exact" for event in suffix)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["all_paths_safe"]


def test_different_waveforms_can_have_an_identical_proven_queue_supply():
    source = ("jge R7,1,@other\n" + "nop\n" * 5 + "play 0,1,100\njmp @join\nother: play 2,3,100\n"
              + "nop\n" * 4 + "join: wait 40\nstop")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_join_proven"]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_same_captured_literal_condition_uses_its_existing_equal_duration_proof():
    state = analyze("jge R7,1,@other\nset_cond 1,1,0,100\nwait 100\njmp @join\n"
                    "other: set_cond 1,1,0,100\nwait 100\njoin: set_cond 0,0,0,4\nwait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(140)


def test_disagreeing_registers_get_fresh_identity_and_cannot_reuse_one_arms_relation():
    state = analyze("jge R7,1,@other\nmove R8,R1\nnop\njmp @join\nother: move R9,R1\nnop\n"
                    "join: xor R1,R8,R2\nnop\njz @maybe\nwait 100\nstop\nmaybe: wait 200\nstop")
    branches = [event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") in {"jge", "jz"}]

    assert branches[0].meta["branch_rt_join_proven"]
    assert not isinstance(state.registers["R2"], Concrete)
    assert branches[1].meta["branch_decision"] == "runtime_dependent"


def test_join_keeps_only_flags_proved_on_both_classical_prefixes():
    state = analyze("move 2,R3\nnop\njge R7,1,@other\nmove 4,R1\nnop\njmp @join\nother: add R3,2,R1\nnop\n"
                    "join: jz @maybe\nwait 100\nstop\nmaybe: wait 200\nstop")
    branches = [event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") in {"jge", "jz"}]

    assert branches[0].meta["branch_rt_join_proven"]
    assert state.registers["R1"] == Concrete(4)
    assert branches[1].meta["branch_decision"] == "concrete"
    assert branches[1].meta["branch_taken"] is False


def test_invalid_branch_target_line_and_its_branch_are_excluded_before_join_analysis():
    state = analyze("jge R7,1,@other\nmove 4,R1\nnop\njmp @join\nother: add 2,2,R1\nnop\njoin: wait 100\nstop")
    assert any(item.severity == "error" for item in state.diagnostics)
    assert not any(event.kind == "q1_issue" and event.meta.get("op") == "jge" for event in state.events)
    assert any(event.kind == "wait" for event in state.events)
    assert {item.source.line for item in state.diagnostics if item.details.get("source_line_skipped")} == {1, 5}


@pytest.mark.parametrize("prefix", [
    "lsl R8,R9,R1\nand R1,0,R1\nnop",
    "and R8,65535,R1\nnop\nset_awg_gain R1,R1",
])
def test_unknown_unsupported_operand_domains_cannot_hide_inside_a_timing_join(prefix):
    state = analyze(f"jge R7,1,@other\n{prefix}\nwait 100\njmp @join\n"
                    "other: wait 100\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_rt_join_proven")
    assert not state.queue_alternatives


def test_disagreeing_classical_values_retain_only_bounds_and_bits_true_on_both_paths():
    state = analyze("jge R7,1,@other\nand R8,15,R1\nnop\njmp @join\nother: and R9,7,R1\nnop\n"
                    "join: jlt R1,16,@known\nwait 200\nstop\nknown: wait 100\nstop")
    branches = [event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") in {"jge", "jlt"}]

    assert branches[0].meta["branch_rt_join_proven"]
    assert state.register_bounds["R1"] == (0, 15)
    assert state.register_known_bits["R1"][1] & 0xFFFFFFF0 == 0xFFFFFFF0
    assert not isinstance(state.registers["R1"], Concrete)
    assert branches[1].meta["branch_decision"] == "concrete"
    assert state.rt_time_ns == Concrete(100)


def test_same_unknown_waveform_description_never_becomes_a_known_output_choice():
    state = analyze("jge R7,1,@other\nplay R8,R9,100\njmp @join\nother: play R10,R11,100\njoin: wait 40\nstop")

    assert state.rt_time_ns == Concrete(140)
    assert any(event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown" for event in state.events)


def test_joined_supported_operator_range_preserves_equal_future_timing_without_predicting_outputs():
    state = analyze("jge R7,1,@other\nset_cond 1,1,0,40\njmp @join\nother: set_cond 1,1,1,40\n"
                    "join: play 0,1,40\nstop")

    assert state.conditional_operator_bounds == (0, 1)
    assert state.rt_time_ns == Concrete(40)
    assert not any(event.kind == "play" for event in state.events)
    assert any(event.kind == "unknown_region" and event.meta.get("conditional_timing_known") for event in state.events)


def test_joined_sparse_operators_with_the_same_empty_mask_result_can_execute():
    state = analyze("jge R7,1,@other\nset_cond 1,0,1,40\njmp @join\nother: set_cond 1,0,5,40\n"
                    "join: play 0,1,100\nstop")

    assert state.conditional_operator_bounds == (1, 5)
    assert state.conditional_operator_known_bits == (1, 0xFFFFFFFA)
    assert state.rt_time_ns == Concrete(100)
    assert len([event for event in state.events if event.kind == "play"]) == 2


def test_joined_operator_range_does_not_drop_a_possible_reserved_operator():
    state = analyze("jge R7,1,@other\nset_cond 1,0,1,40\njmp @join\nother: set_cond 1,0,7,40\n"
                    "join: play 0,1,40\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not any(event.kind == "play" for event in state.events)


@pytest.mark.parametrize("reset,uncertain", [("", True), ("reset_ph\n", False), ("set_ph 0\n", True)])
def test_possible_nco_application_requires_a_later_rearmed_absolute_phase_reset(reset, uncertain):
    source = ("set_freq 100\nset_ph 0\nset_cond 1,1,0,40\njge R7,1,@other\nplay 0,1,40\n"
              f"set_cond 0,0,0,4\n{reset}upd_param 4\njmp @join\nother: play 2,3,40\n"
              f"set_cond 0,0,0,4\n{reset}upd_param 4\njoin: wait 40\nstop")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.nco_phase_uncertain == uncertain
    region = next(event for event in state.events if event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown")
    if not uncertain:
        assert region.meta["latched_state_at_join"]["phase"] == 0
        assert region.meta["pending_fields_at_join"] == []
    else:
        assert "phase" not in region.meta["latched_state_at_join"]


def test_ordinary_acquisition_joins_keep_alternatives_uncertain_and_propagate_names():
    state = analyze("jz @other\nacquire 0,0,100\njmp @join\nother: acquire 1,0,100\njoin: acquire 2,0,40\nstop",
                    acquisition_names={0: "left", 1: "right", 2: "common"})
    prefix, = [event for event in state.events if event.kind == "acquire" and event.meta.get("branch_join_path")]
    suffix, = [event for event in state.events if event.kind == "acquire" and not event.meta.get("branch_join_path")]

    assert prefix.label == "right" and prefix.confidence == "assumed"
    assert suffix.label == "common" and suffix.t0 == Concrete(100)
    assert state.rt_time_ns == Concrete(140)
    assert any(event.kind == "unknown_region" and event.meta.get("output_state_unknown") for event in state.events)


def test_equal_hardware_waveform_indices_do_not_differ_because_of_alias_provenance():
    state = analyze(".DEF WI 0\n.DEF WQ 1\njge R7,1,@other\nplay $WI,$WQ,100\njmp @join\n"
                    "other: play 0,1,100\njoin: wait 40\nstop")

    assert state.rt_time_ns == Concrete(140)
    assert not any(event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown" for event in state.events)
    assert not any(event.meta.get("output_state_unknown") for event in state.events if event.kind == "play")


def test_weighted_acquisition_payloads_include_weights_not_just_bin_and_acquisition_indices():
    state = analyze("jge R7,1,@other\nacquire_weighed 0,0,0,1,100\njmp @join\n"
                    "other: acquire_weighed 0,0,2,3,100\njoin: wait 40\nstop")

    assert state.rt_time_ns == Concrete(140)
    assert any(event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown" for event in state.events)


def test_common_rt_state_joins_compose_after_q1_arrival_has_become_unknown():
    state = analyze("jge R7,1,@other\nwait 100\njmp @next\nother: wait 100\n"
                    "next: jge R6,1,@other2\nwait 40\njmp @join\nother2: wait 40\njoin: wait 4\nstop")
    branches = [event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge"]

    assert all(event.meta["branch_rt_join_proven"] for event in branches)
    assert not isinstance(branches[1].t0, Concrete)
    assert not isinstance(state.q1_time_ns, Concrete)
    assert state.rt_time_ns == Concrete(144)
    assert not state.metadata.get("branches", {}).get("assumptions")
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_ordinary_wait_and_play_can_share_queue_timing_without_sharing_outputs():
    source = ("jge R7,1,@other\n" + "nop\n" * 5 + "wait 100\njmp @join\nother: play 0,1,100\n"
              + "nop\n" * 4 + "join: wait 40\nstop")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_join_proven"]
    assert any(event.kind == "unknown_region" and event.meta.get("reason") == "branch_outputs_unknown" for event in state.events)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("other_arm,proven", [("wait 100", False), ("acquire 0,0,100", True)])
def test_feedback_producing_acquisition_joins_require_the_same_message_profile(other_arm, proven):
    state = analyze(f"fb_acq_iq_id 5,4\njge R7,1,@other\n{other_arm}\njmp @join\n"
                    "other: acquire 0,0,100\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert bool(branch.meta.get("branch_rt_join_proven")) == proven


@pytest.mark.parametrize("reset_arm", ["reset_ph", "reset_ph\nset_ph 0"])
def test_phase_reset_is_not_equal_to_a_phase_offset_update_at_a_join(reset_arm):
    state = analyze("set_freq 4000000\nreset_ph\nupd_param 4\nwait 100\njge R7,1,@other\n"
                    f"{reset_arm}\nupd_param 4\njmp @join\nother: set_ph 0\nupd_param 4\n"
                    "join: play 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(148)
    assert state.nco_phase_uncertain
    suffix = [event for event in state.events if event.kind == "play" and not event.meta.get("branch_join_path")]
    assert all(event.meta["nco_phase_uncertain"] and event.meta["output_state_unknown"] for event in suffix)
    region = next(event for event in state.events if event.meta.get("reason") == "branch_outputs_unknown")
    assert "phase" not in region.meta["latched_state_at_join"]
    assert region.meta["latched_state_at_join"]["frequency"] == 4000000


def test_branch_dependent_pending_reset_stays_unknown_until_captured_then_a_fresh_reset_converges():
    state = analyze("jge R7,1,@other\nreset_ph\nset_ph 0\njmp @join\nother: set_ph 0\n"
                    "join: upd_param 4\nreset_ph\nplay 0,1,40\nstop")
    update, = [event for event in state.events if event.kind == "upd_param"]
    plays = [event for event in state.events if event.kind == "play"]

    assert update.meta["captured_phase_reset"] is None
    assert update.meta["nco_phase_uncertain"]
    assert "phase_reset" not in update.meta
    assert all(event.meta["phase_reset"] and not event.meta.get("nco_phase_uncertain") for event in plays)
    assert not state.nco_phase_uncertain
    assert state.pending_phase_reset is False


def test_known_skip_consumes_the_pending_reset_without_resetting_uncertain_phase():
    state = analyze("set_freq 100\nset_cond 1,1,0,40\nplay 0,1,40\nset_cond 0,0,0,4\n"
                    "reset_ph\nset_ph 0\nset_cond 1,0,0,40\nplay 0,1,100\n"
                    "set_cond 0,0,0,4\nupd_param 4\nstop")
    skip, = [event for event in state.events if event.kind == "conditional_skip"]
    update, = [event for event in state.events if event.kind == "upd_param"]

    assert skip.meta["captured_phase_reset"] is True
    assert not skip.meta.get("phase_reset")
    assert skip.meta["applied_state"] == {}
    assert update.meta["captured_phase_reset"] is False
    assert update.meta["nco_phase_uncertain"]
    assert not update.meta.get("phase_reset")
    assert state.nco_phase_uncertain


def test_inherited_uncertain_phase_survives_a_later_equal_state_join():
    state = analyze("jge R7,1,@other\nset_freq 100\nupd_param 40\njmp @next\n"
                    "other: set_freq 200\nupd_param 40\nnext: jge R6,1,@other2\nwait 40\njmp @join\n"
                    "other2: wait 40\njoin: play 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(120)
    assert state.nco_phase_uncertain
    assert all(event.meta["nco_phase_uncertain"] for event in state.events if event.kind == "play")


@pytest.mark.parametrize("frequency,uncertain", [(0, False), (4000000, True), (40000000, False), (-40000000, False)])
def test_reset_time_differences_preserve_phase_only_for_an_exact_number_of_turns(frequency, uncertain):
    state = analyze("set_freq 4000000\nreset_ph\nupd_param 4\njge R7,1,@other\n"
                    f"set_freq {frequency}\nreset_ph\nupd_param 4\nwait 100\njmp @join\n"
                    f"other: wait 100\nset_freq {frequency}\nreset_ph\nupd_param 4\njoin: play 0,1,40\nstop")
    region = next(event for event in state.events if event.meta.get("reason") == "branch_outputs_unknown")
    suffix = [event for event in state.events if event.kind == "play"]

    assert state.rt_time_ns == Concrete(148)
    assert state.nco_phase_uncertain == uncertain
    assert bool(region.meta["nco_phase_uncertain"]) == uncertain
    assert ("phase" in region.meta["latched_state_at_join"]) != uncertain
    assert all(bool(event.meta.get("nco_phase_uncertain")) == uncertain for event in suffix)


def test_different_phase_offsets_after_stationary_resets_remain_uncertain():
    state = analyze("jge R7,1,@other\nset_freq 0\nreset_ph\nset_ph 100\nupd_param 4\nwait 100\njmp @join\n"
                    "other: wait 100\nset_freq 0\nreset_ph\nset_ph 200\nupd_param 4\njoin: play 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(144)
    assert state.nco_phase_uncertain


def test_stationary_reset_endpoints_compare_phase_modulo_a_full_turn():
    state = analyze("jge R7,1,@other\nset_freq 0\nreset_ph\nset_ph 1000000000\nupd_param 4\nwait 100\njmp @join\n"
                    "other: wait 100\nset_freq 0\nreset_ph\nupd_param 4\njoin: play 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(144)
    assert not state.nco_phase_uncertain
    assert not any(event.meta.get("nco_phase_uncertain") for event in state.events if event.kind == "play")


def test_equal_total_phase_does_not_equate_accumulated_phase_and_a_replaceable_phase_offset():
    state = analyze("set_freq 4000000\nreset_ph\nupd_param 4\njge R7,1,@other\n"
                    "set_ph 896000000\nupd_param 4\nwait 100\njmp @join\n"
                    "other: wait 100\nreset_ph\nupd_param 4\njoin: set_ph 0\nplay 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(148)
    assert state.nco_phase_uncertain
    assert all(event.meta["nco_phase_uncertain"] for event in state.events if event.kind == "play")


def test_phase_integration_decodes_register_frequency_words_as_signed_before_counting_turns():
    state = analyze("move 4000000000,R1\nnop\nset_freq R1\nreset_ph\nupd_param 4\njge R7,1,@other\n"
                    "reset_ph\nupd_param 4\nwait 100\njmp @join\n"
                    "other: wait 100\nreset_ph\nupd_param 4\njoin: play 0,1,40\nstop")

    assert state.rt_time_ns == Concrete(148)
    assert state.nco_phase_uncertain
    assert all(event.meta["nco_phase_uncertain"] for event in state.events if event.kind == "play")
