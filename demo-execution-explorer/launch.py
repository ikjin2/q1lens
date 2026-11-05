"""Build the local-only demo from the current checkout and open a browser window."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import webbrowser

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
CASES = [
    ("retry", "Bounded retry", "q1asm-tutorials/11-control-flow-patterns/35-bounded-retry-loop/seq0.q1asm", [0, 0, 1]),
    ("bins", "Round-robin bin writer", "q1asm-tutorials/10-stateful-programs/32-round-robin-bin-writer/seq0.q1asm", []),
    ("golden", "Golden-section search", "golden-section/golden_section.q1asm", [value for index in range(16) for value in ([2000000, 1000000, 4000000, 1000000] if index % 2 == 0 else [4000000, 1000000, 2000000, 1000000])]),
]


def build() -> Path:
    output = ROOT / "output" / "execution-explorer"
    output.mkdir(parents=True, exist_ok=True)
    examples = []
    for case_id, title, relative, inputs in CASES:
        source = ROOT / "examples" / relative
        params_file = source.with_name("params.json")
        examples.append({
            "id": case_id, "title": title, "file": f"examples/{relative}",
            "source": source.read_text(encoding="utf-8"),
            "params": json.loads(params_file.read_text(encoding="utf-8")) if params_file.exists() else {},
            "inputs": inputs, "usesInputs": bool(inputs),
        })
    for name in ("index.html", "style.css", "trace.js", "app.js"):
        shutil.copyfile(DEMO / name, output / name)
    (output / "examples.js").write_text("window.EXPLORER_EXAMPLES = " + json.dumps(examples, ensure_ascii=False) + ";\n", encoding="utf-8")
    return output / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    page = build()
    print(page)
    if args.build_only:
        return
    chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if chrome.exists():
        subprocess.Popen([str(chrome), "--new-window", page.as_uri()])
    else:
        webbrowser.open(page.as_uri(), new=1)


if __name__ == "__main__":
    main()
