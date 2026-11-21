from typing import TYPE_CHECKING

from q1timeline.analysis.values import (
    Concrete,
    Range,
    RuntimeDependent,
    Symbolic,
    Unknown,
    Value,
)

if TYPE_CHECKING:
    from q1timeline.analysis.api import analyze_documents, analyze_text


def __getattr__(name: str):
    if name in {"analyze_documents", "analyze_text"}:
        from q1timeline.analysis import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "analyze_documents",
    "analyze_text",
    "Concrete",
    "Range",
    "RuntimeDependent",
    "Symbolic",
    "Unknown",
    "Value",
]
