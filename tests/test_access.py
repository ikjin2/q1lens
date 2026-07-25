from __future__ import annotations

from qbstimeline._access import get_value, unwrap


class DataPropertyRaises:
    name = "untimed schedule"

    @property
    def data(self):
        raise RuntimeError("`data` dict unavailable on schedule with untimed operations")


class SchedulablesPropertyRaises:
    @property
    def schedulables(self):
        raise RuntimeError("`schedulables` dict unavailable on schedule with untimed operations")


def test_unwrap_falls_back_to_object_when_data_property_raises() -> None:
    value = DataPropertyRaises()

    assert unwrap(value) is value
    assert get_value(value, "name") == "untimed schedule"


def test_get_value_returns_default_when_attribute_property_raises() -> None:
    value = SchedulablesPropertyRaises()

    assert get_value(value, "schedulables", {}) == {}
