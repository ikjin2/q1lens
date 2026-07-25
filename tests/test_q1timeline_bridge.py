from __future__ import annotations

import sys
from pathlib import Path

from qbstimeline.cli import main


def _clear_q1timeline_modules() -> None:
    for name in list(sys.modules):
        if name == "q1timeline" or name.startswith("q1timeline."):
            sys.modules.pop(name, None)


def test_q1timeline_subcommand_uses_bundled_cli_before_configured_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fake_src = tmp_path / "q1timeline_checkout" / "src"
    fake_package = fake_src / "q1timeline"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text('__version__ = "fake"\n', encoding="utf-8")
    (fake_package / "cli.py").write_text(
        """
from __future__ import annotations

def main(argv=None):
    print("delegated:" + "|".join(argv or []))
    return 17
""".lstrip(),
        encoding="utf-8",
    )

    original_sys_path = list(sys.path)
    _clear_q1timeline_modules()
    monkeypatch.setenv("QBSTIMELINE_Q1TIMELINE_PATH", str(fake_src))
    try:
        exit_code = main(["q1timeline", "--version"])
    finally:
        sys.path[:] = original_sys_path
        _clear_q1timeline_modules()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "q1timeline 0.1.1" in captured.out
    assert "delegated:" not in captured.out


def test_q1timeline_subcommand_uses_bundled_cli_before_importable_checkout(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fake_src = tmp_path / "q1timeline_checkout" / "src"
    fake_package = fake_src / "q1timeline"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text('__version__ = "fake"\n', encoding="utf-8")
    (fake_package / "cli.py").write_text(
        """
from __future__ import annotations

def main(argv=None):
    print("hijacked:" + "|".join(argv or []))
    return 17
""".lstrip(),
        encoding="utf-8",
    )

    original_sys_path = list(sys.path)
    _clear_q1timeline_modules()
    monkeypatch.syspath_prepend(str(fake_src))
    try:
        exit_code = main(["q1timeline", "--version"])
    finally:
        sys.path[:] = original_sys_path
        _clear_q1timeline_modules()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "q1timeline 0.1.1" in captured.out
    assert "hijacked:" not in captured.out


def test_q1timeline_subcommand_forwards_help_to_bundled_cli(
    capsys,
) -> None:
    original_sys_path = list(sys.path)
    _clear_q1timeline_modules()
    try:
        exit_code = main(["q1timeline", "--help"])
    finally:
        sys.path[:] = original_sys_path
        _clear_q1timeline_modules()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Q1ASM Live Timeline Debugger" in captured.out
