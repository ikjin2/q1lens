from __future__ import annotations

import hashlib
import json
import warnings
import zipfile
from pathlib import Path, PurePosixPath

from qbstimeline.cli import main


def test_logically_identical_inputs_create_byte_identical_bundles(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    qbs_ir = _qbs_ir()
    (first / "qbs.json").write_text(json.dumps(qbs_ir), encoding="utf-8")
    (second / "qbs.json").write_text(
        json.dumps(qbs_ir, indent=4, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for directory in (first, second):
        (directory / "experiment.ipynb").write_bytes(b'{"cells": []}\n')

    first_bundle = first / "bundle.zip"
    second_bundle = second / "bundle.zip"
    assert _build_bundle(first / "qbs.json", first_bundle, first / "experiment.ipynb") == 0
    assert _build_bundle(second / "qbs.json", second_bundle, second / "experiment.ipynb") == 0

    assert first_bundle.read_bytes() == second_bundle.read_bytes()


def test_manifest_covers_payload_hashes_sizes_and_deterministic_zip_metadata(tmp_path: Path) -> None:
    bundle_path = _valid_bundle(tmp_path)

    with zipfile.ZipFile(bundle_path) as bundle:
        assert bundle.namelist() == sorted(bundle.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in bundle.infolist())
        assert all(info.compress_type == zipfile.ZIP_STORED for info in bundle.infolist())
        assert all(info.create_system == 3 for info in bundle.infolist())
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["schema"] == "q1lens.diagnostic-bundle"
        assert manifest["version"] == 1
        assert "manifest.json" not in {entry["path"] for entry in manifest["members"]}
        assert {entry["role"] for entry in manifest["members"]} >= {
            "qbs_ir",
            "diagnostics_report",
            "versions",
            "q1asm",
        }
        for entry in manifest["members"]:
            data = bundle.read(entry["path"])
            assert entry["size"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 0


def test_verify_bundle_detects_tampered_payload(tmp_path: Path, capsys) -> None:
    original = _valid_bundle(tmp_path)
    tampered = tmp_path / "tampered.zip"
    _rewrite_bundle(original, tampered, replacements={"qbs_ir.json": b"{}\n"})

    assert main(["verify-bundle", "--bundle", str(tampered)]) == 1
    assert "SHA-256 mismatch for qbs_ir.json" in capsys.readouterr().err


def test_verify_bundle_rejects_extra_member(tmp_path: Path, capsys) -> None:
    bundle_path = _valid_bundle(tmp_path)
    with zipfile.ZipFile(bundle_path, "a", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("extra.txt", "not declared")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert "extra archive member not declared by manifest: extra.txt" in capsys.readouterr().err


def test_verify_bundle_rejects_duplicate_member(tmp_path: Path, capsys) -> None:
    bundle_path = _valid_bundle(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(bundle_path, "a", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("qbs_ir.json", "{}\n")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert "duplicate archive member: qbs_ir.json" in capsys.readouterr().err


def test_verify_bundle_rejects_unsafe_member(tmp_path: Path, capsys) -> None:
    bundle_path = _valid_bundle(tmp_path)
    with zipfile.ZipFile(bundle_path, "a", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("../escape.txt", "unsafe")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert "unsafe archive member path: ../escape.txt" in capsys.readouterr().err


def test_verify_bundle_rejects_case_insensitive_member_collision(tmp_path: Path, capsys) -> None:
    bundle_path = _valid_bundle(tmp_path)
    with zipfile.ZipFile(bundle_path, "a", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("q1asm/SEQ0.q1asm", "stop\n")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert (
        "case-insensitive archive member collision: q1asm/SEQ0.q1asm, q1asm/seq0.q1asm"
        in capsys.readouterr().err
    )


def test_verify_bundle_rejects_trailing_dot_or_space_segments(tmp_path: Path, capsys) -> None:
    bundle_path = _valid_bundle(tmp_path)
    with zipfile.ZipFile(bundle_path, "a", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("q1asm/trailing.", "stop\n")
        bundle.writestr("q1asm/trailing-space ", "stop\n")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    stderr = capsys.readouterr().err
    assert "unsafe archive member path: q1asm/trailing." in stderr
    assert "unsafe archive member path: q1asm/trailing-space " in stderr


def test_malicious_and_colliding_sequencer_ids_get_distinct_safe_names(tmp_path: Path) -> None:
    qbs_ir = _qbs_ir()
    qbs_ir["q1asm_by_sequencer"] = {
        "seq0": "stop\n",
        "../seq": "wait 4\nstop\n",
        "..\\seq": "wait 8\nstop\n",
        "q1asm/../../seq": "wait 12\nstop\n",
        "SEQ": "wait 16\nstop\n",
        "seq": "wait 20\nstop\n",
    }
    ir_path = tmp_path / "qbs.json"
    bundle_path = tmp_path / "bundle.zip"
    ir_path.write_text(json.dumps(qbs_ir), encoding="utf-8")

    assert _build_bundle(ir_path, bundle_path) == 0

    with zipfile.ZipFile(bundle_path) as bundle:
        q1asm_names = [name for name in bundle.namelist() if name.startswith("q1asm/")]
        assert len(q1asm_names) == 6
        assert len(set(q1asm_names)) == 6
        assert len({name.casefold() for name in q1asm_names}) == 6
        assert "q1asm/seq0.q1asm" in q1asm_names
        assert all("\\" not in name and ".." not in PurePosixPath(name).parts for name in q1asm_names)
        assert {bundle.read(name).decode("utf-8") for name in q1asm_names} == {
            "stop\n",
            "wait 4\nstop\n",
            "wait 8\nstop\n",
            "wait 12\nstop\n",
            "wait 16\nstop\n",
            "wait 20\nstop\n",
        }
    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 0


def test_missing_or_non_file_schedule_fails_without_writing_bundle(tmp_path: Path) -> None:
    ir_path = tmp_path / "qbs.json"
    ir_path.write_text(json.dumps(_qbs_ir()), encoding="utf-8")
    schedule_directory = tmp_path / "schedule-dir"
    schedule_directory.mkdir()

    for index, schedule_path in enumerate((tmp_path / "missing.py", schedule_directory)):
        bundle_path = tmp_path / f"bundle-{index}.zip"
        assert _build_bundle(ir_path, bundle_path, schedule_path) == 2
        assert not bundle_path.exists()


def test_schedule_member_preserves_basename_and_extension(tmp_path: Path) -> None:
    ir_path = tmp_path / "qbs.json"
    schedule_path = tmp_path / "experiment.ipynb"
    bundle_path = tmp_path / "bundle.zip"
    ir_path.write_text(json.dumps(_qbs_ir()), encoding="utf-8")
    schedule_path.write_text('{"cells": []}\n', encoding="utf-8")

    assert _build_bundle(ir_path, bundle_path, schedule_path) == 0

    with zipfile.ZipFile(bundle_path) as bundle:
        assert "schedule/experiment.ipynb" in bundle.namelist()
        assert "schedule.py" not in bundle.namelist()


def test_verify_bundle_returns_runtime_error_for_missing_path(tmp_path: Path) -> None:
    assert main(["verify-bundle", "--bundle", str(tmp_path / "missing.zip")]) == 2


def _valid_bundle(tmp_path: Path) -> Path:
    ir_path = tmp_path / "qbs.json"
    bundle_path = tmp_path / "bundle.zip"
    ir_path.write_text(json.dumps(_qbs_ir()), encoding="utf-8")
    assert _build_bundle(ir_path, bundle_path) == 0
    return bundle_path


def _build_bundle(ir_path: Path, bundle_path: Path, schedule_path: Path | None = None) -> int:
    args = ["diagnose", "--ir", str(ir_path), "--bundle", str(bundle_path)]
    if schedule_path is not None:
        args.extend(["--schedule", str(schedule_path)])
    return main(args)


def _rewrite_bundle(
    source: Path,
    destination: Path,
    *,
    replacements: dict[str, bytes],
) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as rewritten:
        for info in original.infolist():
            rewritten.writestr(info.filename, replacements.get(info.filename, original.read(info)))


def _qbs_ir() -> dict:
    return {
        "version": "0.1.0",
        "schedule": {"name": "bundle demo"},
        "operations": [],
        "symbolic_pulses": [],
        "q1asm_programs": [{"sequencer_id": "seq0", "file": "q1asm/seq0.q1asm"}],
        "q1asm_by_sequencer": {"seq0": "wait_sync 4\nstop\n"},
        "q1asm_provenance": [],
    }
