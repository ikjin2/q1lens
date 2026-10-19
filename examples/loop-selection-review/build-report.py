"""Build a portable HTML report from the example sources and verification evidence."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote


def main() -> None:
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    output = root / "output" / "loop-selection-review"
    output.mkdir(parents=True, exist_ok=True)
    cases = json.loads((here / "cases.json").read_text(encoding="utf-8"))["cases"]
    evidence = {}
    run_status = output / "run-status.json"
    run = json.loads(run_status.read_text(encoding="utf-8")) if run_status.exists() else None
    fixture_sources = [here / "cases.json", *here.glob("*/program.q1asm"), *here.glob("*/q1timeline.yml")]
    def checksum(path: Path) -> str | None:
        return sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def stale(kind: str, reason: str) -> None:
        evidence[kind] = {"status": "stale", "results": [], "reason": reason}

    for kind in ("backend", "host", "browser"):
        path = output / f"{kind}-results.json"
        evidence[kind] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "missing", "results": []}
        if evidence[kind].get("complete") is False:
            evidence[kind]["status"] = "running"
        if run and path.exists() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < datetime.fromisoformat(run["started_at"]):
            evidence[kind] = {"status": "stale", "results": [], "previous_generated_at": evidence[kind].get("generated_at")}
        if path.exists() and any(source.stat().st_mtime > path.stat().st_mtime for source in fixture_sources):
            stale(kind, "Example sources changed after this verification.")
    backend_path = output / "backend-results.json"
    if backend_path.exists() and any(path.stat().st_mtime > backend_path.stat().st_mtime for path in (root / "src" / "q1timeline").rglob("*.py")):
        stale("backend", "Analyzer sources changed after this verification.")
    controller = root / "vscode-extension" / "out" / "src" / "q1timeline" / "controller.js"
    host = evidence["host"]
    if host.get("controller_sha256") and (host["controller_sha256"] != checksum(controller) or
            host.get("source_sha256") != checksum(root / "vscode-extension" / "src" / "q1timeline" / "controller.ts")):
        stale("host", "The controller differs from the verified version.")
    host_path = output / "host-results.json"
    if host_path.exists() and any(path.stat().st_mtime > host_path.stat().st_mtime for path in (output / "fixtures").glob("*/*.json")):
        stale("host", "Analyzer fixtures were regenerated after editor verification.")
    browser = evidence["browser"]
    if any(checksum(root / "vscode-extension" / "src" / asset) != expected for asset, expected in browser.get("asset_sha256", {}).items()):
        stale("browser", "Webview assets differ from the verified version.")
    elif any(checksum(output / "fixtures" / f"{key}.json") != expected for key, expected in browser.get("fixture_sha256", {}).items()):
        stale("browser", "Webview fixtures differ from the verified version.")
    for case in cases:
        source = here / case["id"] / "program.q1asm"
        project = source.with_name("q1timeline.yml")
        case.update(
            source=str(source), project=str(project), code=source.read_text(encoding="utf-8"),
            source_sha256=sha256(source.read_bytes()).hexdigest(),
            source_url=quote(Path(os.path.relpath(source, output)).as_posix()),
            project_url=quote(Path(os.path.relpath(project, output)).as_posix()),
        )
    browser = evidence["browser"]
    for row in browser.get("results", []):
        screenshot = row.get("screenshot")
        if screenshot:
            path = Path(screenshot)
            if not path.is_absolute():
                path = root / path
            row["screenshot_url"] = quote(Path(os.path.relpath(path, output)).as_posix())
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root), "revision": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")), "cases": cases, "evidence": evidence,
        "workspace": str(here / "loop-selection-review.code-workspace"),
        "workspace_url": quote(Path(os.path.relpath(here / "loop-selection-review.code-workspace", output)).as_posix()),
        "controller_sha256": checksum(controller) or "compile이 필요합니다",
        "renderer_sha256": checksum(root / "vscode-extension" / "src" / "shared" / "timeline" / "renderer.js"),
        "has_comparison": (root / "output" / "playwright" / "loop-selection-review" / "before-strip-fix" / "03-nested-counted-analysis.png").is_file()
            and (root / "output" / "playwright" / "loop-selection-review" / "03-nested-counted-analysis-outer-after.png").is_file(),
    }
    if run:
        data["run"] = run
    template = (here / "report-template.html").read_text(encoding="utf-8")
    colors = (root / "vscode-extension" / "src" / "shared" / "timeline" / "instructionColors.js").read_text(encoding="utf-8")
    html = template.replace("__REPORT_DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
    html = html.replace("__INSTRUCTION_COLORS__", colors)
    target = output / "report.html"
    target.write_text(html, encoding="utf-8")
    print(f"Loop selection report: {target}")


if __name__ == "__main__":
    main()
