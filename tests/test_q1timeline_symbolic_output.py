import json
import math

import pytest

from q1lens import output
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.output import Repeat
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.signals import SignalModel


def sequence(program, *, settings=None):
    return {
        "program": program,
        "waveforms": {"long": {"index": 0, "data": [0.5, -0.25, 1.0, -1.0] * 9},
                      "short": {"index": 1, "data": [0.125, -0.375] * 4}},
        "output_settings": settings or {},
    }


def concrete_samples(seq, params, end):
    state = interpret_program(parse_q1asm(seq["program"], file="<test>"), sequencer_id="s",
                              execution_mode="concrete", params=params)
    return samples_from_concrete_packets(seq, state, end)


def samples_from_concrete_packets(seq, state, end):
    """Drive the independent sampled simulator from executed concrete RT packets."""
    assert not [d for d in state.diagnostics if d.severity == "error"]
    model = SignalModel({"sequencers": {"s": seq["output_settings"]}}, {"s": seq})
    for packet in state.rt_packets:
        if packet.meta.get("conditional_skipped"):
            continue
        events = [e for e in state.events if e.meta.get("rt_packet_id") == packet.id and e.kind != "q1_issue"]
        settings = next((dict(e.meta["applied_state"]) for e in events if "applied_state" in e.meta), {})
        settings["_phase_reset"] = settings.pop("phase_reset", False)
        waves = [e.meta["waveform_index"] for e in events if e.kind == "play"]
        model.apply("s", packet.rt_t0.value, packet.op, waves, settings)
    model._render(end)
    return model.signals["s"].stages


PROBE = """move $N,R0
nop
set_awg_gain $G,$G
body: play 0,1,20
wait $T
loop R0,@body
set_awg_gain 1000,1000
play 0,0,40
stop"""

RAMP = """move 5,R0
move 1000,R1
nop
body: set_awg_gain R1,R1
play 0,1,12
add R1,1000,R1
nop
loop R0,@body
wait 4
stop"""

NCO_LOOP = """move N,R0
set_freq 50000000
upd_param 8
body: set_ph_delta 125000000
play 0,1,12
set_freq -100000000
upd_param 8
loop R0,@body
wait 12
stop"""


@pytest.mark.parametrize(("program", "params", "end"), [
    (PROBE, {"N": 4, "T": 12, "G": -16001}, 178),
    (RAMP, {}, 70),
    ("move N,R0\nmove 100,R1\nnop\nagain: play 0,1,8\nset_awg_gain R1,R1\nadd R1,200,R1\nnop\n"
     "loop R0,@again\nplay 0,1,8\nstop", {"N": 5}, 57),
    ("move 3,R0\nnop\nouter: move 4,R1\nnop\ninner: play 0,1,8\nloop R1,@inner\nwait 4\nloop R0,@outer\nstop",
     {}, 122),
    (NCO_LOOP, {"N": 4}, 111),
    ("set_awg_gain G,100\nupd_param 4\nplay 0,1,8\nset_awg_offs 3000,-2000\n"
     "set_awg_gain 900,-200\nset_freq 40000000\nset_ph 250000000\nupd_param 4\n"
     "reset_ph\nupd_param 4\nwait 20\nstop", {"G": 16000}, 52),
    ("play 0,1,8\nmove 3,R0\nnop\nbody: wait 4\nloop R0,@body\nstop", {}, 41),
    ("move 3,R0\nset_ph_delta 250000000\nbody: play 0,1,12\nloop R0,@body\nstop", {}, 48),
    ("move 3,R0\nnop\nbody: wait 4\nplay 0,1,4\nset_awg_gain 16000,8000\nloop R0,@body\n"
     "play 0,1,12\nstop", {}, 45),
    ("move N,R0\nnop\nset_ph 250000000\nbody: set_ph_delta 100000000\nreset_ph\nset_awg_offs 1000,900\n"
     "upd_param 8\nwait 4\nloop R0,@body\nstop", {"N": 3}, 48),
    ("move N,R0\nnop\nplay 0,1,8\nbody: wait 4\nplay 1,0,8\nset_freq 100000000\nwait 4\n"
     "loop R0,@body\nupd_param 8\nstop", {"N": 5}, 105),
])
def test_matches_concrete_signal_model(program, params, end):
    seq = sequence(program)
    model = output(seq, params)
    assert not model.issues
    expected = concrete_samples(seq, params, end)
    for stage in ("waveform", "gain_offset", "nco", "dac"):
        for subrange in [(0, end), (5, 23), (end - 5, end)]:
            lo, hi = subrange
            actual = model.samples(lo, hi, stage=stage)
            for p in (0, 1):
                assert actual[p] == pytest.approx(list(expected[stage][p][lo:hi]), abs=4e-14), (stage, lo, hi, p)


def test_symbolic_probe_repeat_seek_wrap_and_display():
    model = output(sequence(PROBE))
    repeat = model.segments[0]
    assert isinstance(repeat, Repeat)
    assert str(model.segments[1].start) == "N * (20 + T)"
    assert "G/32768" in model._repr_html_()
    assert json.loads(json.dumps(model.to_json()))["status"] == "modeled"
    for n, count in ((0, 2**32), (2_000_000, 2_000_000)):
        bound = model.bind({"T": 24, "G": 16384, "N": n})
        start = count * 44
        assert bound.samples(start - 1, start + 1, stage="gain_offset")[0] == [0, 500 / 32768]
    for params in ({"T": 3, "G": 1, "N": 1}, {"T": 65536, "G": 1, "N": 1},
                   {"T": 4, "G": -40000, "N": 1}, {"T": 4, "G": 0, "N": 2**32}):
        with pytest.raises(ValueError):
            model.bind(params)


@pytest.mark.parametrize("body", [
    # A skipped capture leaves a persistent gain pending for the next lap.
    "again: set_cond 0,0,0,4\nplay 0,1,8\nset_awg_gain 16384,0\nset_cond 1,0,0,4\nplay 0,1,8",
    # A skipped capture consumes the previous lap's one-shot phase delta.
    "set_cond 1,0,0,4\nagain: play 0,1,8\nset_cond 0,0,0,4\nplay 0,1,8\n"
    "set_cond 1,0,0,4\nset_ph_delta 250000000",
])
def test_compact_loop_skipped_capture_matches_concrete_latches(body):
    from q1timeline.output import from_state

    seq = sequence(f"move 3,R0\n{body}\nloop R0,@again\nset_cond 0,0,0,4\nplay 0,1,8\nstop")
    expanded = interpret_program(parse_q1asm(seq["program"], file="s"), sequencer_id="s", execution_mode="concrete")
    assert output(seq).samples(0, 48, stage="nco") == from_state(expanded, seq).samples(0, 48, stage="nco")


def test_raw_threshold_packet_applies_pending_offset_on_subsequent_laps():
    seq = sequence("move 3,R0\nagain: fb_cmd 0x10,2,8\nset_awg_offs 16384,0\nloop R0,@again\nstop")
    assert output(seq).samples(0, 25, stage="gain_offset")[0] == [0] * 8 + [.5] * 17


def test_symbolic_waveform_selection_and_model_assumptions():
    model = output(sequence("play W,1,20\nwait 20\nstop"))
    for w in (0, 1):
        wave = output(sequence(f"play {w},1,20\nwait 20\nstop"))
        assert model.bind({"W": w}).samples(0, 45) == wave.samples(0, 45)
    with pytest.raises(ValueError, match="Missing waveform"):
        model.bind({"W": 12})
    assert "rounding" in model.to_json()["dac_assumption"]
    bound = model.bind({"W": 0})
    assert bound.samples(0, 3, quantizer=lambda v, bits: 1.0)[0] == [1, 1, 1]
    tail = output(sequence("play W,1,4\nstop"))
    assert tail.duration.evaluate({}) == 4
    assert [tail.waveform_end.evaluate({"W": w}) for w in (0, 1)] == [36, 8]
    assert tail.to_json()["tail"]["source_event"] == tail.tail.source_event


def test_window_seek_preserves_half_open_boundaries_and_missing_parameter_errors():
    model = output(sequence("play 0,1,8\nset_awg_gain G,0\nplay 1,0,8\nstop"))
    assert model.samples(7, 8, stage="waveform") == ([-1.0], [-0.375])
    assert model.samples(8, 8, stage="waveform") == ([], [])
    with pytest.raises(ValueError, match="Missing output parameter G"):
        model.samples(7, 9, stage="waveform")
    with pytest.raises(ValueError, match="Missing output parameter G"):
        model.samples(16, 17, stage="waveform")

    unresolved_end = output(sequence("play 0,1,8\nwait T\nstop"))
    with pytest.raises(ValueError, match="Missing output parameter T"):
        unresolved_end.samples(0, 1)


def test_bound_window_index_follows_mutable_params_and_custom_quantizer():
    from dataclasses import replace

    model = output(sequence("play 0,1,T\nplay 1,0,8\nstop"))
    bound = model.bind({"T": 8})
    bound.params["T"] = 12
    reference = replace(model, params={"T": 12})
    assert bound.samples(7, 17) == reference.samples(7, 17)
    bound.params["T"] = 8.0
    with pytest.raises(TypeError):
        bound.samples(7, 17)

    def quantized(out):
        def quantize(value, bits):
            out.params["T"] = 12
            return value
        return out.samples(6, 14, quantizer=quantize)

    assert quantized(model.bind({"T": 8})) == quantized(replace(model, params={"T": 8}))


def test_stationary_offset_calls_custom_quantizer_for_each_path_and_tick():
    model = output(sequence("set_awg_offs -4096,2048\nupd_param 4\nwait 8\nstop"))
    calls = []
    def quantize(value, bits):
        calls.append(value)
        return len(calls)
    assert model.samples(2, 8, quantizer=quantize) == ([1, 3, 5, 7, 9, 11], [2, 4, 6, 8, 10, 12])
    assert calls == [-0.125, 0.0625] * 6


@pytest.mark.parametrize("shadow", [False, True])
def test_bind_checks_dependent_endpoints_of_nested_repeats(shadow):
    from dataclasses import replace
    from q1timeline.analysis.expr import Expr

    model = output(sequence("wait 4\nstop"))
    leaf, = model.segments
    outer = Expr.symbol("outer")
    inner = outer if shadow else Expr.symbol("inner")
    # With the same index name, the inner count still reads the outer index.
    inner_count = outer + 1 if shadow else Expr.const(2)
    gain = inner.in_range(0, 0) if shadow else (outer + inner).in_range(0, 1)
    leaf = replace(leaf, gain=(gain, gain))
    child = Repeat(leaf.start, inner_count, leaf.end, inner.value, (leaf,), "child")
    parent = Repeat(leaf.start, Expr.symbol("N"), leaf.end * 2, outer.value, (child,), "parent")
    model = replace(model, segments=(parent,), tail=None)
    assert model.parameter_names == {"N"}
    assert model.bind({"N": 0}).params == {"N": 0}
    bound = model.bind({"N": 1})
    with pytest.raises(ValueError, match="outside"):
        bound.bind({"N": 2})


def test_unknown_branch_and_missing_memory_are_not_silence():
    for program in ("jlt R0,1,@alt\nset_awg_gain 3,3\nalt: play 0,0,8\nstop", "play 3,1,4\nstop"):
        model = output(sequence(program))
        assert model.issues
        with pytest.raises(ValueError):
            model.samples(0, 4)


@pytest.mark.parametrize("iir", [{"a": [1], "b": [0.2, 0.8]}, {"a": [1, -0.25], "b": [1, -0.1]}])
def test_digital_filters_match_signal_model_across_windows(iir):
    seq = sequence("play 0,1,8\nwait 24\nset_awg_offs 400,200\nupd_param 4\nstop",
                   settings={"fir": [1, -0.5, 0.125], "iir": iir, "dac_bits": 13})
    model = output(seq)
    expected = concrete_samples(seq, {}, 4130)
    assert model.filtered
    for stage in ("fir", "filtered", "dac"):
        for lo, hi in [(0, 43), (17, 31), (4092, 4130)]:
            for observed, reference in zip(model.samples(lo, hi, stage=stage), expected[stage]):
                assert observed == list(reference[lo:hi])


def test_memory_arrays_and_legacy_names_without_samples():
    from array import array

    seq = sequence("play 0,1,20\nstop")
    values = seq["waveforms"]["long"]["data"]
    seq["waveforms"]["long"]["data"] = array("d", values)
    seq["waveforms"]["unused waveform"] = {"index": 4}
    assert output(seq).samples(0, 4, stage="waveform")[0] == values[:4]
    seq["program"] = "play 4,1,20\nstop"
    assert "Missing waveform 4" in output(seq).issues[0]


def test_partial_or_assumed_paths_do_not_claim_a_final_output():
    from q1timeline.output import from_state

    seq = sequence("play 0,1,4\nmove 3,R0\nnop\nagain: wait 4\nloop R0,@again\nstop")
    partial = interpret_program(parse_q1asm(seq["program"], file="s"), sequencer_id="s",
                                execution_mode="concrete", max_steps=2)
    assert partial.rt_packets and partial.metadata["execution"]["status"] == "step_limit"
    assert "Incomplete concrete execution" in from_state(partial, seq).issues[0]
    assumed = interpret_program(parse_q1asm("jlt R0,1,@alt\nset_awg_gain 2,0\nalt: play 0,1,8\nstop",
                                           file="s"), sequencer_id="s", branch_policy="assume_true")
    assert "assumed branch" in from_state(assumed, seq).issues[0]
    assert output(sequence("play 0,1,4")).issues


def test_symbolic_output_survives_incomplete_queue_analysis():
    from q1timeline.analysis.api import analyze_text
    from q1timeline.analysis.expr import Expr

    seq = sequence("wait T\nplay 0,1,8\nstop")
    ir = analyze_text(seq["program"], sequencer_id="s", sequence=seq)
    assert any(e["kind"] == "analysis_incomplete" and e["lane"] == "debug.underflow" for e in ir["events"])
    lane = ir["output"]["sequencers"]["s"]
    assert lane["status"] == "modeled"
    start = next(node["start"] for node in lane["segments"] if node["waveforms"] == [0, 1])
    assert Expr.from_value_json(start).evaluate({"T": 12}) == 12


def test_missing_sync_peer_still_blocks_symbolic_output():
    from q1timeline.analysis.api import analyze_documents

    seq = sequence("wait_sync 4\nplay 0,1,4\nstop")
    ir = analyze_documents({"s.q1asm": seq["program"], "peer.q1asm": "wait 8\nstop"}, sequences={"s": seq})
    assert ir["output"]["sequencers"]["s"]["status"] == "unavailable"


@pytest.mark.parametrize(("dwell", "start"), [(0, 44), (4, 44), (12, 52)])
def test_delayed_trigger_output_matches_concrete_packet_start(dwell, start):
    from q1timeline.output import from_state
    from q1timeline.q1asm.core import ExternalInputs

    seq = sequence(f"wait_trigger 1,{dwell}\nplay 0,1,4\nstop")
    state = interpret_program(parse_q1asm(seq["program"], file="s"), sequencer_id="s", execution_mode="concrete",
                              external=ExternalInputs(trigger_arrivals=((40, 1),)))
    assert state.rt_packets[1].rt_t0.value == start
    model = from_state(state, seq)
    assert model.samples(start - 1, start + 1, stage="waveform")[0] == [0, .5]


@pytest.mark.parametrize("laps", [1, 2, 3])
@pytest.mark.parametrize("skip", [False, True], ids=["execute", "skip"])
@pytest.mark.parametrize("dwell", [0, 4], ids=["dwell0", "dwell4"])
def test_trigger_conditional_loop_output_matrix(laps, skip, dwell):
    from q1timeline.output import from_state
    from q1timeline.q1asm.core import ExternalInputs

    body = (f"move N,R0\nnop\nset_awg_gain 8192,-8192\nagain: set_cond 0,0,0,4\nplay 0,1,4\n"
            f"set_awg_gain 16384,-16384\nset_ph_delta 250000000\nset_cond 1,0,{int(not skip)},4\n"
            "play 1,0,8\nloop R0,@again\nset_cond 0,0,0,4\nplay 0,1,4\nstop")
    seq = {"program": f"wait_trigger 1,{dwell}\n" + body, "output_settings": {},
           "waveforms": {"a": {"index": 0, "data": [1] * 4}, "b": {"index": 1, "data": [.5] * 4}}}
    state = interpret_program(parse_q1asm(seq["program"], file="s"), sequencer_id="s", execution_mode="concrete",
                              params={"N": laps}, external=ExternalInputs(trigger_arrivals=((40, 1),)))
    assert state.metadata["execution"]["status"] == "completed"
    trigger, first_play, *_ = state.rt_packets
    assert trigger.op == "wait_trigger" and trigger.rt_t1.value == 40 + dwell
    assert first_play.op == "play" and first_play.rt_t0.value == 44
    assert sum(bool(p.meta.get("conditional_skipped")) for p in state.rt_packets) == (laps if skip else 0)

    # Both dwell values occupy one 4 ns dequeue slot after arrival. A skipped
    # play consumes the phase delta, keeps the gain pending, and waits 4 ns.
    expected = [0j] * 44
    rotation = 1
    for lap in range(laps):
        gain = .25 if lap == 0 else .5
        expected += [rotation * complex(gain, -gain / 2)] * 4
        if not skip:
            rotation *= 1j
            expected += [rotation * complex(.25, -.5)] * 4
        expected += [0j] * 4
    expected += [rotation * complex(.5, -.25)] * 4 + [0j] * 4
    channels = ([v.real for v in expected], [v.imag for v in expected])
    end = len(expected)

    # Analysis has no arrival schedule. Bind the equivalent release edge and
    # loop count, and also lower the original program's observed trigger packet.
    assert any("wait_trigger" in issue for issue in output(seq).issues)
    compact = output({**seq, "program": "wait RELEASE\n" + body}).bind({"RELEASE": 44, "N": laps})
    assert any(isinstance(node, Repeat) for node in compact.segments)
    assert compact.samples(0, end, stage="nco") == channels
    assert from_state(state, seq).samples(0, end, stage="nco") == channels
    for path, samples in enumerate(samples_from_concrete_packets(seq, state, end)["nco"]):
        assert list(samples) == channels[path]


def test_simulation_uses_dequeue_clock_and_applied_fields_once():
    from q1timeline.analysis.alignment import align_timelines
    from q1timeline.output import from_state
    from q1timeline.output.modules import ModuleConfig
    from q1timeline.output.timeline import output_section
    from q1timeline.simulation.config import validate_simulation
    from q1timeline.simulation.runtime import simulate_programs

    seq = sequence("set_awg_gain 8192,8192\nset_ph_delta 250000000\nplay 0,1,4\n"
                   "set_awg_offs 1000,0\nupd_param 4\nreset_ph\nplay 0,1,4\nstop")
    config = validate_simulation({"startup_delay_ns": 512, "physical": {"sequencers": {"s": {
        "nco_frequency_hz": 25000000, "fir": [1, -.25], "dac_bits": 12,
    }}}}, ["s"])
    state, = simulate_programs({"s": parse_q1asm(seq["program"], file="s")}, params={}, max_steps=100,
                               simulation=config, sequences={"s": seq})
    model = from_state(state, seq)
    assert model.duration.evaluate({}) == 524
    for trace in state.metadata["simulation"]["signals"]["traces"]:
        if trace["stage"] in ("waveform", "gain_offset", "nco", "filtered", "dac"):
            path = model.samples(0, len(trace["samples"]), stage=trace["stage"])[int(trace["path"] == "q")]
            assert path == pytest.approx(trace["samples"], abs=1e-14)
    state.metadata["output_sequence"] = seq
    align_timelines([state], mode="first_rt_dequeue")
    module = ModuleConfig("board", "QRM", {"s": {"out0": "I"}})
    preview = output_section([state], {"board": module})["ports"][0]["preview"]
    assert preview["t0"] == 0
    assert preview["values"] == model.samples(512, 768, stage="filtered")[0]


@pytest.mark.parametrize(("q_code", "expected"), [
    (112.5, 112), (113.5, 114), (-112.5, -112), (-113.5, -114),
    (math.nextafter(112.5, 0), 112), (math.nextafter(112.5, math.inf), 113),
])
def test_dac_half_lsb_at_exact_quadrature_matches_concrete_and_late_seek(q_code, expected):
    # At 15 ns the 250 MHz NCO has advanced 3.75 turns, so output I is exactly input Q.
    seq = {"program": "move N,R0\nnop\nset_freq 1000000000\nbody: play 0,1,16\nloop R0,@body\nstop",
           "waveforms": {"i": {"index": 0, "data": [-.5] * 16},
                         "q": {"index": 1, "data": [q_code / 32768] * 16}}, "output_settings": {}}
    model = output(seq)
    stages = concrete_samples(seq, {"N": 3}, 48)
    bound = model.bind({"N": 3})
    for stage in ("gain_offset", "nco", "dac"):
        for sym, ref in zip(bound.samples(0, 48, stage=stage), stages[stage]):
            assert sym == list(ref)
    for at in (15, 31, 47):
        assert stages["nco"][0][at] * 32768 == q_code
        assert stages["dac"][0][at] * 32768 == expected
    last = 16 * 2**32 - 1
    assert model.bind({"N": 0}).samples(last, last + 1)[0] == [expected / 32768]


def test_unknown_loop_counter_names_register_and_source_line():
    model = output(sequence("again: play 0,0,4\nloop R7,@again\nstop"))
    issue, = model.issues
    assert "Loop L0 count at <sequence>:2" in issue and "counter R7" in issue
    assert "initial value" in issue and "{" not in issue
    with pytest.raises(ValueError, match="counter R7"):
        model.samples(0, 4)


@pytest.mark.parametrize("error", [AttributeError, KeyError, TypeError, ValueError])
def test_lowering_does_not_convert_implementation_errors_into_unavailable_output(monkeypatch, error):
    from q1timeline.output import build

    def broken(*args):
        raise error("unexpected model invariant")
    monkeypatch.setattr(build._Builder, "block", broken)
    with pytest.raises(error, match="unexpected model invariant"):
        output(sequence("play 0,1,4\nstop"))


def test_wait_and_following_play_share_the_analyzed_duration_tree():
    from q1timeline.output import from_state

    seq = sequence("wait T\nplay 0,1,8\nstop")
    state = interpret_program(parse_q1asm(seq["program"]), sequencer_id="s")
    model = from_state(state, seq)
    duration = state.rt_packets[0].duration.tree
    assert model.segments[0].end is duration
    assert model.segments[1].start is duration
    assert model.bind({"T": 12}).samples(11, 13, stage="waveform")[0] == [0, .5]
    with pytest.raises(ValueError, match="outside"):
        model.bind({"T": 3})


def test_nested_stationary_repeats_build_a_bounded_number_of_intervals(monkeypatch):
    from q1timeline.output import build

    depth = 6
    lines = ["move N,R0", "nop"]
    for i in range(1, depth):
        lines.extend([f"L{i-1}: move 4,R{i}", "nop"])
    lines.append(f"L{depth-1}: play 0,1,8")
    lines.extend(f"loop R{i},@L{i}" for i in reversed(range(depth)))
    calls = 0
    interval = build._Builder.interval

    def count_interval(*args):
        nonlocal calls
        calls += 1
        return interval(*args)

    monkeypatch.setattr(build._Builder, "interval", count_interval)
    model = output(sequence("\n".join([*lines, "stop"])))
    assert not model.issues and calls <= 2 * depth
    node, = model.segments
    for _ in range(depth - 1):
        node, = node.body
    assert isinstance(node, Repeat)
    bound = model.bind({"N": 3})
    last = 8 * 3 * 4 ** (depth - 1) - 1
    assert bound.samples(last, last + 1, stage="waveform") == ([-1.0], [-.375])
