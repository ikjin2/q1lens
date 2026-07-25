from __future__ import annotations

import json
from pathlib import Path

import yaml

from q1timeline.cli import main as q1timeline_main


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "cursor-frequency-demo"

def _analyze_cursor_frequency_demo(tmp_path: Path) -> dict:
    out_file = tmp_path / "cursor-frequency-ir.json"
    exit_code = q1timeline_main(["analyze", "--project", str(EXAMPLE_DIR / "q1timeline.yml"), "--out", str(out_file)])

    assert exit_code == 0
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_cursor_frequency_demo_project_documents_signal_roles() -> None:
    project = yaml.safe_load((EXAMPLE_DIR / "q1timeline.yml").read_text(encoding="utf-8"))
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert (EXAMPLE_DIR / "cursor-frequency-demo.ipynb").is_file()
    assert {sequencer["id"] for sequencer in project["sequencers"]} == {
        "blue_peak",
        "red_cursor",
        "red_tracker",
        "orange_drive",
    }
    assert {sequencer["id"]: sequencer["module"] for sequencer in project["sequencers"]} == {
        "blue_peak": "QCM0",
        "red_cursor": "QRM0",
        "red_tracker": "QRM0",
        "orange_drive": "QCM1",
    }
    assert "blue peak" in readme
    assert "xorshift PRNG" in readme
    assert "QCM marker 1" in readme
    assert "oscilloscope can trigger" in readme
    assert "external trigger" in readme
    assert "default bench workflow" in readme
    assert "start_demo_burst()" in readme
    assert "finish_demo_burst(read_data=True)" in readme
    assert "download only" in readme
    assert "does not wait for the full 65536-bin acquisition to complete" in readme
    assert "run_demo_live_plot_loop" in readme
    assert "30 us" in readme
    assert "33.3 kHz" in readme
    assert "1.0 seconds" in readme
    assert "32768 left/right shot pairs" in readme
    assert "first-pass window" in readme
    assert "reused bin contains the hardware average" in readme
    assert "not overwritten with only the newest sample" in readme
    assert "abs(I)+abs(Q)" in readme
    assert "acquisition feedback" in readme
    assert "next shot" in readme
    assert "red cursor" in readme
    assert "orange sine" in readme
    assert "low-MHz" in readme
    assert "python -m q1lens q1timeline analyze" in readme
    assert "triggered shot loop" in readme
    assert project["alignment"]["mode"] == "first_anchor"
    assert project["alignment"]["anchor_kinds"] == ["wait_trigger"]


def test_cursor_frequency_demo_q1asm_runs_as_triggered_shot_loop() -> None:
    for file_name in ("blue_peak.q1asm", "red_cursor.q1asm", "red_tracker.q1asm", "orange_drive.q1asm"):
        source = (EXAMPLE_DIR / file_name).read_text(encoding="utf-8")

        assert "shot_loop:" in source
        assert "wait_trigger 1, $TRIGGER_WAIT" in source
        assert ".DEF WAIT_CHUNK" in source
        assert "jmp @shot_loop" in source
        assert "\n    stop" not in source


def test_cursor_frequency_demo_uses_real_acquisition_feedback_for_tracker() -> None:
    source = (EXAMPLE_DIR / "red_tracker.q1asm").read_text(encoding="utf-8")
    notebook = json.loads((EXAMPLE_DIR / "cursor-frequency-demo.ipynb").read_text(encoding="utf-8"))
    notebook_source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )

    assert "fb_acq_iq_id $IQ_ID" in source
    assert "fb_acq_iq_shift $IQ_SHIFT" in source
    assert ".DEF EDGE_ACQ_NUM_BINS" in source
    assert "move 0, $LEFT_BIN" in source
    assert "move 1, $RIGHT_BIN" in source
    assert "acquire $EDGE_ACQ, $LEFT_BIN, $ACQ_DUR" in source
    assert "acquire $EDGE_ACQ, $RIGHT_BIN, $ACQ_DUR" in source
    assert "add $LEFT_BIN, 2, $LEFT_BIN" in source
    assert "jge $RIGHT_BIN, $EDGE_ACQ_NUM_BINS, @wrap_edge_bins" in source
    assert "fb_pop_data $IQ_ID, $LEFT_I" in source
    assert "fb_pop_data $IQ_ID, $LEFT_Q" in source
    assert "fb_pop_data $IQ_ID, $RIGHT_I" in source
    assert "fb_pop_data $IQ_ID, $RIGHT_Q" in source
    assert "sub $ZERO, $LEFT_I, $LEFT_I" in source
    assert "sub $ZERO, $LEFT_Q, $LEFT_Q" in source
    assert "sub $ZERO, $RIGHT_I, $RIGHT_I" in source
    assert "sub $ZERO, $RIGHT_Q, $RIGHT_Q" in source
    assert "add $LEFT_I, $LEFT_Q, $LEFT_MAG" in source
    assert "add $RIGHT_I, $RIGHT_Q, $RIGHT_MAG" in source
    assert "sub $RIGHT_MAG, $LEFT_MAG, $SLOPE" in source
    assert ".DEF TRACKER_CENTER_OFFSET" in source
    assert ".DEF TRACKED_CENTER" in source
    assert ".DEF TRACKED_GAIN" in source
    assert "add $MEAS_DELAY, $TRACKER_CENTER_OFFSET, $TRACKED_CENTER" in source
    assert "add $LEFT_MAG, $RIGHT_MAG, $TRACKED_GAIN" in source
    assert "asr $TRACKED_GAIN, 1, $TRACKED_GAIN" in source
    assert "fb_com_data $CURSOR_CHANNEL, $TRACKED_CENTER" in source
    assert "fb_com_data $FREQ_CHANNEL, $TRACKED_CENTER" in source
    assert "fb_com_data $CURSOR_GAIN_CHANNEL, $TRACKED_GAIN" in source
    assert "fb_com_data $RF_GAIN_CHANNEL, $TRACKED_GAIN" in source
    assert "\"tracked_edge\": {\"num_bins\": params[\"EDGE_ACQ_NUM_BINS\"], \"index\": params[\"EDGE_ACQ\"]}" in notebook_source
    assert "cluster.set_cmm_route(params[\"CURSOR_GAIN_CHANNEL\"], [qrm_module.sequencer0])" in notebook_source
    assert "cluster.set_cmm_route(params[\"RF_GAIN_CHANNEL\"], [qcm_orange_module.sequencer0])" in notebook_source
    assert "qrm_module.sequencer1.demod_en_acq(True)" in notebook_source
    assert "qrm_module.sequencer1.integration_length_acq(ACQ_DUR)" in notebook_source


def test_cursor_frequency_demo_tracks_peak_position_into_orange_frequency(tmp_path: Path) -> None:
    ir = _analyze_cursor_frequency_demo(tmp_path)
    params = json.loads((EXAMPLE_DIR / "params.json").read_text(encoding="utf-8"))

    diagnostics = ir["diagnostics"]
    assert not [diagnostic for diagnostic in diagnostics if diagnostic["severity"] == "error"]
    assert not any(diagnostic["category"] == "feedback_latency_violation" for diagnostic in diagnostics)
    assert not any(diagnostic["category"] == "possible_underflow" for diagnostic in diagnostics)

    flows = ir["feedback_flows"]
    channels = {flow["channel"] for flow in flows}
    assert {
        str(params["IQ_FEEDBACK_CHANNEL"]),
        str(params["CURSOR_CHANNEL"]),
        str(params["FREQ_CHANNEL"]),
        str(params["CURSOR_GAIN_CHANNEL"]),
        str(params["RF_GAIN_CHANNEL"]),
    } <= channels
    assert any(
        flow["channel"] == str(params["IQ_FEEDBACK_CHANNEL"])
        and flow["source"].startswith("acq#")
        and flow["target"] == "$LEFT_I"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["IQ_FEEDBACK_CHANNEL"])
        and flow["source"].startswith("acq#")
        and flow["target"] == "$LEFT_Q"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["IQ_FEEDBACK_CHANNEL"])
        and flow["source"].startswith("acq#")
        and flow["target"] == "$RIGHT_I"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["IQ_FEEDBACK_CHANNEL"])
        and flow["source"].startswith("acq#")
        and flow["target"] == "$RIGHT_Q"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["CURSOR_CHANNEL"])
        and flow["source"] == "$TRACKED_CENTER"
        and flow["target"] == "$CURSOR_CENTER"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["FREQ_CHANNEL"])
        and flow["source"] == "$TRACKED_CENTER"
        and flow["target"] == "$CURSOR_CENTER"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["CURSOR_GAIN_CHANNEL"])
        and flow["source"] == "$TRACKED_GAIN"
        and flow["target"] == "$TRACKED_GAIN"
        for flow in flows
    )
    assert any(
        flow["channel"] == str(params["RF_GAIN_CHANNEL"])
        and flow["source"] == "$TRACKED_GAIN"
        and flow["target"] == "$SINE_GAIN"
        for flow in flows
    )
    assert ir["feedback_balance"]["channels"][str(params["IQ_FEEDBACK_CHANNEL"])]["status"] == "balanced"
    assert ir["feedback_balance"]["channels"][str(params["CURSOR_CHANNEL"])]["status"] == "balanced"
    assert ir["feedback_balance"]["channels"][str(params["FREQ_CHANNEL"])]["status"] == "balanced"
    assert ir["feedback_balance"]["channels"][str(params["CURSOR_GAIN_CHANNEL"])]["status"] == "balanced"
    assert ir["feedback_balance"]["channels"][str(params["RF_GAIN_CHANNEL"])]["status"] == "balanced"

    events = ir["events"]
    assert {"play", "acquire", "feedback_com", "feedback_pop", "latched_state_pending", "marker_state", "upd_param", "wait_trigger"} <= {
        event["kind"] for event in events
    }
    loop_blocks = [event for event in events if event["kind"] == "loop_block"]
    assert {event["sequencer_id"] for event in loop_blocks} == {
        "blue_peak",
        "red_cursor",
        "red_tracker",
        "orange_drive",
    }
    assert all(event["meta"]["count"] == "forever" for event in loop_blocks)
    assert all(event["meta"]["period"]["value"] == params["SHOT_PERIOD"] for event in loop_blocks)
    wait_triggers = [event for event in events if event["kind"] == "wait_trigger"]
    assert {event["sequencer_id"] for event in wait_triggers} == {
        "blue_peak",
        "red_cursor",
        "red_tracker",
        "orange_drive",
    }

    red_cursor_events = [event for event in events if event["sequencer_id"] == "red_cursor"]
    red_tracker_events = [event for event in events if event["sequencer_id"] == "red_tracker"]
    orange_events = [event for event in events if event["sequencer_id"] == "orange_drive"]
    red_cursor_play = next(event for event in red_cursor_events if event["kind"] == "play")
    orange_play = next(event for event in orange_events if event["kind"] == "play")
    tracker_acquires = [event for event in red_tracker_events if event["kind"] == "acquire"]
    assert len(tracker_acquires) == 2
    assert all(event["meta"].get("feedback", {}).get("data_type") == "iq_values" for event in tracker_acquires)
    assert not any(event["kind"] == "play" for event in red_tracker_events)

    blue_peak_play = next(event for event in events if event["sequencer_id"] == "blue_peak" and event["kind"] == "play")
    blue_peak_t0 = blue_peak_play["t0"]["value"]
    blue_peak_t1 = blue_peak_t0 + blue_peak_play["duration"]["value"]
    red_cursor_center = red_cursor_play["t0"]["value"] + red_cursor_play["duration"]["value"] / 2
    orange_t0 = orange_play["t0"]["value"]
    orange_t1 = orange_t0 + orange_play["duration"]["value"]
    assert blue_peak_play["duration"]["value"] >= 5000
    assert blue_peak_t0 < red_cursor_center < blue_peak_t1
    assert orange_t0 < blue_peak_t1 and blue_peak_t0 < orange_t1

    blue_markers = [
        event
        for event in events
        if event["sequencer_id"] == "blue_peak" and event["kind"] == "marker_state"
    ]
    blue_wait_trigger = next(
        event
        for event in events
        if event["sequencer_id"] == "blue_peak" and event["kind"] == "wait_trigger"
    )
    assert [event["meta"]["value"] for event in blue_markers] == [params["TRIG_MRK_MASK"], 0]
    assert blue_markers[0]["t0"]["value"] == blue_wait_trigger["t0"]["value"] + params["TRIGGER_WAIT"]
    assert blue_markers[1]["t0"]["value"] == blue_markers[0]["t0"]["value"] + params["TRIG_HIGH"]

    feedback_sends = [event for event in red_tracker_events if event["kind"] == "feedback_com"]
    assert [event["meta"]["feedback"]["channel"] for event in feedback_sends] == [
        str(params["CURSOR_CHANNEL"]),
        str(params["FREQ_CHANNEL"]),
        str(params["CURSOR_GAIN_CHANNEL"]),
        str(params["RF_GAIN_CHANNEL"]),
    ]
    assert [event["meta"]["feedback"]["source"] for event in feedback_sends] == [
        "$TRACKED_CENTER",
        "$TRACKED_CENTER",
        "$TRACKED_GAIN",
        "$TRACKED_GAIN",
    ]

    cursor_gain_pop = next(
        event
        for event in red_cursor_events
        if event["kind"] == "feedback_pop"
        and event["meta"]["feedback"]["channel"] == str(params["CURSOR_GAIN_CHANNEL"])
    )
    assert cursor_gain_pop["meta"]["feedback"]["target"] == "$TRACKED_GAIN"

    frequency_pending = [
        event
        for event in events
        if event["sequencer_id"] == "orange_drive"
        and event["kind"] == "latched_state_pending"
        and event["meta"].get("field") == "frequency"
        and event["confidence"] == "symbolic"
    ]
    assert len(frequency_pending) == 1
    assert frequency_pending[0]["meta"]["value"]["kind"] == "symbolic"
    assert f"fb_pop_data channel {params['FREQ_CHANNEL']}" in frequency_pending[0]["meta"]["value"]["expr"]
    assert f"asl {params['FREQ_CURSOR_SHIFT']}" in frequency_pending[0]["meta"]["value"]["expr"]

    orange_update = next(
        event
        for event in events
        if event["sequencer_id"] == "orange_drive" and event["kind"] == "upd_param"
        and event["meta"]["applied_state"].get("frequency") == frequency_pending[0]["meta"]["value"]
    )
    applied_frequency = orange_update["meta"]["applied_state"]["frequency"]
    assert applied_frequency == frequency_pending[0]["meta"]["value"]

    gain_pending = [
        event
        for event in events
        if event["sequencer_id"] == "orange_drive"
        and event["kind"] == "latched_state_pending"
        and event["meta"].get("field") == "awg_gain"
        and event["confidence"] == "symbolic"
        and event["meta"].get("loop_iteration_index") == 0
    ]
    assert len(gain_pending) == 1
    assert gain_pending[0]["meta"]["value"][0]["expr"] == f"mulu16 result asr {params['RF_GAIN_SHIFT']}"
    assert orange_update["meta"]["applied_state"]["awg_gain"] == gain_pending[0]["meta"]["value"]


def test_cursor_frequency_demo_params_keep_cursor_center_offset_in_sync() -> None:
    params = json.loads((EXAMPLE_DIR / "params.json").read_text(encoding="utf-8"))

    assert params["SHOT_PERIOD"] == 30000
    assert params["WAIT_CHUNK"] == 500
    assert params["START_ALIGN"] >= 100
    assert params["TRIGGER_WAIT"] >= 8
    assert params["PEAK_DUR"] >= 9000
    assert params["PEAK_UPDATE_SHOTS"] >= 1000
    assert params["PEAK_STEP_MASK"] >= 255
    assert params["PEAK_OFFSET_SPAN"] >= 3000
    assert params["PEAK_OFFSET_MAX"] == params["PEAK_OFFSET_SPAN"] - 1
    assert params["PEAK_GAIN_MIN"] < params["PEAK_GAIN_INIT"] < params["PEAK_GAIN_MAX"]
    assert params["PEAK_GAIN_STEP_MASK"] > 0
    assert params["PEAK_GAIN_PRNG_SEED"] != params["PRNG_SEED"]
    assert params["TRACKER_INIT_DELAY"] > params["BLUE_PEAK_DELAY"]
    assert params["TRACKER_MIN_DELAY"] < params["TRACKER_INIT_DELAY"] < params["TRACKER_MAX_DELAY"]
    assert params["TRACKER_MAX_DELAY"] - params["TRACKER_MIN_DELAY"] >= 5000
    assert params["TRACKER_CENTER_OFFSET"] == params["MEAS_DELTA"] // 2 + params["ACQ_DUR"] // 2
    assert params["CURSOR_CENTER_TO_START"] == params["CURSOR_DUR"] // 2
    assert params["TRACKER_INIT_CENTER"] == params["TRACKER_INIT_DELAY"] + params["TRACKER_CENTER_OFFSET"]
    assert params["TRACKER_MIN_CENTER"] == params["TRACKER_MIN_DELAY"] + params["TRACKER_CENTER_OFFSET"]
    assert params["TRACKER_MAX_CENTER"] == params["TRACKER_MAX_DELAY"] + params["TRACKER_CENTER_OFFSET"]
    assert 0 < params["TRACK_STEP"] <= params["SLOPE_DEADBAND"]
    peak_center_min = (
        params["TRIG_HIGH"]
        + params["TRIG_LOW"]
        + params["BLUE_PEAK_DELAY"]
        + params["PEAK_DUR"] // 2
    )
    peak_center_max = peak_center_min + params["PEAK_OFFSET_MAX"]
    assert params["TRACKER_INIT_CENTER"] == peak_center_min
    assert params["TRACKER_MIN_CENTER"] < peak_center_min < params["TRACKER_MAX_CENTER"]
    assert params["TRACKER_MIN_CENTER"] < peak_center_max < params["TRACKER_MAX_CENTER"]
    assert params["TRACKER_MIN_DELAY"] < peak_center_min - params["TRACKER_CENTER_OFFSET"] < params["TRACKER_MAX_DELAY"]
    assert params["TRACKER_MIN_DELAY"] < peak_center_max - params["TRACKER_CENTER_OFFSET"] < params["TRACKER_MAX_DELAY"]
    assert params["ACQ_MIN_START_GAP"] >= 1000
    assert params["ACQ_DUR"] + params["MEAS_DELTA"] >= params["ACQ_MIN_START_GAP"]
    assert params["WAIT_FOR_IQ"] >= 900
    assert params["FB_CFG_WAIT"] >= 8
    assert params["FB_SEND_WAIT"] >= 8
    assert params["TRACKER_INIT_GAIN"] == 7000
    assert 0 < params["CURSOR_GAIN_NUMERATOR"] < (1 << params["CURSOR_GAIN_SHIFT"])
    assert params["CURSOR_GAIN_SHIFT"] == 8
    assert 0 < params["RF_GAIN_NUMERATOR"] < (1 << params["RF_GAIN_SHIFT"])
    assert params["RF_GAIN_NUMERATOR"] == 64
    assert params["RF_GAIN_SHIFT"] == 8
    assert len({
        params["IQ_FEEDBACK_CHANNEL"],
        params["CURSOR_CHANNEL"],
        params["FREQ_CHANNEL"],
        params["CURSOR_GAIN_CHANNEL"],
        params["RF_GAIN_CHANNEL"],
    }) == 5
    assert params["EDGE_ACQ_NUM_BINS"] >= 65536
    assert params["EDGE_ACQ_NUM_BINS"] % 2 == 0
    assert params["FREQ_BASE"] <= 500000
    assert params["FREQ_CURSOR_SHIFT"] >= 9
    assert params["UPDATE_DUR"] >= 16
    assert params["SINE_START_WAIT"] >= params["BLUE_PEAK_DELAY"]
    assert params["SINE_DUR"] >= params["PEAK_DUR"]
    assert params["SINE_DUR"] % 4 == 0
    assert params["SINE_START_WAIT"] + params["SINE_DUR"] >= (
        params["BLUE_PEAK_DELAY"] + params["PEAK_OFFSET_MAX"] + params["PEAK_DUR"]
    )
    assert params["SINE_START_WAIT"] + params["SINE_DUR"] <= params["SHOT_PERIOD"] - params["UPDATE_DUR"]
    cursor_initial_wait = params["TRACKER_INIT_CENTER"] - params["CURSOR_CENTER_TO_START"]
    assert params["CURSOR_POST_WAIT"] == (
        params["SHOT_PERIOD"]
        - params["TRIGGER_WAIT"]
        - cursor_initial_wait
        - params["CURSOR_DUR"]
        - params["POST_FEEDBACK_WAIT"]
    )
    latest_tracker_feedback_send = (
        params["TRIGGER_WAIT"]
        + params["TRACKER_MAX_DELAY"]
        + params["ACQ_DUR"]
        + params["MEAS_DELTA"]
        + params["ACQ_DUR"]
        + params["WAIT_FOR_IQ"]
    )
    assert params["CURSOR_POST_WAIT"] >= latest_tracker_feedback_send - (
        params["TRIGGER_WAIT"] + cursor_initial_wait + params["CURSOR_DUR"]
    )
    assert params["ORANGE_POST_WAIT"] == (
        params["SHOT_PERIOD"]
        - params["TRIGGER_WAIT"]
        - params["SINE_START_WAIT"]
        - params["SINE_DUR"]
        - params["UPDATE_DUR"]
        - params["UPDATE_DUR"]
    )
    assert params["ORANGE_POST_WAIT"] >= 128
    min_blue_post_wait = (
        params["SHOT_PERIOD"]
        - params["TRIGGER_WAIT"]
        - params["TRIG_HIGH"]
        - params["TRIG_LOW"]
        - params["BLUE_PEAK_DELAY"]
        - params["PEAK_OFFSET_MAX"]
        - params["PEAK_DUR"]
    )
    min_tracker_post_wait = (
        params["SHOT_PERIOD"]
        - params["TRIGGER_WAIT"]
        - params["TRACKER_MAX_DELAY"]
        - params["ACQ_DUR"]
        - params["MEAS_DELTA"]
        - params["ACQ_DUR"]
        - params["WAIT_FOR_IQ"]
        - (4 * params["FB_SEND_WAIT"])
    )
    min_cursor_feedback_wait = (
        params["SHOT_PERIOD"]
        - params["TRIGGER_WAIT"]
        - (params["TRACKER_MAX_CENTER"] - params["CURSOR_CENTER_TO_START"])
        - params["CURSOR_DUR"]
        - params["POST_FEEDBACK_WAIT"]
    )
    wait_chunk_budget = 16 * params["WAIT_CHUNK"]
    assert wait_chunk_budget < min(
        min_blue_post_wait,
        min_tracker_post_wait,
        min_cursor_feedback_wait,
        params["ORANGE_POST_WAIT"],
    )
    assert min(
        params["CURSOR_POST_WAIT"],
        params["ORANGE_POST_WAIT"],
    ) > 0


def test_cursor_frequency_demo_uses_slow_peak_walk_and_tracked_delay_consumers() -> None:
    blue_source = (EXAMPLE_DIR / "blue_peak.q1asm").read_text(encoding="utf-8")
    red_cursor_source = (EXAMPLE_DIR / "red_cursor.q1asm").read_text(encoding="utf-8")
    orange_source = (EXAMPLE_DIR / "orange_drive.q1asm").read_text(encoding="utf-8")

    assert ".DEF PEAK_UPDATE_SHOTS" in blue_source
    assert ".DEF PEAK_STEP_MASK" in blue_source
    assert ".DEF PEAK_GAIN_INIT     {PEAK_GAIN_INIT}" in blue_source
    assert ".DEF PEAK_GAIN_PRNG_SEED {PEAK_GAIN_PRNG_SEED}" in blue_source
    assert ".DEF GAIN_SEED" in blue_source
    assert ".DEF PEAK_GAIN" in blue_source
    assert "move $PRNG_SEED, $SEED" in blue_source
    assert "move $PEAK_GAIN_PRNG_SEED, $GAIN_SEED" in blue_source
    assert "move $PEAK_GAIN_INIT, $PEAK_GAIN" in blue_source
    assert "jge $SHOT_COUNT, $PEAK_UPDATE_SHOTS, @update_peak" in blue_source
    assert "and $SEED, $PEAK_STEP_MASK, $STEP" in blue_source
    assert "and $GAIN_SEED, $PEAK_GAIN_STEP_MASK, $STEP" in blue_source
    assert "jlt $TMP, $PEAK_GAIN_MIN, @gain_to_min" in blue_source
    assert "jlt $TMP, $PEAK_GAIN_MAX, @gain_set" in blue_source
    assert "set_awg_gain $PEAK_GAIN, $ZERO" in blue_source

    assert ".DEF TRACKER_INIT_GAIN  {TRACKER_INIT_GAIN}" in red_cursor_source
    assert ".DEF CURSOR_GAIN_CHANNEL {CURSOR_GAIN_CHANNEL}" in red_cursor_source
    assert ".DEF CURSOR_GAIN_NUMERATOR {CURSOR_GAIN_NUMERATOR}" in red_cursor_source
    assert ".DEF CURSOR_GAIN_SHIFT {CURSOR_GAIN_SHIFT}" in red_cursor_source
    assert ".DEF CURSOR_OUTPUT_GAIN" in red_cursor_source
    assert "move $TRACKER_INIT_CENTER, $CURSOR_CENTER" in red_cursor_source
    assert "move $TRACKER_INIT_GAIN, $TRACKED_GAIN" in red_cursor_source
    assert "mulu16 $TRACKED_GAIN, $CURSOR_GAIN_NUMERATOR, $CURSOR_OUTPUT_GAIN" in red_cursor_source
    assert "asr $CURSOR_OUTPUT_GAIN, $CURSOR_GAIN_SHIFT, $CURSOR_OUTPUT_GAIN" in red_cursor_source
    assert "fb_pop_data $CURSOR_CHANNEL, $CURSOR_CENTER" in red_cursor_source
    assert "fb_pop_data $CURSOR_GAIN_CHANNEL, $TRACKED_GAIN" in red_cursor_source
    assert "sub $CURSOR_WAIT, $CURSOR_CENTER_TO_START, $CURSOR_WAIT" in red_cursor_source
    assert "wait $CURSOR_WAIT" in red_cursor_source
    assert "set_awg_gain $CURSOR_OUTPUT_GAIN, $ZERO" in red_cursor_source
    assert "play $CURSOR_W, $CURSOR_W, $CURSOR_DUR" in red_cursor_source

    assert ".DEF RF_GAIN_CHANNEL    {RF_GAIN_CHANNEL}" in orange_source
    assert ".DEF RF_GAIN_NUMERATOR  {RF_GAIN_NUMERATOR}" in orange_source
    assert ".DEF RF_GAIN_SHIFT      {RF_GAIN_SHIFT}" in orange_source
    assert ".DEF SINE_OUTPUT_GAIN" in orange_source
    assert "move $TRACKER_INIT_CENTER, $CURSOR_CENTER" in orange_source
    assert "move $TRACKER_INIT_GAIN, $SINE_GAIN" in orange_source
    assert "mulu16 $SINE_GAIN, $RF_GAIN_NUMERATOR, $SINE_OUTPUT_GAIN" in orange_source
    assert "asr $SINE_OUTPUT_GAIN, $RF_GAIN_SHIFT, $SINE_OUTPUT_GAIN" in orange_source
    assert "fb_pop_data $FREQ_CHANNEL, $CURSOR_CENTER" in orange_source
    assert "fb_pop_data $RF_GAIN_CHANNEL, $SINE_GAIN" in orange_source
    assert "sub $CURSOR_CENTER, $TRACKER_MIN_CENTER, $FREQ_OFFSET" in orange_source
    assert "asl $FREQ_OFFSET, $FREQ_CURSOR_SHIFT, $FREQ_OFFSET" in orange_source
    assert "add $FREQ_OFFSET, $FREQ_BASE, $FREQ_WORD" in orange_source
    assert "set_awg_gain $SINE_OUTPUT_GAIN, $ZERO" in orange_source
    phase_reset = orange_source.index("reset_ph")
    phase_update = orange_source.index("upd_param $UPDATE_DUR", phase_reset)
    sine_play = orange_source.index("play $SINE_W, $SINE_W, $SINE_DUR", phase_update)
    assert phase_reset < phase_update < sine_play


def test_cursor_frequency_demo_notebook_is_qblox_experiment_workflow() -> None:
    notebook = json.loads((EXAMPLE_DIR / "cursor-frequency-demo.ipynb").read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )

    assert "# Cursor Frequency Demo" in source
    assert "from qcodes.instrument import find_or_create_instrument" in source
    assert "import matplotlib.pyplot as plt" in source
    assert "import numpy as np" in source
    assert "from IPython.display import display" in source
    assert "from qblox_instruments import Cluster, ClusterType" in source
    assert "cluster_ip" in source
    assert "ROOT = Path.cwd()" in source
    assert "cluster_ip = \"10.10.200.53\"" in source
    assert "cluster_name = \"QAS\"" in source
    assert "qcm_blue_module_index = 4" in source
    assert "qcm_orange_module_index = 6" in source
    assert "qrm_module_index = 8" in source
    assert "qcm_blue_slot = module_slot(qcm_blue_module)" in source
    assert "qcm_orange_slot = module_slot(qcm_orange_module)" in source
    assert "qrm_slot = module_slot(qrm_module)" in source
    assert "dummy_cfg" in source
    assert "cluster.set_cmm_route(params[\"CURSOR_CHANNEL\"], [qrm_module.sequencer0])" in source
    assert "cluster.set_cmm_route(params[\"FREQ_CHANNEL\"], [qcm_orange_module.sequencer0])" in source
    assert "cluster.set_cmm_route(params[\"CURSOR_GAIN_CHANNEL\"], [qrm_module.sequencer0])" in source
    assert "cluster.set_cmm_route(params[\"RF_GAIN_CHANNEL\"], [qcm_orange_module.sequencer0])" in source
    assert "cluster.ext_trigger_input_trigger_en(True)" in source
    assert "cluster.ext_trigger_input_trigger_address(1)" in source
    assert "red_cursor_pulse = [1.0] * CURSOR_DUR" in source
    assert "starts the four sequencers used by the demo" in source
    assert "triggered shot loop" in source
    assert "external trigger" in source
    assert "acquisition feedback" in source
    assert "next shot" in source
    assert "QCM marker 1 for the scope trigger" in source
    assert "independent xorshift PRNG random walks for position and height" in source
    assert "first QCM sequencer 0 waits for the external trigger" in source
    assert "$TRACKED_GAIN = (left magnitude + right magnitude) / 2" in source
    assert "applies a calibrated fixed-point cursor gain from the latest tracked gain" in source
    assert "applies a calibrated fixed-point RF gain with `set_awg_gain`" in source
    assert "second QCM sequencer 0 receives the tracked center" in source
    assert "subtracts `TRACKER_MIN_CENTER`" in source
    assert "scales the offset with `FREQ_CURSOR_SHIFT`" in source
    assert "QCM marker 1 carries the scope trigger" in source
    assert "QRM sequencer 0 centers the red cursor on the latest tracked center" in source
    assert "QRM sequencer 1 acquires left/right IQ samples" in source
    assert "compares `abs(I)+abs(Q)`" in source
    assert "sends center and gain feedback" in source
    assert "30 us" in source
    assert "33.3 kHz" in source
    assert "1.0 seconds" in source
    assert "32768 left/right shot pairs" in source
    assert "downloads only in the final stop/reset cell" in source
    assert "reused bin contains the hardware average" in source
    assert "not overwritten with only the newest sample" in source
    assert "blue_peak_sequence.json" in source
    assert "red_cursor_sequence.json" in source
    assert "red_tracker_sequence.json" in source
    assert "orange_drive_sequence.json" in source
    assert "red_cursor" in source
    assert "qcm_blue_module.sequencer0.sequence(\"blue_peak_sequence.json\")" in source
    assert "qrm_module.sequencer0.sequence(\"red_cursor_sequence.json\")" in source
    assert "qrm_module.sequencer1.sequence(\"red_tracker_sequence.json\")" in source
    assert "qcm_orange_module.sequencer0.sequence(\"orange_drive_sequence.json\")" in source
    assert "cluster.clear_sequencer_flags(slot=slot, sequencer=seq_idx)" in source
    assert "def prepare_demo_run():" in source
    assert "def start_demo_burst():" in source
    assert "def finish_demo_burst(" in source
    assert "wait_for_status=False" in source
    assert "def pause_demo_triggers(" in source
    assert "def resume_demo_triggers():" in source
    assert "def drain_tracker_acquisition(read_data=True, wait_for_status=False, clear_flags=False, as_numpy=True):" in source
    assert "def tracker_acquisition_rows(acquisitions):" in source
    assert "def read_cursor_register_snapshot(iteration=None):" in source
    assert 'cluster.get_sequencer_registers(qrm_slot, 0, ["R1", "R4", "R5"])' in source
    assert "def plot_tracker_acquisition(acquisitions, *, axes=None, title=None, cursor_history=None, max_plot_pairs=5000):" in source
    assert "def run_demo_live_plot_loop(" in source
    assert "def run_demo_burst_loop(" in source
    assert "Stop the external trigger before running" in source
    assert "cluster.ext_trigger_input_trigger_en(False)" in source
    assert "cluster.ext_trigger_input_trigger_en(True)" in source
    assert "prepare_demo_run()" in source
    assert "qrm_module.get_acquisition_status(1, timeout=1)" in source
    assert "wait_for_status=wait_for_status" in source
    assert "cluster.get_acquisitions(qrm_slot, 1, as_numpy=as_numpy)" in source
    assert "print_status=False" in source
    assert "as_numpy=as_numpy" in source
    assert "qrm_module.delete_acquisition_data(1, all=True)" in source
    assert "tracked_edge = acquisitions[\"tracked_edge\"][\"acquisition\"][\"bins\"]" in source
    assert "math.hypot(i_value, q_value)" in source
    assert "np.hypot(left_i, left_q)" in source
    assert "valid_pairs = (avg_cnt[left_bins] > 0) & (avg_cnt[right_bins] > 0)" in source
    assert "plot_rows = rows" in source
    assert "plot_step = math.ceil(len(rows) / max_plot_pairs)" in source
    assert "showing {len(plot_rows)} of {len(rows)} pairs" in source
    assert "show_cursor = bool(cursor_history)" in source
    assert "fig, mag_ax = plt.subplots(1, 1, figsize=(9, 3.5))" in source
    assert "axes = np.asarray([mag_ax])" in source
    assert "axes = np.atleast_1d(axes)" in source
    assert "mean_magnitude = (left_magnitude + right_magnitude) / 2" in source
    assert "mag_ax.plot(x_values, [row[\"mean_magnitude\"] for row in plot_rows], label=\"sqrt(I^2 + Q^2)\")" in source
    assert "mag_ax.set_ylabel(\"sqrt(I^2 + Q^2)\")" in source
    assert "cursor_ax = axes[1] if show_cursor and len(axes) > 1 else None" in source
    assert "cursor_ax.plot(cursor_x, cursor_y, marker=\"o\", color=\"tab:purple\", label=\"cursor center R1\")" in source
    assert "plt.subplots(2, 1, figsize=(9, 6))" in source
    assert "axis.clear()" in source
    assert "axes=axes" in source
    assert "cursor_history=cursor_history" in source
    assert "display_handle = display(fig, display_id=True)" in source
    assert "display_handle.update(fig)" in source
    assert "time.sleep(update_s)" in source
    assert "pause_demo_triggers(idle_wait_s)" in source
    assert "resume_demo_triggers()" in source
    assert "drain_tracker_acquisition(read_data=True)" in source
    assert "cursor_snapshot = read_cursor_register_snapshot(iteration=iteration)" in source
    assert '"cursor_snapshot": cursor_snapshot' in source
    assert "time.sleep(burst_s)" in source
    assert "Stopping sequencers and downloading tracker acquisition" in source
    assert "tracker_acquisitions = finish_demo_burst(read_data=True, print_status=False)" in source
    assert "Download finished in" in source
    assert "Plotting tracker acquisition" in source
    assert "fig, axes, rows = plot_tracker_acquisition(tracker_acquisitions)" in source
    assert "display(fig)" in source
    assert "plt.close(fig)" in source
    assert "Plotted {len(rows)} tracked-edge pairs" in source
    assert "Optional: run this only after the acquisition plot is visible." in source
    assert "Reading cursor register snapshot" in source
    assert "cursor_history=[cursor_snapshot]" in source
    assert "# cluster.reset()" in source
    assert "Optional monitoring: run_demo_live_plot_loop(...) downloads at every plot update" in source
    assert "cluster.clear_sequencer_flags(slot=qrm_slot, sequencer=1)" in source
    assert "cluster.arm_sequencer(slot=qcm_blue_slot, sequencer=0)" in source
    assert "cluster.arm_sequencer(slot=qcm_orange_slot, sequencer=0)" in source
    assert "cluster.arm_sequencer(slot=qrm_slot, sequencer=0)" in source
    assert "cluster.arm_sequencer(slot=qrm_slot, sequencer=1)" in source
    assert "cluster.start_sequencer(slot=qcm_blue_slot, sequencer=0)" in source
    assert "cluster.start_sequencer(slot=qcm_orange_slot, sequencer=0)" in source
    assert "cluster.start_sequencer(slot=qrm_slot, sequencer=0)" in source
    assert "cluster.start_sequencer(slot=qrm_slot, sequencer=1)" in source
    assert "cluster.start_sequencer()" not in source
    assert "print_all_sequencer_statuses(\"After start\")" in source
    assert "for seq_idx in range(6):" in source
    assert "qcm_module =" not in source
    assert "qcm_module.sequencer" not in source
    assert "cluster.reset()" in source
    assert '"-m"' in source
    assert '"q1lens"' in source
    assert '"q1timeline"' in source
    assert '"analyze"' in source
    assert '"render"' in source
    assert "sequence_json_by_id" in source
    assert "q1timeline.with-sequences.yml" in source
    assert "expected_labels" in source
    assert "feedback_flows" in source
    assert 'flow["source"] == "$TRACKED_CENTER"' in source
    assert 'flow["source"] == "$TRACKED_GAIN"' in source
    assert "applied_state" in source
    assert "fb_pop_data channel" in source
    assert "blue_peak_t0" in source
    assert "red_cursor_center" in source
    assert "acquisition feedback" in source
    assert "orange sine" in source

    live_loop_source = source[
        source.index("def run_demo_live_plot_loop("):source.index("def run_demo_burst_loop(")
    ]
    assert "start_demo_burst()" not in live_loop_source
    assert "finish_demo_burst(" not in live_loop_source
    assert "stop_demo_sequencers()" not in live_loop_source

    prepare_source = source[
        source.index("def prepare_demo_run():"):source.index("def start_demo_burst():")
    ]
    assert prepare_source.index("cluster.ext_trigger_input_trigger_en(False)") < prepare_source.index("stop_demo_sequencers()")
    assert prepare_source.index("stop_demo_sequencers()") < prepare_source.index("clear_demo_runtime_state()")
    assert prepare_source.index("clear_demo_runtime_state()") < prepare_source.index("arm_demo_sequencers()")
    assert prepare_source.index("arm_demo_sequencers()") < prepare_source.index("start_demo_sequencers()")
    assert prepare_source.index("start_demo_sequencers()") < prepare_source.index("resume_demo_triggers()")
