from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


def test_python_project_uses_q1lens_as_distribution_name() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "q1lens"
    assert pyproject["project"]["scripts"]["q1lens"] == "q1lens.cli:main"
    assert pyproject["project"]["scripts"]["qbstimeline"] == "qbstimeline.cli:main"
    assert pyproject["project"]["scripts"]["q1timeline"] == "q1timeline.cli:main"


def test_q1lens_import_reexports_qbstimeline_helpers() -> None:
    import q1lens
    import qbstimeline

    assert q1lens.sym is qbstimeline.sym
    assert q1lens.annotate is qbstimeline.annotate


def test_q1lens_module_cli_uses_q1lens_prog_name() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "q1lens", "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("usage: q1lens ")
    assert "Q1Lens" in result.stdout
