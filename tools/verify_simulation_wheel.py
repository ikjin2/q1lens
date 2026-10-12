"""Verify a built wheel in a clean environment using copied simulation inputs."""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import venv


def verify_installed(examples: Path, output: Path) -> None:
    import q1lens
    import q1timeline
    from q1timeline.analysis.api import analyze_documents
    from q1timeline.project import load_project_config
    from q1timeline.sequence import load_sequence_data

    paths = {module.__name__: str(Path(module.__file__).resolve()) for module in (q1lens, q1timeline)}
    for path in paths.values():
        assert Path(path).is_relative_to(Path(sys.prefix).resolve()), path
        assert "site-packages" in Path(path).parts, path
    assert importlib.util.find_spec("q1simulator") is None, "Unexpected q1simulator dependency"
    assert sys.flags.isolated, "Installed checks require isolated Python (-I)"
    environment = {
        "python": sys.version, "executable": sys.executable, "imports": paths,
        "q1simulator_available": False,
        "distributions": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
    }
    (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")
    verifier = runpy.run_path(str(examples / "verify.py"))
    results = []
    for case in verifier["CASES"]:
        config = load_project_config(examples / case / "q1timeline.yml")
        documents = {str(seq.file): seq.file.read_text(encoding="utf-8") for seq in config.sequencers}
        sequences = {seq.id: load_sequence_data(seq.sequence_json) for seq in config.sequencers if seq.sequence_json}
        ir = analyze_documents(
            documents, params=config.params, execution_mode="simulation", max_steps=config.analysis.max_steps,
            alignment_mode=config.alignment_mode, simulation=config.simulation, sequences=sequences,
        )
        results.append({"case": case, "passed": True, "evidence": verifier["check"](case, ir)})
    (output / "api-report.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    assert verifier["main"]() == 0, "Installed CLI verification failed"
    shutil.copy2(examples / ".verification" / "report.json", output / "cli-report.json")
    for case in verifier["CASES"]:
        shutil.copy2(examples / case / ".q1timeline" / "timeline_ir.json", output / f"{case}.json")
    print("PASS: installed API (5 cases), CLI (5 cases x 2 runs), isolated imports, no q1simulator")


def main() -> int:
    if not __debug__:
        raise RuntimeError("Verification requires Python assertions; remove -O and PYTHONOPTIMIZE.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", help="Wheel path or glob resolving to exactly one file")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--installed-inputs", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.installed_inputs:
        verify_installed(args.installed_inputs, output)
        return 0
    (output / "report.json").write_text(json.dumps({"passed": False, "wheel": args.wheel}, indent=2) + "\n", encoding="utf-8")
    if not args.wheel:
        parser.error("--wheel is required")
    wheels = [Path(path).resolve() for path in glob.glob(args.wheel) if Path(path).is_file()]
    if len(wheels) != 1:
        parser.error(f"--wheel must resolve to exactly one file; found {len(wheels)}")
    wheel = wheels[0]
    summary = {"passed": False, "wheel": str(wheel), "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    (output / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    examples_source = Path(__file__).resolve().parents[1] / "examples" / "simulation-verification"
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}}
    try:
        # Only this owned temporary directory is removed. Evidence stays in --out.
        with tempfile.TemporaryDirectory(prefix="wheel-check-", dir=output) as temporary:
            root = Path(temporary)
            venv.EnvBuilder(with_pip=True).create(root / "venv")
            python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            with (output / "install.log").open("w", encoding="utf-8") as log:
                subprocess.run([str(python), "-I", "-m", "pip", "install", str(wheel)], cwd=root,
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            examples = root / "inputs"
            shutil.copytree(examples_source, examples,
                            ignore=shutil.ignore_patterns(".q1timeline", ".verification", "__pycache__"))
            worker = root / "verify_installed.py"
            shutil.copy2(__file__, worker)
            with (output / "verification.log").open("w", encoding="utf-8") as log:
                subprocess.run([str(python), "-I", str(worker), "--installed-inputs", str(examples), "--out", str(output)],
                               cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        summary["passed"] = True
        print(f"PASS wheel verification: {output}")
        return 0
    except (OSError, subprocess.SubprocessError) as error:
        summary["error"] = str(error)
        print(f"FAIL wheel verification: {error}; see {output}", file=sys.stderr)
        return 1
    finally:
        (output / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
