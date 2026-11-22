import itertools
from pathlib import Path
from time import monotonic

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm import core
from q1timeline.q1asm.ast import with_def_values
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.verification import _cases, verify_file


QUEUE = Path(__file__).resolve().parents[1] / "examples" / "def-register-sweep" / "queue.q1asm"


def source(tmp_path, text):
    path = tmp_path / "probe.q1asm"
    path.write_text(text, encoding="utf-8")
    return path


def project_with_peer(tmp_path):
    (tmp_path / "peer.q1asm").write_text("stop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: probe, file: probe.q1asm}\n- {id: peer, file: peer.q1asm}")
    return project


def test_finds_first_failure_without_a_sweep_and_replays_the_exact_event():
    check, = verify_file(QUEUE, startup_delay_ns=4, include_replay=True)["checks"]
    assert check["safe_prefix"] == {"name": "N", "min": 1, "max": 37}
    assert check["first_failure"] == {"name": "N", "value": 38}
    assert (check["line"], check["status"], check["startup_delay_ns"]) == (1, "counterexample", 4)
    witness = check["counterexample"]
    event, = [e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"]]
    assert (event["kind"], event["t0"]["value"], witness["inputs"]) == ("underflow_warning", 1188, {"N": 38})
    assert event["source"]["line"] == witness["line"]
    assert Path(event["source"]["file"]) == QUEUE
    assert witness["timeline"]["register_history"]


def test_buffered_start_before_first_packet_proves_wide_domain(tmp_path):
    path = source(tmp_path, ".DEF VALUE 7\nmove $VALUE,R0\nnop\nwait 8\nstop")
    check, = verify_file(path, max_cases=1)["checks"]
    assert check["status"] == "proved" and check["startup_delay_ns"] is None
    assert check["safe_prefix"] == {"name": "VALUE", "min": 1, "max": 2**32 - 1}
    assert verify_file(path, startup_delay_ns=4)["status"] == "counterexample"


def test_symbolic_delay_before_buffering_keeps_loop_exit_state_and_witness_time(tmp_path):
    prelude = ".DEF DELAY 8\nmove $DELAY,R0\nnop\npause: nop\nloop R0,@pause\n"
    safe = prelude + "jz @ready\nillegal\nready: wait 8\nstop"
    path = source(tmp_path, safe)
    assert verify_file(path, max_paths=1, max_cases=1)["status"] == "proved"
    # Delay the same starving pulse train, including cases with more than one
    # prefill loop iteration. Its failure time must match the actual clock.
    text = prelude + ";@assume DELAY == 8\nmove 41,R1\nnop\nrun: play 0,1,8\nloop R1,@run\nstop"
    check, = verify_file(source(tmp_path, text), include_replay=True)["checks"]
    state, = simulate_programs({"s": parse_q1asm(text)})
    failure, = [e for e in state.events if e.kind == "underflow_warning"]
    assert failure.t0.value > 1448
    assert check["counterexample"]["time_ns"] == failure.t0.value


def test_buffered_play_loop_boundary_matches_simulation_on_every_prefix_input(tmp_path):
    text = ".DEF N 20\nmove $N,R0\nnop\nrun: play 0,1,8\nloop R0,@run\nstop"
    path = source(tmp_path, text)
    check, = verify_file(path, include_replay=True)["checks"]
    assert check["first_failure"] == {"name": "N", "value": 41}
    assert check["safe_prefix"] == {"name": "N", "min": 1, "max": 40}
    program = parse_q1asm(text, file=str(path))
    for n in range(1, 42):
        state, = simulate_programs({"s": with_def_values(program, {"N": n})})
        failures = [e.t0.value for e in state.events if e.kind == "underflow_warning"]
        assert failures == ([1448] if n == 41 else [])
    witness = check["counterexample"]
    assert witness["time_ns"] == 1448
    event, = [e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"]]
    assert event["kind"] == "underflow_warning"


def test_prefill_deadline_and_stop_enqueue_ties(tmp_path, monkeypatch):
    from q1timeline.analysis import smt_queue
    from q1timeline.simulation import runtime

    assert smt_queue.POSSIBLE_INITIAL_BUFFER_NS == runtime.POSSIBLE_INITIAL_BUFFER_NS == 2_000_000
    # Compress the prefill window in both engines so the actual scheduler
    # crosses its deadline without recording half a million individual nops.
    monkeypatch.setattr(smt_queue, "POSSIBLE_INITIAL_BUFFER_NS", 12)
    monkeypatch.setattr(runtime, "POSSIBLE_INITIAL_BUFFER_NS", 12)
    for dwell in (0, 4, 8):
        for nops in (0, 2, 3, 4, 5, 6):
            for tail in ("stop", "wait 4\nstop"):
                text = f"wait {dwell}\n" + "nop\n" * nops + tail
                state, = simulate_programs({"s": parse_q1asm(text)})
                first = next((e for e in state.events if e.kind == "underflow_warning"), None)
                check, = verify_file(source(tmp_path, text))["checks"]
                if first:
                    assert check["status"] == "counterexample" and check["counterexample"]["time_ns"] == first.t0.value
                else:
                    assert state.metadata["execution"]["status"] == "completed" and check["status"] == "proved"


def test_single_hole_in_nonmonotone_domain_with_play(tmp_path):
    path = source(tmp_path, """.DEF MODE 3
;@assume 1 <= MODE <= 100
play 0,1,128
move $MODE,R0
nop
cmp R0,7
jnz @done
move 5,R1
nop
slow: loop R1,@slow
done: wait 4
stop""")
    check, = verify_file(path, startup_delay_ns=4)["checks"]
    assert check["first_failure"]["value"] == 7
    assert check["safe_prefix"]["max"] == 6
    for value in (6, 7, 8, 100):
        path.write_text(path.read_text().replace(";@assume 1 <= MODE <= 100", f";@assume MODE == {value}"))
        assert verify_file(path, startup_delay_ns=4)["status"] == ("counterexample" if value == 7 else "proved")
        path.write_text(path.read_text().replace(f";@assume MODE == {value}", ";@assume 1 <= MODE <= 100"))


def test_joint_domains_fixed_def_and_label_entry_clocks_registers(tmp_path):
    path = source(tmp_path, """.DEF X 1
.DEF Y 1
.DEF BIAS 5
;@assume 1 <= X <= 1000
;@assume 1 <= Y <= 5
;@assert rt_time == 8 at @readout
;@assert R2 <= 1010
;@assert R2 < 1010 at @readout
wait 8
move $X,R0
nop
add R0,$Y,R2
nop
add R2,$BIAS,R2
nop
readout: wait 128
stop""")
    clock, final, register, *_ = verify_file(path, startup_delay_ns=1024, include_replay=True)["checks"]
    assert (clock["status"], final["status"], register["status"]) == ("proved", "proved", "counterexample")
    assert register["line"] == 8 and register["fixed_defs"] == {"BIAS": 5}
    witness = register["counterexample"]
    assert witness["line"] == 16
    assert (witness["register"], witness["value"], witness["inputs"]) == ("R2", 1010, {"X": 1000, "Y": 5})
    event, = [e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"]]
    assert event["meta"]["register_snapshot"]["R2"]["value"] == 1010


def test_label_checks_repeated_visits_and_final_assertion_once(tmp_path):
    path = source(tmp_path, """;@assert rt_time == 0 at @again
;@assert rt_time == 12
move 3,R0
nop
again: wait 4
loop R0,@again
stop""")
    first, final, *_ = verify_file(path, startup_delay_ns=200, include_replay=True)["checks"]
    assert first["status"] == "counterexample" and final["status"] == "proved"
    witness = first["counterexample"]
    assert (witness["value"], witness["line"], witness["inputs"]) == (4, 5, {})
    visits = [e for e in witness["timeline"]["events"] if e["kind"] == "q1_issue" and e["source"]["line"] == 5]
    assert [e["id"] == witness["event_id"] for e in visits] == [False, True]


def test_unreachable_label_explains_why_its_assertion_holds(tmp_path):
    check, = verify_file(source(tmp_path, ";@assert R0 == 99 at @other\njmp @done\nother: nop\ndone: stop"))["checks"]
    assert check["status"] == "proved" and "unreachable" in check["message"]


@pytest.mark.parametrize("register,bound", [("R64", 0), ("R0", -1)])
def test_invalid_project_register_query_cannot_be_proved_at_an_unreachable_label(tmp_path, register, bound):
    path = source(tmp_path, f";@assert {register} == {bound} at @away\njmp @end\naway: nop\nend: stop")
    standalone, = verify_file(path)["checks"]
    multi, = verify_file(path, project=project_with_peer(tmp_path))["checks"]
    assert multi["status"] == standalone["status"] == "unknown"
    assert multi["message"] == standalone["message"]


@pytest.mark.parametrize("initial", ["nop", "move 0,R0", "move 0,R0\nnop"],
                         ids=["unwritten", "pending", "ready"])
@pytest.mark.parametrize("low,high", [(-2, -1), (0, 2), (2**32 - 3, 2**32 - 1)])
def test_repeated_label_verdict_and_witness_match_every_concrete_visit(tmp_path, initial, low, high):
    word_max = max(n & 0xFFFFFFFF for n in range(low, high + 1))
    text = (f".DEF N {low}\n;@assume {low} <= N <= {high}\n"
            f";@assert R0 <= {word_max} at @mark\n;@assert R0 < {word_max} at @mark\n"
            f"move 3,R1\n{initial}\nmark: nop\nmove $N,R0\nloop R1,@mark\nstop")
    path = source(tmp_path, text)
    program = parse_q1asm(text)
    # Record (input, instruction ordinal) directly at the core's issue boundary.
    visits = {}
    for n in range(low, high + 1):
        code = core.prepare_program(with_def_values(program, {"N": n}))
        state = core.CoreState()
        for ordinal in range(30):
            if code[state.pc].label == "mark":
                busy = any(write.index == 0 for write in state.pending_writes)
                visits[n, ordinal] = None if busy else state.registers[0]
            state = core.step(state, code).state
            if state.halted:
                break
        assert state.halted
    assert len(visits) == 3 * (high - low + 1)

    for scenario in (None, project_with_peer(tmp_path)):
        upper, strict = verify_file(path, project=scenario, include_replay=True)["checks"]
        assert upper["status"] == ("unknown" if None in visits.values() else "proved")
        assert strict["status"] == "counterexample"
        witness = strict["counterexample"]
        replay = [event for event in witness["timeline"]["events"] if event["kind"] == "q1_issue"]
        index = next(i for i, event in enumerate(replay) if event["id"] == witness["event_id"])
        assert visits[witness["inputs"]["N"], index] == witness["value"] == word_max


def test_unreadable_input_does_not_hide_later_project_counterexample(tmp_path):
    path = source(tmp_path, ".DEF N 1\n;@assume 1 <= N <= 2\n;@assert R0 <= 1 at @done\n"
                           "move $N,R1\nnop\njlt R1,2,@done\nmove $N,R0\nnop\ndone: stop")
    check, = verify_file(path, project=project_with_peer(tmp_path))["checks"]
    assert check["status"] == "counterexample"
    assert check["counterexample"]["inputs"] == {"N": 2} and check["counterexample"]["value"] == 2


@pytest.mark.parametrize("text,reason", [
    (";@assert R0 == 2 at @read\nmove 2,R0\nread: nop\nstop", "not ready"),
    (";@assert R0 == 2\nstop", "readable"),
    (";@assume 2 < N < 3\n.DEF N 2\nstop", "No value"),
    (";@assume 1 <= NO_NAME <= 8\nstop", "numeric DEF"),
    (";@assert rt_time > 8 at @missing\nstop", "executable instruction"),
    (";@assert rt_time = 0\nstop", "Compare"),
    (";@assert rt_time == 0\nstop\nwait -10", "duration"),
    (";@assert rt_time == 0\nendless: jmp @endless", "limit"),
])
def test_unresolved_or_invalid_checks_never_pass(tmp_path, text, reason):
    report = verify_file(source(tmp_path, text), max_steps=8)
    assert report["status"] == "unknown"
    assert reason.lower() in report["checks"][0]["message"].lower()


def test_project_startup_instance_and_parameter_binding(tmp_path):
    path = source(tmp_path, ".DEF X 5\n;@assume 1 <= X <= 3\n;@assert R2 <= 12\nwait 8\nmove $X,R0\nnop\nadd R0,OFFSET,R2\nnop\nstop")
    project = tmp_path / "q1timeline.yml"
    (tmp_path / "params.json").write_text('{"OFFSET":10,"X":999}')
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nparams:\n  file: params.json\nsimulation:\n  startup_delay_ns: 100")
    check, queue = verify_file(path, project=project)["checks"]
    assert queue["startup_delay_ns"] == 100 and queue["status"] == "proved"
    assert check["counterexample"]["inputs"] == {"X": 3}
    assert check["counterexample"]["value"] == 13
    project.write_text(project.read_text().replace("params:", "- {id: other, file: probe.q1asm}\nparams:"))
    assert verify_file(path, project=project)["status"] == "unknown"
    check, queue = verify_file(path, project=project, sequencer="other")["checks"]
    assert check["counterexample"]["sequencer"] == "other" and queue["status"] == "proved"


def test_analytical_receiver_schedule_is_not_silently_dropped(tmp_path):
    path = source(tmp_path, "wait_trigger 1,4\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nexternal_inputs:\n  triggers:\n"
                       "    - {id: gate, address: 1, targets: [drive], times_ns: [20]}\n")
    check, = verify_file(path, project=project)["checks"]
    assert check["status"] == "unknown" and "receiver schedule" in check["message"]


def test_analytical_assertion_binds_receiver_address_for_each_input(tmp_path):
    path = source(tmp_path, ";@assume 4 <= ADDRESS <= 5\n;@assert rt_time == 24 at @received\n"
                           ";@assert rt_time <= 28\nwait_trigger ADDRESS,4\nreceived: wait ADDRESS\nstop")
    (tmp_path / "params.json").write_text('{"ADDRESS": 4}')
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nparams: {file: params.json}\n"
                       "external_inputs:\n  triggers:\n"
                       "    - {id: gate, address: ADDRESS, targets: [drive], times_ns: [20]}\n")
    received, final, _queue = verify_file(path, project=project, include_replay=True)["checks"]
    assert received["status"] == "proved"
    witness = final["counterexample"]
    assert witness["inputs"] == {"ADDRESS": 5} and witness["value"] == 29
    assert any(e["id"] == witness["event_id"] for e in witness["timeline"]["events"])


def test_invalid_receiver_address_prevents_queue_only_project_proof(tmp_path):
    path = source(tmp_path, "wait 4\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nexternal_inputs:\n  triggers:\n"
                       "    - {id: gate, address: MISSING, targets: [drive], times_ns: [20]}\n")
    report = verify_file(path, project=project)
    assert report["status"] == "unknown" and "MISSING" in report["checks"][0]["message"]


@pytest.mark.parametrize("startup", [4, 200])
def test_invalid_receiver_binding_does_not_hide_later_witness(tmp_path, startup):
    path = source(tmp_path, ";@assume 0 <= A <= 1\n;@assert R0 <= 0\n;@assert rt_time <= 4\n"
                           ";@assert R0 <= 1\n;@assert no_clip\nmove A,R0\nnop\nadd R0,4,R1\n"
                           "nop\nwait R1\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nexternal_inputs:\n  triggers:\n"
                       "    - {id: gate, address: A, targets: [drive], times_ns: [20]}\n")
    report = verify_file(path, project=project, startup_delay_ns=startup, include_replay=True)
    assert report["status"] == "counterexample"
    register, clock, unresolved, output, queue = report["checks"]
    for check, expected in ((register, 1), (clock, 5)):
        witness = check["counterexample"]
        assert witness["inputs"] == {"A": 1} and witness["value"] == expected
        event = next(e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"])
        assert event["meta"]["op"] == "stop"
    assert unresolved["status"] == output["status"] == "unknown"
    assert "address" in unresolved["message"] and "address" in output["message"]
    if startup == 4:
        assert queue["counterexample"]["inputs"] == {"A": 1}
        assert queue["counterexample"]["time_ns"] == 4
    else:
        assert queue["status"] == "unknown" and "address" in queue["message"]
    assert "first_failure" not in queue and "safe_prefix" not in queue


def test_queue_safe_prefix_ends_before_invalid_receiver_address(tmp_path):
    path = source(tmp_path, ";@assume 14 <= A <= 16\nwait A\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nexternal_inputs:\n  triggers:\n"
                       "    - {id: gate, address: A, targets: [drive], times_ns: [20]}\n")
    queue, = verify_file(path, project=project, startup_delay_ns=200)["checks"]
    assert queue["status"] == "unknown" and "address" in queue["message"]
    assert queue["safe_prefix"] == {"name": "A", "min": 14, "max": 15}
    assert "first_failure" not in queue


def test_configured_simulation_replays_received_word_at_label(tmp_path):
    path = source(tmp_path, ";@assert R0 <= 6 at @readout\nfb_pop_data 5,R0\nnop\nreadout: wait 8\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\nanalysis: {execution_mode: simulation}\n"
                       "simulation:\n  inputs:\n    feedback:\n      - {target: drive, time_ns: 2, id: 5, value: 7}\n")
    check, queue = verify_file(path, project=project, include_replay=True)["checks"]
    assert queue["status"] == "proved"
    witness = check["counterexample"]
    assert (witness["line"], witness["value"]) == (4, 7)
    assert witness["timeline"]["execution"]["drive"]["mode"] == "simulation"


def test_incomplete_peer_cannot_serve_as_an_underflow_witness(tmp_path):
    path = source(tmp_path, "nop\nwait 8\nstop")
    (tmp_path / "peer.q1asm").write_text("fb_pop_data 5,R0\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n- {id: drive, file: probe.q1asm}\n- {id: peer, file: peer.q1asm}\n"
                       "simulation: {startup_delay_ns: 4}")
    check, = verify_file(path, project=project)["checks"]
    assert check["status"] == "unknown" and "peer" in check["message"]


def test_domain_iteration_is_lazy_and_handles_many_axes():
    assert list(itertools.islice(_cases({"A": (1, 2**32 - 1), "B": (7, 8)}), 3)) == [
        {"A": 1, "B": 7}, {"A": 1, "B": 8}, {"A": 2, "B": 7}]
    assert len(next(_cases({str(i): (0, 1) for i in range(1024)}))) == 1024


def test_timeout_preserves_prior_result_and_each_remaining_check(tmp_path, monkeypatch):
    import q1timeline.verification as verification

    clock = [monotonic()]
    monkeypatch.setattr(verification, "monotonic", lambda: clock[0])
    verify = verification.verify_domain

    def use_budget(*args, **kwargs):
        result = verify(*args, **kwargs)
        clock[0] += 5
        return result

    monkeypatch.setattr(verification, "verify_domain", use_budget)
    path = source(tmp_path, ";@assert rt_time == 4\n;@assert rt_time < 8\nwait 4\nstop")
    report = verify_file(path, timeout_ms=1000)
    assert report["status"] == "unknown"
    assert [check["status"] for check in report["checks"]] == ["proved", "unknown", "unknown"]
    assert all("timed out" in check["message"] for check in report["checks"][1:])
    program = parse_q1asm("spin: jmp @spin")
    with pytest.raises(TimeoutError):
        interpret_program(program, sequencer_id="seq", execution_mode="concrete", deadline=monotonic() - 1)
    with pytest.raises(TimeoutError):
        simulate_programs({"seq": program}, deadline=monotonic() - 1)
