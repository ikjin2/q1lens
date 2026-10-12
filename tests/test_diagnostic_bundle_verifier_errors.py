from __future__ import annotations

import errno
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from qbstimeline.cli import main
from qbstimeline.diagnostic_bundle import verify_diagnostics_bundle


def _bundle_members() -> dict[str, bytes]:
    roles = {
        "qbs_ir.json": "qbs_ir",
        "diagnostics_report.json": "diagnostics_report",
        "versions.json": "versions",
    }
    members = {name: b"{}\n" for name in roles}
    manifest = {
        "schema": "q1lens.diagnostic-bundle",
        "version": 1,
        "members": [
            {"path": name, "role": role, "size": len(members[name]),
             "sha256": hashlib.sha256(members[name]).hexdigest()}
            for name, role in roles.items()
        ],
    }
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    return members


def _write_archive(path: Path, members: dict[str, bytes], compression: int = zipfile.ZIP_STORED) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)


@pytest.mark.parametrize("member_name", ["qbs_ir.json", "manifest.json"])
def test_verify_rejects_raw_nul_member_names(tmp_path: Path, capsys, member_name: str) -> None:
    members = _bundle_members()
    members[member_name + "Xhidden"] = members.pop(member_name)
    bundle_path = tmp_path / "nul-name.zip"
    _write_archive(bundle_path, members)
    bundle_path.write_bytes(bundle_path.read_bytes().replace(
        (member_name + "Xhidden").encode(), (member_name + "\x00hidden").encode(),
    ))

    assert any("unsafe archive member path" in error for error in verify_diagnostics_bundle(bundle_path))
    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    stderr = capsys.readouterr().err
    assert "unsafe archive member path" in stderr
    assert "\\x00hidden" in stderr
    assert "\x00" not in stderr


@pytest.mark.parametrize("member_name", ["qbs_ir.json", "manifest.json"])
@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["deflate", "bzip2", "lzma"])
def test_verify_reports_invalid_compression_as_integrity_failure(
    tmp_path: Path, capsys, member_name: str, compression: int,
) -> None:
    bundle_path = tmp_path / "invalid-compression.zip"
    _write_archive(bundle_path, _bundle_members(), compression)
    assert verify_diagnostics_bundle(bundle_path) == []
    with zipfile.ZipFile(bundle_path) as bundle:
        info = bundle.getinfo(member_name)
        offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    data = bytearray(bundle_path.read_bytes())
    if compression == zipfile.ZIP_DEFLATED:
        data[offset] |= 6  # DEFLATE block type 3 is reserved and invalid.
    elif compression == zipfile.ZIP_BZIP2:
        data[offset] ^= 0xFF  # Corrupt the BZIP2 stream header.
    else:
        properties_size = int.from_bytes(data[offset + 2:offset + 4], "little")
        data[offset + 4 + properties_size] = 0xFF  # Invalid initial range-coder byte.
    bundle_path.write_bytes(data)

    assert any(member_name in error for error in verify_diagnostics_bundle(bundle_path))
    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert member_name in capsys.readouterr().err


def test_verify_preserves_filesystem_errors_while_reading_bzip2(tmp_path: Path, capsys, monkeypatch) -> None:
    bundle_path = tmp_path / "io-error.zip"
    _write_archive(bundle_path, _bundle_members(), zipfile.ZIP_BZIP2)
    original_read = zipfile.ZipFile.read

    def fail_payload_read(bundle, member, *args, **kwargs):
        if member.filename == "qbs_ir.json":
            raise OSError(errno.EIO, "simulated filesystem read error")
        return original_read(bundle, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_payload_read)

    with pytest.raises(OSError) as error:
        verify_diagnostics_bundle(bundle_path)
    assert error.value.errno == errno.EIO
    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 2
    assert "simulated filesystem read error" in capsys.readouterr().err


def test_verify_reports_manifest_integer_limit_as_structure_failure(tmp_path: Path, capsys) -> None:
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if not limit:
        pytest.skip("This interpreter does not enforce the JSON integer conversion limit")
    members = _bundle_members()
    members["manifest.json"] = members["manifest.json"].replace(
        b'"version": 1', b'"version": ' + b"9" * (limit + 1),
    )
    bundle_path = tmp_path / "invalid-manifest.zip"
    _write_archive(bundle_path, members)

    assert any("invalid manifest.json" in error for error in verify_diagnostics_bundle(bundle_path))
    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == 1
    assert "invalid manifest.json" in capsys.readouterr().err


@pytest.mark.parametrize(("exists", "expected_code"), [(True, 1), (False, 2)])
def test_verify_preserves_invalid_zip_and_missing_file_exit_codes(
    tmp_path: Path, capsys, exists: bool, expected_code: int,
) -> None:
    bundle_path = tmp_path / "not-a-bundle.zip"
    if exists:
        bundle_path.write_bytes(b"not a ZIP archive")

    assert main(["verify-bundle", "--bundle", str(bundle_path)]) == expected_code
    assert capsys.readouterr().err
