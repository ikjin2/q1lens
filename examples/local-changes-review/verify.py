"""Check the demo's diagnostic contracts with the current Python installation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "output" / "local-review" / "analysis"
EXPECTED = {
    "01-buffered-burst": (0, set()),
    "02-short-wrap": (0, {("analysis_incomplete", "warning")}),
    "03-finite-pressure": (0, {("acquisition_buffer_pressure", "warning")}),
    "04-sustained-overload": (2, {("acquisition_buffer_sustained_overload", "error"), ("analysis_incomplete", "warning")}),
    "05-unknown-profile": (0, {("acquisition_buffer_pressure", "warning"), ("analysis_incomplete", "warning")}),
    "06-pending-settings": (0, {("pending_hardware_state", "warning")}),
    "07-applied-settings": (0, set()),
    "08-rt-buffer": (2, {("definite_underflow", "error")}),
    "09-unresolved-branch": (0, {("acquisition_buffer_pressure", "warning"), ("unknown_duration", "warning"),
                                  ("analysis_incomplete", "warning"), ("unresolved_branch", "info")}),
    "10-editor-diagnostics": (2, {("invalid_argument_count", "error")}),
}


def main() -> None:
    print(f"Python: {sys.executable}")
    for name, (exit_code, expected) in EXPECTED.items():
        out = OUTPUT / name / "timeline_ir.json"
        result = subprocess.run(
            [sys.executable, "-m", "q1lens", "q1timeline", "analyze", "--project",
             str(ROOT / name / "q1timeline.yml"), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == exit_code, f"{name}: {result.stdout}\n{result.stderr}"
        ir = json.loads(out.read_text(encoding="utf-8"))
        # Compact analytical loops can additionally report omitted preview notes.
        findings = {(d["category"], d["severity"]) for d in ir["diagnostics"]
                    if d["category"] not in {"loop_truncated"}}
        assert findings == expected, f"{name}: expected {expected}, got {findings}"
        subprocess.run([sys.executable, "-m", "q1lens", "q1timeline", "render", "--ir", str(out),
                        "--out", str(out.with_name("timeline.html")), "--mode", "debug"],
                       check=True, capture_output=True)
        html = out.with_name("timeline.html").read_text(encoding="utf-8")
        assert html.index('<section class="diagnostics"') < html.index('<details id="q1timeline-analysis-details"')
        assert html.index('<aside id="event-inspector"') > html.index('<details id="q1timeline-analysis-details"')
        assert '<p class="diagnostic-fix">' not in html
        print(f"PASS {name}: exit={exit_code}, {sorted(findings)}")
    print(f"All {len(EXPECTED)} cases passed. Outputs: {OUTPUT}")


if __name__ == "__main__":
    main()
