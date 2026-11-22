"""Verify a built wheel and optionally its companion VSIX in an isolated installation."""
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


def verify_demo(examples: Path, output: Path) -> None:
    cli = Path(sys.executable).with_name("q1lens.exe" if os.name == "nt" else "q1lens")
    for name, args, status, code in (
        ("queue", ["verify", "queue.q1asm", "--include-replay"], "counterexample", 1),
        ("equivalent", ["equiv", "before.q1asm", "optimized.q1asm"], "proved", 0),
        ("changed", ["equiv", "before.q1asm", "changed.q1asm"], "counterexample", 1),
        ("limited", ["verify", "alignment/q1timeline.yml", "--alignment", "--max-cases", "1"], "unknown", 2),
    ):
        run = subprocess.run([str(cli), *args, "--json"], cwd=examples, capture_output=True, text=True, timeout=45)
        (output / f"demo-{name}.json").write_text(run.stdout, encoding="utf-8")
        assert run.returncode == code, (args, run.returncode, run.stderr, run.stdout[:500])
        report = json.loads(run.stdout)
        assert report["status"] == status, report["status"]
        check, = report["checks"]
        if name == "queue":
            witness = check["counterexample"]
            event, = [event for event in witness["timeline"]["events"] if event["id"] == witness["event_id"]]
            assert check["safe_prefix"] == {"name": "N", "min": 1, "max": 37}
            assert (witness["inputs"], event["kind"], event["t0"]["value"]) == ({"N": 38}, "underflow_warning", 1188)
        elif name == "equivalent":
            assert check["checked_cases"] == 8
        elif name == "changed":
            witness = check["counterexample"]
            observations = witness["observations"]
            assert witness["inputs"] == {"N": 7}
            assert observations["after"]["end_ns"] - observations["before"]["end_ns"] == 4
        elif name == "limited":
            assert check["kind"] == "alignment" and check["checked_cases"] == 1
    print("PASS: installed verify/equiv commands, exits 0/1/2, queue at 1188 ns and RT difference of 4 ns")


def main() -> int:
    if not __debug__:
        raise RuntimeError("Verification requires Python assertions; remove -O and PYTHONOPTIMIZE.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", help="Wheel path or glob resolving to exactly one file")
    parser.add_argument("--vsix", type=Path, help="Install this VSIX in a fresh VS Code profile and open the queue replay")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--installed-inputs", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.installed_inputs:
        verify_installed(args.installed_inputs / "simulation-verification", output)
        verify_demo(args.installed_inputs / "verification-demo", output)
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
    repository = Path(__file__).resolve().parents[1]
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
            for name in ("simulation-verification", "verification-demo"):
                shutil.copytree(repository / "examples" / name, examples / name,
                                ignore=shutil.ignore_patterns(".q1timeline", ".verification", "__pycache__"))
            worker = root / "verify_installed.py"
            shutil.copy2(__file__, worker)
            with (output / "verification.log").open("w", encoding="utf-8") as log:
                subprocess.run([str(python), "-I", str(worker), "--installed-inputs", str(examples), "--out", str(output)],
                               cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            if args.vsix:
                vsix = args.vsix.resolve(strict=True)
                summary.update(vsix=str(vsix), vsix_sha256=hashlib.sha256(vsix.read_bytes()).hexdigest())
                with (output / "installed-vsix.log").open("w", encoding="utf-8") as log:
                    subprocess.run(["node", "out/test/runTest.js", str(vsix)], cwd=repository / "vscode-extension",
                                   env={**environment, "PYTHON": str(python), "Q1LENS_EXTENSION_HOST_OUTPUT": str(root / "host")},
                                   stdout=log, stderr=subprocess.STDOUT, check=True, timeout=360)
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
