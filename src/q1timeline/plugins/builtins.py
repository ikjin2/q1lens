from __future__ import annotations

from typing import Any

from q1timeline.plugins.base import PluginResult, SemanticAnnotation, SemanticPlugin
from q1timeline.q1asm.instruction_table import get_instruction_spec


_NO_MARKER = object()


class MarkerPulseRecognizer:
    name = "marker_pulse"

    def apply(self, ir: dict[str, Any]) -> PluginResult:
        marker_streams: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
        packet_index: dict[tuple[str | None, str, str], int] = {}
        for event in ir.get("events", []):
            marker_value = _marker_value(event)
            if marker_value is _NO_MARKER:
                continue
            key = (_event_sequencer_id(event), "marker")
            marker_events = marker_streams.setdefault(key, [])
            packet_id = _event_rt_packet_id(event)
            if packet_id is not None:
                dedupe_key = (key[0], key[1], packet_id)
                existing_index = packet_index.get(dedupe_key)
                if existing_index is not None:
                    if event.get("kind") == "marker_state" and marker_events[existing_index].get("kind") != "marker_state":
                        marker_events[existing_index] = event
                    continue
                packet_index[dedupe_key] = len(marker_events)
            marker_events.append(event)
        annotations: list[SemanticAnnotation] = []
        for marker_events in marker_streams.values():
            current_marker_value: Any = None
            pulse_start: dict[str, Any] | None = None
            for event in marker_events:
                marker_value = _marker_value(event)
                if _is_high_marker_value(marker_value):
                    if not _is_high_marker_value(current_marker_value):
                        pulse_start = event
                    current_marker_value = marker_value
                    continue
                if marker_value == 0:
                    if _is_high_marker_value(current_marker_value) and pulse_start is not None:
                        annotations.append(
                            SemanticAnnotation(
                                id=f"marker_pulse:{len(annotations)}",
                                kind="semantic_label",
                                label="trigger pulse",
                                event_ids=[pulse_start["id"], event["id"]],
                                details={"field": "marker", "value": _marker_value(pulse_start)},
                            )
                        )
                    else:
                        annotations.append(_marker_state_annotation(event, marker_value, len(annotations)))
                    current_marker_value = marker_value
                    pulse_start = None
                    continue
                annotations.append(_marker_state_annotation(event, marker_value, len(annotations)))
                current_marker_value = marker_value
                pulse_start = None
            if _is_high_marker_value(current_marker_value) and pulse_start is not None:
                pulse_start_value = _marker_value(pulse_start)
                annotations.append(
                    SemanticAnnotation(
                        id=f"marker_state:{len(annotations)}",
                        kind="semantic_label",
                        label=f"marker {pulse_start_value}",
                        event_ids=[pulse_start["id"]],
                        details={"field": "marker", "value": pulse_start_value},
                    )
                )
        return PluginResult(annotations=annotations)


class ReadoutAcquireRecognizer:
    name = "readout_acquire"

    def apply(self, ir: dict[str, Any]) -> PluginResult:
        acquire_ids = [event["id"] for event in ir.get("events", []) if event.get("kind") == "acquire"]
        if not acquire_ids:
            return PluginResult()
        return PluginResult(
            groups=[
                {
                    "id": "readout_acquire:0",
                    "label": "readout/acquire",
                    "event_ids": acquire_ids,
                }
            ]
        )


class FeedbackAnnotationRecognizer:
    name = "feedback_annotation"

    LABELS = {
        "fb_pop_data": "feedback pop",
        "fb_pull_data": "feedback pull",
        "fb_com_data": "feedback commit",
        "fb_acq_iq_id": "feedback acquire id",
        "fb_acq_iq_shift": "feedback acquire shift",
    }
    LABEL_PARTS = {
        "acq": "acquisition",
        "tb": "timebin",
        "llp": "LLP",
        "ttls": "TTLs",
        "tdc": "TDC",
        "tdelta": "time delta",
        "com": "commit",
        "cfg": "config",
    }

    def apply(self, ir: dict[str, Any]) -> PluginResult:
        annotations = []
        for event in ir.get("events", []):
            if event.get("kind") != "q1_issue" or not isinstance(event.get("meta"), dict):
                continue
            op = event["meta"].get("op")
            label = self._label_for_op(op)
            if label is None:
                continue
            annotations.append(
                SemanticAnnotation(
                    id=f"feedback:{len(annotations)}",
                    kind="annotation",
                    label=label,
                    event_ids=[event["id"]],
                    details={"op": op},
                )
            )
        return PluginResult(annotations=annotations)

    @classmethod
    def _label_for_op(cls, op: Any) -> str | None:
        if not isinstance(op, str):
            return None
        label = cls.LABELS.get(op)
        if label is not None:
            return label
        spec = get_instruction_spec(op)
        if not op.startswith("fb_") or spec.category == "unknown":
            return None
        return "feedback " + " ".join(cls.LABEL_PARTS.get(part, part) for part in op[3:].split("_"))


def builtin_plugins() -> list[SemanticPlugin]:
    return [
        MarkerPulseRecognizer(),
        ReadoutAcquireRecognizer(),
        FeedbackAnnotationRecognizer(),
    ]


def _marker_state_annotation(event: dict[str, Any], marker_value: Any, index: int) -> SemanticAnnotation:
    return SemanticAnnotation(
        id=f"marker_state:{index}",
        kind="semantic_label",
        label=f"marker {marker_value}",
        event_ids=[event["id"]],
        details={"field": "marker", "value": marker_value},
    )


def _marker_value(event: dict[str, Any]) -> Any:
    meta = event.get("meta")
    if not isinstance(meta, dict):
        return _NO_MARKER
    if event.get("kind") == "marker_state" and meta.get("field") == "marker" and "value" in meta:
        return meta["value"]
    applied_state = meta.get("applied_state")
    if isinstance(applied_state, dict) and "marker" in applied_state:
        return applied_state["marker"]
    return _NO_MARKER


def _is_high_marker_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


def _event_rt_packet_id(event: dict[str, Any]) -> str | None:
    meta = event.get("meta")
    if not isinstance(meta, dict):
        return None
    packet_id = meta.get("rt_packet_id")
    if packet_id is None:
        return None
    return str(packet_id)


def _event_sequencer_id(event: dict[str, Any]) -> str | None:
    sequencer_id = event.get("sequencer_id")
    if sequencer_id is not None:
        return str(sequencer_id)
    event_id = event.get("id")
    if isinstance(event_id, str) and ":" in event_id:
        return event_id.split(":", 1)[0]
    return None
