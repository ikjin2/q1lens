"""Rebuild loop fixtures, check real editor/browser behavior, then write the report."""
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import subprocess
import sys


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for the VS Code and browser checks.")
    output = root / "output" / "loop-selection-review"
    output.mkdir(parents=True, exist_ok=True)
    run = {"started_at": datetime.now(timezone.utc).isoformat(), "stages": []}
    manifest = output / "run-status.json"
    manifest.write_text(json.dumps(run, indent=2), encoding="utf-8")
    stages = [
        [sys.executable, str(here / "verify.py")],
        [node, str(here / "verify-host.cjs")],
        [node, str(here / "verify-browser.cjs")],
    ]
    exit_code = 0
    for command in stages:
        stage = {"name": Path(command[1]).name, "started_at": datetime.now(timezone.utc).isoformat()}
        run["stages"].append(stage)
        manifest.write_text(json.dumps(run, indent=2), encoding="utf-8")
        result = subprocess.run(command, cwd=root, check=False)
        stage.update(exit_code=result.returncode, finished_at=datetime.now(timezone.utc).isoformat())
        manifest.write_text(json.dumps(run, indent=2), encoding="utf-8")
        if result.returncode:
            exit_code = result.returncode
            break
    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest.write_text(json.dumps(run, indent=2), encoding="utf-8")
    report = subprocess.run([sys.executable, str(here / "build-report.py")], cwd=root, check=False)
    return exit_code or report.returncode


if __name__ == "__main__":
    raise SystemExit(main())
