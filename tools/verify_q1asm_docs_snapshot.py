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


DOCS_URL = "https://docs.qblox.com/en/2026.10.1/products/qblox_instruments/q1/index.html"
LINQ_RECEIVE_URL = "https://docs.qblox.com/en/2026.10.1/products/architecture/sequencers/linq_based_feedback.html#fb-pull-data"
ALLOWED_LOCAL_EXTRAS = {"acquire_weighed", "fb_cmd", "play_pulse", "sw_req"}
# The assembler additionally accepts a packed register for these named operations.
ASSEMBLER_ONLY_SIGNATURES = {
    (op, ("R", "I")) for op in (
        "fb_acq_tb_id", "fb_acq_tb_cfg", "fb_acq_iq_id", "fb_acq_tb_valid", "fb_acq_tb_extra",
        "fb_acq_tb_mock", "fb_acq_iq_shift", "fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id",
        "fb_tdc_tdelta_id", "fb_com_cfg", "fb_com_extra",
    )
} | {(op, ("I", "R", "I")) for op in ("fb_com_data", "upd_thres")}

DYNAMIC_RUNTIME_EXPECTED = {
    **{op: {"jump": 16, "continue": 4} for op in {"jmp", *STATUS_BRANCH_OPS}},
    "jge": {"jump": 24, "continue": 4},
    "jlt": {"jump": 24, "continue": 4},
    "loop": {"jump": 24, "continue": 4},
}


# Each exception is keyed by the exact assembler operand types. Its pair is
# (Qblox 2026.10.1 instruction reference, q1simulator 1.4.1 ISA 2), in ns.
Q1SIMULATOR_TIMING_DIFFERENCES = {
    **{(op, ("R", "R", "R")): (12, 16)
       for op in ("add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr", "mulu16", "muls16")},
    **{(op, ("R", "R")): (12, 16) for op in ("cmp", "test")},
    **{(op, ("R", "R", "R")): (20, 24) for op in ("mulu32l", "mulu32h", "muls32l", "muls32h")},
    **{(op, ("R", "R", "R", "R")): (24, 28) for op in ("mulu32", "muls32")},
    **{("jge", (target,)): (24, 16) for target in ("I/L", "R")},
    **{(op, ("R", "I", "I/L")): (24, 28) for op in ("jge", "jlt")},
    **{(op, ("R", "I", "R")): (24, 32) for op in ("jge", "jlt")},
    **{("loop", ("R", target)): (24, 32) for target in ("I/L", "R")},
    **{(op, ("R", "R")): (4, 8) for op in ("set_awg_gain", "set_awg_offs")},
    ("set_cond", ("R", "R", "R", "I")): (4, 12),
    ("play", ("R", "R", "I")): (4, 8),
    ("acquire_weighted", ("I", "R", "R", "R", "I")): (4, 12),
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


def parse_docs_snapshot(source: str) -> tuple[dict[str, set[tuple[tuple[str, ...], int]]], dict[str, dict[str, int | None]]]:
    docs: dict[str, set[tuple[tuple[str, ...], int]]] = {}
    dynamic_runtimes: dict[str, dict[str, int | None]] = {}
    for op, section in iter_instruction_sections(source):
        plain = strip_tags(section)
        signatures: set[tuple[tuple[str, ...], int]] = set()
        for signature_text, runtime_raw in re.findall(
            r"signature (.*?) arguments .*? Q1 core runtime (.*?)(?= signature | Description |$)",
            plain,
        ):
            jump_runtime, continue_runtime = parse_runtime(runtime_raw)
            signatures.add((normalize_docs_types(op, signature_types(signature_text)), jump_runtime))
            if op in DYNAMIC_RUNTIME_EXPECTED or continue_runtime is not None:
                observed = {"jump": jump_runtime, "continue": continue_runtime}
                if dynamic_runtimes.setdefault(op, observed) != observed:
                    raise ValueError(f"Inconsistent branch timings in the documentation for {op}: {signature_text}")
        if signatures:
            docs[op] = signatures
    return docs, dynamic_runtimes


def main() -> int:
    source = urllib.request.urlopen(DOCS_URL, timeout=30).read().decode("utf-8", "replace")
    docs, dynamic_runtimes = parse_docs_snapshot(source)

    missing_local_ops = sorted(set(docs) - set(INSTRUCTION_TABLE))
    unexpected_local_ops = sorted(set(INSTRUCTION_TABLE) - set(docs) - ALLOWED_LOCAL_EXTRAS)
    missing_local_signatures: dict[str, list[dict[str, object]]] = {}
    undocumented_local_signatures: dict[str, list[tuple[str, ...]]] = {}
    runtime_mismatches: dict[str, list[dict[str, object]]] = {}
    simulator_differences: dict[str, list[dict[str, object]]] = {}

    for op, docs_signatures in sorted(docs.items()):
        if op not in INSTRUCTION_TABLE:
            continue
        local_signatures = local_signature_summary(op)
        local_runtimes_by_signature: dict[tuple[str, ...], list[int]] = {}
        for signature, runtime in local_signatures:
            local_runtimes_by_signature.setdefault(signature, []).append(runtime)
        docs_types = {signature for signature, _ in docs_signatures}
        for signature in sorted(local_runtimes_by_signature.keys() - docs_types):
            if (op, signature) not in ASSEMBLER_ONLY_SIGNATURES:
                undocumented_local_signatures.setdefault(op, []).append(signature)
        for signature, runtime in sorted(docs_signatures):
            local_runtimes = sorted(local_runtimes_by_signature.get(signature, []))
            if not local_runtimes:
                missing_local_signatures.setdefault(op, []).append({"signature": signature, "docs_runtime": runtime})
                continue
            entry = {"signature": signature, "docs_runtime": runtime, "local_runtimes": local_runtimes}
            expected_pair = Q1SIMULATOR_TIMING_DIFFERENCES.get((op, signature))
            if expected_pair is None:
                if local_runtimes != [runtime]:
                    runtime_mismatches.setdefault(op, []).append(entry)
            elif runtime == expected_pair[0] and local_runtimes == [expected_pair[1]]:
                simulator_differences.setdefault(op, []).append(entry)
            else:
                runtime_mismatches.setdefault(op, []).append({**entry, "expected_docs_and_simulator_ns": expected_pair})

    dynamic_runtime_mismatches: dict[str, dict[str, object]] = {}
    for op, runtime in sorted(dynamic_runtimes.items()):
        expected = DYNAMIC_RUNTIME_EXPECTED.get(op)
        if runtime == expected:
            continue
        dynamic_runtime_mismatches[op] = {"docs": runtime, "expected": expected}

    report = {
        "docs_url": DOCS_URL,
        "docs_op_count": len(docs),
        "local_op_count": len(INSTRUCTION_TABLE),
        "allowed_local_extras": sorted(ALLOWED_LOCAL_EXTRAS),
        "assembler_backed_aliases": {"fb_cmd": {
            "assembler": "Qblox Instruments 1.4.1 q1asm_windows -d",
            "signature_fixture": "tests/fixtures/q1asm_signatures.json",
            "alias_vectors": "tests/test_q1timeline_linq_aliases.py",
        }},
        "unexpected_local_ops": unexpected_local_ops,
        "missing_local_ops": missing_local_ops,
        "missing_local_signatures": missing_local_signatures,
        "undocumented_local_signatures": undocumented_local_signatures,
        "runtime_mismatches": runtime_mismatches,
        "q1simulator_1_4_1_isa2_differences": simulator_differences,
        "documented_runtime_conflicts": {
            "fb_pull_data": {
                "instruction_reference": {"runtime_ns": dict(docs.get("fb_pull_data", ())).get(("R", "R")),
                                          "url": DOCS_URL + "#fb_pull_data-instt"},
                "linq_receive_guide": {"runtime_ns": 8, "url": LINQ_RECEIVE_URL},
                "local_runtime_ns": dict(local_signature_summary("fb_pull_data")).get(("R", "R")),
            }
        },
        "dynamic_runtime_mismatches": dynamic_runtime_mismatches,
        "dynamic_runtimes": dynamic_runtimes,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(
        [
            unexpected_local_ops,
            missing_local_ops,
            missing_local_signatures,
            undocumented_local_signatures,
            runtime_mismatches,
            dynamic_runtime_mismatches,
        ]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
