from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType


class Q1TimelineBridgeError(RuntimeError):
    """Raised when the bundled q1timeline bridge cannot locate q1timeline."""


def run_q1timeline_cli(argv: Sequence[str] | None = None) -> int:
    try:
        cli = import_q1timeline_cli()
    except Q1TimelineBridgeError as exc:
        print(f"qbstimeline q1timeline: {exc}", file=sys.stderr)
        return 2

    try:
        result = cli.main(list(argv or []))
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        if exc.code is None:
            return 0
        print(exc.code, file=sys.stderr)
        return 2
    return int(result or 0)


def import_q1timeline_cli() -> ModuleType:
    bundled_cli = _try_import_from_candidate(_bundled_source_root())
    if bundled_cli is not None:
        return bundled_cli

    try:
        return importlib.import_module("q1timeline.cli")
    except ModuleNotFoundError as exc:
        if exc.name != "q1timeline":
            raise

    for candidate in _env_source_roots():
        cli = _try_import_from_candidate(candidate)
        if cli is not None:
            return cli

    raise Q1TimelineBridgeError(
        "q1timeline is not importable. Install q1timeline in this Python environment "
        "or set QBSTIMELINE_Q1TIMELINE_PATH to the q1timeline source directory."
    )


def _bundled_source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_source_roots() -> list[Path]:
    candidates: list[Path] = []
    for name in ("QBSTIMELINE_Q1TIMELINE_PATH", "QBSTIMELINE_Q1TIMELINE_SRC"):
        raw = os.environ.get(name)
        if raw:
            candidates.append(Path(raw).expanduser())
    return candidates


def _try_import_from_candidate(candidate: Path) -> ModuleType | None:
    source_root = _normalize_source_root(candidate)
    if source_root is None:
        return None
    source_root_text = str(source_root)
    if source_root_text in sys.path:
        sys.path.remove(source_root_text)
    sys.path.insert(0, source_root_text)
    try:
        return importlib.import_module("q1timeline.cli")
    except ModuleNotFoundError as exc:
        if exc.name != "q1timeline":
            raise
        return None


def _normalize_source_root(candidate: Path) -> Path | None:
    candidate = candidate.resolve()
    if (candidate / "q1timeline" / "cli.py").is_file():
        return candidate
    if candidate.name == "q1timeline" and (candidate / "cli.py").is_file():
        return candidate.parent
    if (candidate / "src" / "q1timeline" / "cli.py").is_file():
        return candidate / "src"
    return None
