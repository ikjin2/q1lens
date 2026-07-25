from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS, INSTRUCTION_TABLE, get_instruction_spec


DOCS_URL = "https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html"
ALLOWED_LOCAL_EXTRAS = {"acquire_weighed", "mulu32", "play_pulse", "sw_req"}
DYNAMIC_RUNTIME_EXPECTED = {
    **{op: {"jump": 16, "continue": 4} for op in STATUS_BRANCH_OPS},
    "jge": {"jump": 24, "continue": 4},
    "jlt": {"jump": 24, "continue": 4},
    "loop": {"jump": 24, "continue": 4},
}


def local_signature_summary(op: str) -> set[tuple[tuple[str, ...], int]]:
    spec = get_instruction_spec(op)
    return {
        (tuple("/".join(sorted(arg_types)) for arg_types in signature.args), signature.q1_time_ns)
        for signature in spec.signatures
    }


def parse_runtime(raw: str) -> tuple[int, int | None]:
    numbers = [int(value) for value in re.findall(r"\d+", raw)]
    if not numbers:
        raise ValueError(f"Unable to parse Q1 runtime: {raw!r}")
    jump_runtime = numbers[0]
    continue_runtime = numbers[1] if "continue" in raw and len(numbers) > 1 else None
    return jump_runtime, continue_runtime


def strip_tags(source: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", source)
    return html.unescape(re.sub(r"\s+", " ", without_tags)).strip()


def iter_instruction_sections(source: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r'<section[^>]*id="([^"]+)-instt"[^>]*>', source, flags=re.IGNORECASE))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        op = match.group(1).replace("-", "_")
        sections.append((op, source[match.end() : end]))
    return sections


def signature_types(signature_text: str) -> tuple[str, ...]:
    return tuple(re.findall(r":\s*([IR])\b", signature_text))


def normalize_docs_types(op: str, types: tuple[str, ...]) -> tuple[str, ...]:
    branch_ops = {"jmp", "jge", "jlt", "loop", *STATUS_BRANCH_OPS}
    if op in branch_ops and types and types[-1] == "I":
        return (*types[:-1], "I/L")
    return types


def parse_docs_snapshot(source: str) -> tuple[dict[str, set[tuple[tuple[str, ...], int]]], dict[str, dict[str, int]]]:
    docs: dict[str, set[tuple[tuple[str, ...], int]]] = {}
    dynamic_runtimes: dict[str, dict[str, int]] = {}
    for op, section in iter_instruction_sections(source):
        plain = strip_tags(section)
        signatures: set[tuple[tuple[str, ...], int]] = set()
        for signature_text, runtime_raw in re.findall(
            r"signature (.*?) arguments .*? Q1 core runtime (.*?)(?= signature | Description |$)",
            plain,
        ):
            jump_runtime, continue_runtime = parse_runtime(runtime_raw)
            signatures.add((normalize_docs_types(op, signature_types(signature_text)), jump_runtime))
            if continue_runtime is not None:
                dynamic_runtimes[op] = {"jump": jump_runtime, "continue": continue_runtime}
        if signatures:
            docs[op] = signatures
    return docs, dynamic_runtimes


def main() -> int:
    source = urllib.request.urlopen(DOCS_URL, timeout=30).read().decode("utf-8", "replace")
    docs, dynamic_runtimes = parse_docs_snapshot(source)

    missing_local_ops = sorted(set(docs) - set(INSTRUCTION_TABLE))
    unexpected_local_ops = sorted(set(INSTRUCTION_TABLE) - set(docs) - ALLOWED_LOCAL_EXTRAS)
    missing_local_signatures: dict[str, list[dict[str, object]]] = {}
    runtime_mismatches: dict[str, list[dict[str, object]]] = {}

    for op, docs_signatures in sorted(docs.items()):
        if op not in INSTRUCTION_TABLE:
            continue
        local_signatures = local_signature_summary(op)
        local_runtimes_by_signature: dict[tuple[str, ...], list[int]] = {}
        for signature, runtime in local_signatures:
            local_runtimes_by_signature.setdefault(signature, []).append(runtime)
        for signature, runtime in sorted(docs_signatures):
            if (signature, runtime) in local_signatures:
                continue
            local_runtimes = sorted(local_runtimes_by_signature.get(signature, []))
            if local_runtimes:
                runtime_mismatches.setdefault(op, []).append(
                    {"signature": signature, "docs_runtime": runtime, "local_runtimes": local_runtimes}
                )
            else:
                missing_local_signatures.setdefault(op, []).append({"signature": signature, "docs_runtime": runtime})

    dynamic_runtime_mismatches: dict[str, dict[str, object]] = {}
    for op, runtime in sorted(dynamic_runtimes.items()):
        expected = DYNAMIC_RUNTIME_EXPECTED.get(op)
        if expected is None or runtime == expected:
            continue
        dynamic_runtime_mismatches[op] = {"docs": runtime, "expected": expected}

    report = {
        "docs_url": DOCS_URL,
        "docs_op_count": len(docs),
        "local_op_count": len(INSTRUCTION_TABLE),
        "allowed_local_extras": sorted(ALLOWED_LOCAL_EXTRAS),
        "unexpected_local_ops": unexpected_local_ops,
        "missing_local_ops": missing_local_ops,
        "missing_local_signatures": missing_local_signatures,
        "runtime_mismatches": runtime_mismatches,
        "dynamic_runtime_mismatches": dynamic_runtime_mismatches,
        "dynamic_runtimes": dynamic_runtimes,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(
        [
            unexpected_local_ops,
            missing_local_ops,
            missing_local_signatures,
            runtime_mismatches,
            dynamic_runtime_mismatches,
        ]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
