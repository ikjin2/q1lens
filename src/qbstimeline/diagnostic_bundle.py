from __future__ import annotations

import hashlib
import json
import lzma
import re
import zipfile
import zlib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from qbstimeline.consistency import versions_payload


MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "q1lens.diagnostic-bundle"
MANIFEST_VERSION = 1
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_SINGLETON_ROLES = {
    "qbs_ir": "qbs_ir.json",
    "q1timeline_ir": "q1timeline_ir.json",
    "diagnostics_report": "diagnostics_report.json",
    "versions": "versions.json",
}
_REQUIRED_ROLES = {"qbs_ir", "diagnostics_report", "versions"}
_ALLOWED_ROLES = {*_SINGLETON_ROLES, "schedule_source", "q1asm"}


def write_diagnostics_bundle(
    *,
    bundle_path: Path,
    qbs_ir: Mapping[str, Any],
    q1timeline_ir: Mapping[str, Any] | None,
    report: Mapping[str, Any],
    schedule_path: Path | None,
) -> None:
    payloads: dict[str, tuple[str, bytes]] = {
        "qbs_ir.json": ("qbs_ir", _json_bytes(qbs_ir)),
        "diagnostics_report.json": ("diagnostics_report", _json_bytes(report)),
        "versions.json": ("versions", _json_bytes(versions_payload())),
    }
    if q1timeline_ir is not None:
        payloads["q1timeline_ir.json"] = ("q1timeline_ir", _json_bytes(q1timeline_ir))
    if schedule_path is not None:
        if not schedule_path.is_file():
            raise ValueError(f"--schedule must name an existing file: {schedule_path}")
        schedule_name = _safe_schedule_name(schedule_path.name)
        payloads[f"schedule/{schedule_name}"] = ("schedule_source", schedule_path.read_bytes())

    used_casefold_names: set[str] = set()
    for sequencer_id, text in sorted(_embedded_q1asm(qbs_ir).items()):
        filename = _safe_q1asm_filename(sequencer_id, used_casefold_names)
        used_casefold_names.add(filename.casefold())
        payloads[f"q1asm/{filename}"] = ("q1asm", text.encode("utf-8"))

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": MANIFEST_VERSION,
        "members": [
            {
                "path": path,
                "role": role,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, (role, data) in sorted(payloads.items())
        ],
    }
    members = {path: data for path, (_, data) in payloads.items()}
    members[MANIFEST_NAME] = _json_bytes(manifest)

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path, data in sorted(members.items()):
            bundle.writestr(_zip_info(path), data)


def verify_diagnostics_bundle(bundle_path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(bundle_path, "r") as bundle:
            return _verify_open_bundle(bundle)
    except zipfile.BadZipFile as exc:
        return [f"invalid ZIP archive: {exc}"]


def _verify_open_bundle(bundle: zipfile.ZipFile) -> list[str]:
    errors: list[str] = []
    infos = bundle.infolist()
    names = [info.filename for info in infos]
    name_counts = Counter(names)
    duplicate_names = sorted(name for name, count in name_counts.items() if count > 1)
    for name in duplicate_names:
        errors.append(f"duplicate archive member: {name}")
    for paths in _casefold_collisions(names):
        errors.append(f"case-insensitive archive member collision: {', '.join(paths)}")
    for name in sorted({info.orig_filename for info in infos}):
        if not _is_safe_member_path(name):
            errors.append("unsafe archive member path: " + name.replace("\x00", "\\x00"))

    manifest_infos = [info for info in infos if info.filename == MANIFEST_NAME]
    if len(manifest_infos) != 1:
        errors.append(f"archive must contain exactly one {MANIFEST_NAME}")
        return errors
    try:
        manifest_data = _read_archive_member(bundle, manifest_infos[0])
        manifest = json.loads(manifest_data.decode("utf-8"))
    except (
        KeyError,
        UnicodeDecodeError,
        ValueError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        zlib.error,
        lzma.LZMAError,
    ) as exc:
        errors.append(f"invalid {MANIFEST_NAME}: {exc}")
        return errors

    entries = _validate_manifest(manifest, errors)
    if entries is None:
        return errors

    declared_paths = [entry["path"] for entry in entries]
    declared_counts = Counter(declared_paths)
    duplicate_declared = sorted(path for path, count in declared_counts.items() if count > 1)
    for path in duplicate_declared:
        errors.append(f"duplicate manifest member: {path}")
    for paths in _casefold_collisions(declared_paths):
        errors.append(f"case-insensitive manifest member collision: {', '.join(paths)}")

    archived_payloads = {name for name in names if name != MANIFEST_NAME}
    declared_payloads = set(declared_paths)
    for path in sorted(archived_payloads - declared_payloads):
        errors.append(f"extra archive member not declared by manifest: {path}")
    for path in sorted(declared_payloads - archived_payloads):
        errors.append(f"manifest member missing from archive: {path}")

    info_by_name = {info.filename: info for info in infos if name_counts[info.filename] == 1}
    for entry in entries:
        path = entry["path"]
        info = info_by_name.get(path)
        if info is None:
            continue
        try:
            data = _read_archive_member(bundle, info)
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, lzma.LZMAError) as exc:
            errors.append(f"cannot read archive member {path}: {exc}")
            continue
        actual_size = len(data)
        if actual_size != entry["size"]:
            errors.append(
                f"size mismatch for {path}: manifest {entry['size']}, archive {actual_size}"
            )
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != entry["sha256"]:
            errors.append(f"SHA-256 mismatch for {path}")
    return errors


def _read_archive_member(bundle: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        return bundle.read(info)
    except OSError as exc:
        if info.compress_type == zipfile.ZIP_BZIP2 and exc.errno is None and str(exc) == "Invalid data stream":
            raise zipfile.BadZipFile("invalid BZIP2 data stream") from exc
        raise


def _validate_manifest(manifest: Any, errors: list[str]) -> list[dict[str, Any]] | None:
    if not isinstance(manifest, dict):
        errors.append("manifest root must be an object")
        return None
    if set(manifest) != {"schema", "version", "members"}:
        errors.append("manifest must contain only schema, version, and members")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append(f"unsupported manifest schema: {manifest.get('schema')!r}")
    manifest_version = manifest.get("version")
    if (
        isinstance(manifest_version, bool)
        or not isinstance(manifest_version, int)
        or manifest_version != MANIFEST_VERSION
    ):
        errors.append(f"unsupported manifest version: {manifest.get('version')!r}")
    raw_entries = manifest.get("members")
    if not isinstance(raw_entries, list):
        errors.append("manifest members must be an array")
        return None

    entries: list[dict[str, Any]] = []
    role_counts: dict[str, int] = {}
    for index, value in enumerate(raw_entries):
        if not isinstance(value, dict):
            errors.append(f"manifest member {index} must be an object")
            continue
        if set(value) != {"path", "role", "size", "sha256"}:
            errors.append(f"manifest member {index} must contain path, role, size, and sha256")
        path = value.get("path")
        role = value.get("role")
        size = value.get("size")
        digest = value.get("sha256")
        valid = True
        if not isinstance(path, str) or not _is_safe_member_path(path) or path == MANIFEST_NAME:
            errors.append(f"manifest member {index} has an unsafe or reserved path")
            valid = False
        if not isinstance(role, str) or role not in _ALLOWED_ROLES:
            errors.append(f"manifest member {index} has an unsupported role")
            valid = False
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(f"manifest member {index} has an invalid size")
            valid = False
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            errors.append(f"manifest member {index} has an invalid SHA-256")
            valid = False
        if not valid:
            continue
        entry = {"path": path, "role": role, "size": size, "sha256": digest}
        entries.append(entry)
        role_counts[role] = role_counts.get(role, 0) + 1
        _validate_role_path(path, role, index, errors)

    for role in sorted(_REQUIRED_ROLES):
        if role_counts.get(role, 0) != 1:
            errors.append(f"manifest must declare exactly one {role} member")
    for role in sorted(set(_SINGLETON_ROLES) - _REQUIRED_ROLES):
        if role_counts.get(role, 0) > 1:
            errors.append(f"manifest may declare at most one {role} member")
    if role_counts.get("schedule_source", 0) > 1:
        errors.append("manifest may declare at most one schedule_source member")
    return entries


def _validate_role_path(path: str, role: str, index: int, errors: list[str]) -> None:
    exact_path = _SINGLETON_ROLES.get(role)
    if exact_path is not None and path != exact_path:
        errors.append(f"manifest member {index} role {role} must use path {exact_path}")
    if role == "q1asm" and not _is_child_file(path, "q1asm", suffix=".q1asm"):
        errors.append(f"manifest member {index} has an invalid q1asm path")
    if role == "schedule_source" and not _is_child_file(path, "schedule"):
        errors.append(f"manifest member {index} has an invalid schedule path")


def _is_child_file(path: str, directory: str, *, suffix: str | None = None) -> bool:
    parts = path.split("/")
    if len(parts) != 2 or parts[0] != directory or not _is_safe_segment(parts[1]):
        return False
    return suffix is None or parts[1].endswith(suffix)


def _is_safe_member_path(path: str) -> bool:
    if not path or "\\" in path or "\x00" in path or ":" in path or path.startswith("/"):
        return False
    pure = PurePosixPath(path)
    parts = path.split("/")
    return (
        pure.as_posix() == path
        and all(_is_safe_segment(part) for part in parts)
        and not path.endswith("/")
    )


def _casefold_collisions(paths: list[str]) -> list[list[str]]:
    grouped: dict[str, set[str]] = {}
    for path in paths:
        grouped.setdefault(path.casefold(), set()).add(path)
    return sorted(sorted(group) for group in grouped.values() if len(group) > 1)


def _safe_q1asm_filename(sequencer_id: str, used_casefold_names: set[str]) -> str:
    if _is_safe_segment(sequencer_id) and len(sequencer_id) <= 80:
        candidate = f"{sequencer_id}.q1asm"
    else:
        candidate = f"{_slug(sequencer_id, fallback='sequencer')}--{_short_hash(sequencer_id)}.q1asm"
    if candidate.casefold() not in used_casefold_names:
        return candidate
    stem = candidate.removesuffix(".q1asm")
    digest = hashlib.sha256(sequencer_id.encode("utf-8")).hexdigest()
    for length in range(16, len(digest) + 1, 4):
        collision_safe = f"{stem}--{digest[:length]}.q1asm"
        if collision_safe.casefold() not in used_casefold_names:
            return collision_safe
    raise ValueError(f"could not create a unique Q1ASM bundle name for {sequencer_id!r}")


def _safe_schedule_name(name: str) -> str:
    if _is_safe_segment(name) and len(name) <= 100:
        return name
    source = Path(name)
    suffix = source.suffix
    safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix) else ""
    stem = name[: -len(suffix)] if suffix else name
    return f"{_slug(stem, fallback='schedule')}--{_short_hash(name)}{safe_suffix}"


def _is_safe_segment(value: str) -> bool:
    if _SAFE_SEGMENT.fullmatch(value) is None or value in {".", ".."}:
        return False
    basename = value.split(".", 1)[0].lower()
    return basename not in _WINDOWS_RESERVED_NAMES and not value.endswith(".")


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("._-")
    if not slug or not _is_safe_segment(slug):
        slug = fallback
    return slug[:64].rstrip("._-") or fallback


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _embedded_q1asm(qbs_ir: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    q1asm_by_sequencer = qbs_ir.get("q1asm_by_sequencer")
    if isinstance(q1asm_by_sequencer, Mapping):
        for key, value in q1asm_by_sequencer.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
    q1asm_programs = qbs_ir.get("q1asm_programs")
    if isinstance(q1asm_programs, list):
        for program in q1asm_programs:
            if not isinstance(program, Mapping):
                continue
            sequencer = program.get("sequencer_id") or program.get("sequencer")
            text = program.get("text")
            if isinstance(sequencer, str) and isinstance(text, str):
                result.setdefault(sequencer, text)
    return result


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info
