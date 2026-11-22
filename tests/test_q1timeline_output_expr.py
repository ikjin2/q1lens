import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.expr import Expr


PROBE = """move $N,R0
nop
body: set_awg_gain $G,G
play 0,0,20
wait $T
loop R0,@body
set_awg_gain 1000,1000
play 0,0,40
stop"""


@pytest.mark.parametrize("field_count", [3, 4])
def test_older_slotted_expr_pickles_restore_formatting_caches(monkeypatch, field_count):
    import pickle

    expr = Expr.symbol("N").loop_count() + 4
    expected = str(expr), expr.display()
    with monkeypatch.context() as patch:
        patch.setattr(Expr, "__getstate__", lambda e: [e.op, e.args, e.value, e._text][:field_count])
        legacy = pickle.dumps(expr)
    restored = pickle.loads(legacy)
    assert (str(restored), restored.display()) == expected
    assert restored.evaluate({"N": 0}) == 2**32 + 4
    assert pickle.loads(pickle.dumps(restored)) == expr


def test_independent_core_binds_free_dollar_operands_and_preserves_defs():
    from q1timeline.q1asm.core import prepare_program
    from q1timeline.q1asm.parser import parse_q1asm

    program = parse_q1asm(".DEF OFFSET 9\n.DEF ALIAS {$G}\nmove $ALIAS,R0\nmove $G,R1\nmove G,R2\n"
                          "move $OFFSET,R3\nstop")
    bound = prepare_program(program, params={"G": 7, "OFFSET": 1})
    assert [i.args[0].value for i in bound if i.op == "move"] == [7, 7, 7, 9]


def test_symbolic_loop_end_preserves_display_domains_and_concrete_timing():
    ir = analyze_text(PROBE)
    assert not [item for item in ir["diagnostics"] if item["severity"] == "error"]
    first, last = [event for event in ir["events"] if event["kind"] == "play" and event["meta"]["path"] == 0]
    gains = [Expr.from_value_json(value) for value in first["meta"]["applied_state"]["awg_gain"]]
    assert gains[0] == gains[1]
    assert gains[0].bounds({}) == (-32768, 32767)
    end = Expr.from_value_json(last["t0"])
    assert end.free_symbols() == {"N", "T"}
    assert last["t0"]["expr"] == "N * (20 + T)"
    assert end.evaluate({"N": 0, "T": 8}) == (1 << 32) * 28
    with pytest.raises(ValueError):
        end.evaluate({"N": 1, "T": 3})
    with pytest.raises(ValueError):
        end.evaluate({"N": 2, "T": 65536})
    for count, dwell in [(1, 4), (5, 12), (8, 65535)]:
        params = {"N": count, "T": dwell, "G": -32000}
        concrete = analyze_text(PROBE, params=params, execution_mode="concrete")
        final = [e for e in concrete["events"] if e["kind"] == "play" and e["meta"]["path"] == 0][-1]
        assert end.evaluate(params) == final["t0"]["value"]


def test_ast_serialization_substitution_enclosures_and_solver_constraints():
    z3 = pytest.importorskip("z3")
    n, t = Expr.symbol("N"), Expr.symbol("T").in_range(4, 65535)
    expr = n.loop_count() * (20 + t)
    assert Expr.from_json(expr.to_json()) == expr
    assert expr.bounds({"N": (2, 5), "T": (0, 9)}) == (48, 145)
    replaced = expr.substitute({"N": 3})
    assert replaced.evaluate({"T": 7}) == 81
    symbols = {name: z3.Int(name) for name in expr.free_symbols()}
    solver = z3.Solver()
    solver.add(*expr.constraints_z3(symbols), symbols["N"] == 0, expr.to_z3(symbols) < (1 << 32) * 24)
    assert solver.check() == z3.unsat
    windows = (n - t).minimum(10).maximum(-2) * -3
    bounds = windows.bounds({"N": (0, 12), "T": (4, 7)})
    assert bounds == (-24, 6)
    assert all(bounds[0] <= windows.evaluate({"N": i, "T": j}) <= bounds[1]
               for i in range(13) for j in range(4, 8))


def test_long_symbolic_packet_clock_remains_evaluable_and_serializable():
    dwell = Expr.symbol("T").in_range(4, 65535)
    clock = sum((dwell for _ in range(1200)), Expr.const(0))
    restored = Expr.from_json(clock.to_json())
    assert restored.evaluate({"T": 4}) == 4800
    assert restored.bounds({"T": (4, 10)}) == (4800, 12000)
    assert str(restored) == "1200 * T"


def test_shared_duration_expressions_export_independent_trees_and_keep_source_diagnostics():
    ir = analyze_text("wait $T\nwait $T\nstop")
    first, second = [event for event in ir["events"] if event["kind"] == "wait"]
    assert [item["source"]["line"] for item in ir["diagnostics"] if item["category"] == "unresolved_symbol"] == [1, 2]
    first["duration"]["tree"]["args"][1]["value"] = 0
    for value in (second["duration"], first["t1"]):
        with pytest.raises(ValueError, match="outside"):
            Expr.from_value_json(value).evaluate({"T": 0})


def test_duration_diagnostic_hint_tracks_mutable_lists_and_preserves_full_warnings():
    from dataclasses import replace
    from q1timeline.analysis.values import resolve_duration_arg
    from q1timeline.q1asm.parser import parse_q1asm

    instruction, = parse_q1asm("wait $T").instructions
    arg, = instruction.args
    reference = []
    resolve_duration_arg(arg, {}, reference, source=instruction.source)
    warning, info = reference
    for existing in ([], [info, warning], [replace(warning, related_events=["prior"])], [warning]):
        expected, actual = list(existing), list(existing)
        resolve_duration_arg(arg, {}, expected, source=instruction.source)
        resolve_duration_arg(arg, {}, actual, source=instruction.source, unresolved_index=0)
        assert actual == expected


def test_polynomial_labels_elide_neutral_terms_and_render_signed_constants():
    x = Expr.symbol("X")
    assert x + 3 + (-3) == x
    assert str(x + (-3)) == "X - 3"
    assert str(x - (-3)) == "X + 3"
    assert str(x + -Expr.symbol("Y")) == "X - Y"
    # Legacy n-ary ASTs can still contain an explicit zero.
    tree = Expr.from_json({"op": "+", "args": [x.to_json(), Expr.const(0).to_json()]})
    assert tree.display() == "X"
    assert tree.evaluate({"X": 8}) == 8


def test_wrap_rule_visible_in_analysis_output_json_and_notebook_formulas():
    from q1lens import output

    sequence = {"program": PROBE, "waveforms": {"p": {"index": 0, "data": [1.0] * 20}}}
    model = output(sequence)
    last = model.segments[1].start
    assert str(last) == "N * (20 + T)"
    assert last.display() == "loop_count(N, 0 → 2³²) * (20 + T)"
    assert last.display() == model.to_json()["segments"][1]["start"]["display"]
    assert last.display() in model._repr_html_()
    assert "wf0[t]" in model._repr_html_()
    ir = analyze_text(PROBE)
    play = [e for e in ir["events"] if e["kind"] == "play" and e["meta"]["path"] == 0][-1]
    assert play["t0"]["expr"] == str(last) and play["t0"]["display"] == last.display()
    loop = next(e for e in ir["events"] if e["kind"] == "loop_block")
    assert "0 → 2³²" in loop["label"]

    sequence["program"] = PROBE.replace("move $N,R0", "move 0,R0")
    zero = output(sequence)
    assert zero.segments[0].count.evaluate({}) == 2**32
    assert str(zero.segments[1].start) == "4294967296 * (20 + T)"
    assert "4294967296 (0 → 2³²)" in zero._repr_html_()
    zero_loop = next(e for e in analyze_text(sequence["program"])["events"] if e["kind"] == "loop_block")
    assert zero_loop["meta"]["counter_before"] == 0
    assert zero_loop["meta"]["counter_after"] == 2**32 - 1


def test_public_binding_rejects_unknown_names_and_accepts_rebinding():
    from q1lens import output

    sequence = {"program": ".DEF UNUSED 4\n" + PROBE, "waveforms": {"p": {"index": 0, "data": [1.0]}}}
    model = output(sequence)
    assert model.parameter_names == {"G", "T", "N"}
    params = {"$G": 16384, "N": 2, "T": 4}
    bound = model.bind(params)
    assert bound.bind({"G": 8192}).samples(0, 1, stage="gain_offset")[0] == [0.25]
    for unknown in ["GG", "UNUSED", model.segments[0].iteration]:
        with pytest.raises(ValueError, match="Unknown output parameter"):
            output(sequence, params={**params, unknown: 42})
        with pytest.raises(ValueError, match="Unknown output parameter"):
            bound.bind({unknown: 42})

    dotted = output({"program": "wait $scenario.duration\nstop"})
    assert dotted.parameter_names == {"scenario.duration"}
    assert dotted.bind({"scenario.duration": 12}).duration.evaluate({"scenario.duration": 12}) == 12
    assert Expr.parse("4 + $scenario.duration").evaluate({"scenario.duration": 12}) == 16


def test_affine_setter_proof_matches_every_real_latch_and_pending_exit():
    src = ("move 5,R0\nmove -6000,R1\nnop\nbody: set_awg_gain R1,R1\nplay 0,0,20\n"
           "sub R1,1000,R1\nnop\nset_awg_offs R1,R1\nloop R0,@body\nupd_param 4\nstop")
    ir = analyze_text(src)
    loop, = [e["meta"] for e in ir["events"] if e["kind"] == "loop_block"]
    concrete = analyze_text(src, execution_mode="concrete")
    for event in [e for e in ir["events"] if e["id"] in loop["uncertain_setter_ids"]]:
        expected = [e["meta"]["value"] for e in concrete["events"]
                    if e["kind"] == "latched_state_pending" and e["source"]["line"] == event["source"]["line"]]
        coefficients = loop["latched_affine"][event["id"]]
        assert [[part["intercept"] + i * part["slope"] for part in coefficients] for i in range(5)] == expected
    for result in [ir, concrete]:
        update, = [e for e in result["events"] if e["kind"] == "upd_param"]
        assert update["meta"]["applied_state"]["awg_offset"] == [-11000, -11000]


def test_symbolic_count_ramp_is_proved_over_the_legal_signed_latch_domain():
    source = ("move N,R0\nmove 1000,R1\nnop\nbody: set_awg_gain R1,R1\nplay 0,0,T\n"
              "add R1,1000,R1\nnop\nloop R0,@body\nwait 4\nstop")
    ir = analyze_text(source)
    loop, = [e["meta"] for e in ir["events"] if e["kind"] == "loop_block"]
    coeff, = loop["latched_affine"].values()
    assert coeff == [{"intercept": 1000, "slope": 1000}] * 2
    wait, = [e for e in ir["events"] if e["kind"] == "wait"]
    time = Expr.from_value_json(wait["t0"])
    assert time.evaluate({"N": 32, "T": 20}) == 640
    with pytest.raises(ValueError):
        time.evaluate({"N": 33, "T": 20})

    fixed = source.replace("move N,R0", "move 5,R0").replace("play 0,0,T", "set_ph_delta 100000000\nplay 0,0,T")
    fixed_loop = next(e for e in analyze_text(fixed)["events"] if e["kind"] == "loop_block")
    assert list(fixed_loop["meta"]["latched_affine"].values()) == [coeff]


def test_waveform_indices_and_signed_register_latches_keep_integer_domains():
    src = ("move G,R0\nnop\nadd R0,1,R0\nnop\nset_awg_gain R0,R0\nplay $W,$W,4\nstop")
    ir = analyze_text(src)
    event = next(e for e in ir["events"] if e["kind"] == "play")
    gain = Expr.from_value_json(event["meta"]["applied_state"]["awg_gain"][0])
    assert gain.evaluate({"G": 0xFFFFFFFE}) == -1
    index = Expr.from_value_json(event["meta"]["waveform_index_expr"])
    assert index.evaluate({"W": 20}) == 20
    with pytest.raises(ValueError):
        index.evaluate({"W": 1024})
