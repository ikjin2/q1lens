from __future__ import annotations

from collections.abc import Sequence

from qbstimeline.cli import main as _qbstimeline_main


def main(argv: Sequence[str] | None = None) -> int:
    return _qbstimeline_main(argv, prog="q1lens")
