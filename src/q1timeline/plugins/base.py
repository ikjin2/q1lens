from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SemanticAnnotation:
    id: str
    kind: str
    label: str
    event_ids: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    timing_override: bool = False


@dataclass(frozen=True)
class PluginResult:
    annotations: list[SemanticAnnotation] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    groups: list[dict[str, Any]] = field(default_factory=list)
    arrows: list[dict[str, Any]] = field(default_factory=list)


class SemanticPlugin(Protocol):
    name: str

    def apply(self, ir: dict[str, Any]) -> PluginResult:
        ...


def apply_plugins(ir: dict[str, Any], plugins: list[SemanticPlugin]) -> dict[str, Any]:
    if not plugins:
        return deepcopy(ir)

    result = deepcopy(ir)
    semantic = _semantic_bucket(result)
    for plugin in plugins:
        plugin_result = plugin.apply(deepcopy(ir))
        semantic["annotations"].extend(_annotation_to_dict(annotation) for annotation in plugin_result.annotations)
        semantic["aliases"].update(plugin_result.aliases)
        semantic["groups"].extend(deepcopy(plugin_result.groups))
        semantic["arrows"].extend(deepcopy(plugin_result.arrows))
    return result


def _semantic_bucket(ir: dict[str, Any]) -> dict[str, Any]:
    semantic = ir.setdefault("semantic", {})
    semantic.setdefault("annotations", [])
    semantic.setdefault("aliases", {})
    semantic.setdefault("groups", [])
    semantic.setdefault("arrows", [])
    return semantic


def _annotation_to_dict(annotation: SemanticAnnotation) -> dict[str, Any]:
    return asdict(annotation)
