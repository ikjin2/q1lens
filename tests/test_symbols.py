from __future__ import annotations

from qbstimeline import annotate, sym
from qbstimeline.symbols import AnnotatedOperation, SymbolicValue, symbolic_values_to_ir


def test_sym_time_creates_stable_value_id() -> None:
    value = sym.time("T_TOTAL", 40e-9)

    assert value == SymbolicValue(
        id="value:t_total",
        label="T_TOTAL",
        value=40e-9,
        unit="s",
        kind="duration",
    )


def test_sym_amp_creates_amp_value() -> None:
    value = sym.amp("AMP_X", 0.32)

    assert value.id == "value:amp_x"
    assert value.label == "AMP_X"
    assert value.value == 0.32
    assert value.unit is None
    assert value.kind == "amplitude"


def test_annotate_dict_operation_returns_copy_with_metadata() -> None:
    operation = {"name": "X(q0)"}
    duration = sym.time("T_TOTAL", 40e-9)

    annotated = annotate(operation, duration=duration)

    assert annotated is not operation
    assert annotated["name"] == "X(q0)"
    assert annotated["__qbstimeline_annotations__"]["duration"] == duration
    assert "__qbstimeline_annotations__" not in operation


def test_annotate_object_attaches_metadata_when_possible() -> None:
    class Operation:
        name = "X(q0)"

    operation = Operation()
    duration = sym.time("T_TOTAL", 40e-9)

    annotated = annotate(operation, duration=duration)

    assert annotated is operation
    assert operation.__qbstimeline_annotations__ == {"duration": duration}


def test_annotate_falls_back_to_wrapper_for_slot_objects() -> None:
    class Operation:
        __slots__ = ("name",)

        def __init__(self) -> None:
            self.name = "X(q0)"

    operation = Operation()
    duration = sym.time("T_TOTAL", 40e-9)

    annotated = annotate(operation, duration=duration)

    assert isinstance(annotated, AnnotatedOperation)
    assert annotated.operation is operation
    assert annotated.annotations == {"duration": duration}


def test_symbolic_values_to_ir_deduplicates_by_id() -> None:
    values = [
        sym.time("T_TOTAL", 40e-9),
        sym.time("T_TOTAL", 40e-9),
        sym.amp("AMP_X", 0.32),
    ]

    assert symbolic_values_to_ir(values) == [
        {
            "id": "value:t_total",
            "label": "T_TOTAL",
            "value": 40e-9,
            "unit": "s",
            "kind": "duration",
        },
        {
            "id": "value:amp_x",
            "label": "AMP_X",
            "value": 0.32,
            "unit": None,
            "kind": "amplitude",
        },
    ]
