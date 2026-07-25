from __future__ import annotations

import html
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def write_gallery_index(
    examples: Iterable[Mapping[str, Any]],
    out: str | Path,
    *,
    title: str = "Q1Lens Gallery",
) -> None:
    output_path = Path(out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_gallery_index(examples, title=title), encoding="utf-8")


def render_gallery_index(
    examples: Iterable[Mapping[str, Any]],
    *,
    title: str = "Q1Lens Gallery",
) -> str:
    rows = list(examples)
    ok_count = sum(1 for row in rows if str(row.get("status", "")).lower() == "ok")
    failed_count = len(rows) - ok_count
    body = _render_cards(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --text: #18202b;
      --muted: #657184;
      --line: #d8dee8;
      --ok: #0f766e;
      --error: #b42318;
      --soft: #eef4f4;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); }}
    header {{ padding: 22px 28px; background: var(--surface); border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; color: var(--muted); font-size: 13px; }}
    .summary strong {{ color: var(--text); }}
    main {{ padding: 18px; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
    .card {{ display: grid; gap: 10px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); text-decoration: none; color: inherit; }}
    .card:hover {{ border-color: var(--ok); background: var(--soft); }}
    .card-title {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    h2 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    .badge {{ border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-size: 11px; text-transform: uppercase; }}
    .badge.ok {{ color: var(--ok); border-color: var(--ok); }}
    .badge.error {{ color: var(--error); border-color: var(--error); }}
    dl {{ display: grid; grid-template-columns: auto 1fr; gap: 5px 10px; margin: 0; color: var(--muted); font-size: 12px; }}
    dt {{ font-weight: 650; color: var(--text); }}
    dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    .empty {{ padding: 16px; color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="summary">
      <span><strong>{len(rows)} examples</strong></span>
      <span><strong>{ok_count} compiled</strong></span>
      <span><strong>{failed_count} failed</strong></span>
    </div>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def _render_cards(examples: list[Mapping[str, Any]]) -> str:
    if not examples:
        return '<div class="empty">No examples generated.</div>'
    return "\n".join(_render_card(example) for example in examples)


def _render_card(example: Mapping[str, Any]) -> str:
    status = str(example.get("status", "unknown")).lower()
    badge_class = "ok" if status == "ok" else "error"
    href = html.escape(str(example.get("href", "#")), quote=True)
    title = html.escape(str(example.get("title", example.get("id", "example"))))
    category = html.escape(str(example.get("category", "uncategorized")))
    source = html.escape(str(example.get("source", "")))
    error = str(example.get("error", ""))
    error_row = f"<dt>error</dt><dd>{html.escape(error)}</dd>" if error else ""
    return f"""<a class="card" href="{href}">
  <div class="card-title">
    <h2>{title}</h2>
    <span class="badge {badge_class}">{html.escape(status)}</span>
  </div>
  <dl>
    <dt>category</dt><dd>{category}</dd>
    <dt>operations</dt><dd>{html.escape(str(example.get("operation_count", 0)))}</dd>
    <dt>timing rows</dt><dd>{html.escape(str(example.get("timing_row_count", 0)))}</dd>
    <dt>Q1ASM</dt><dd>{html.escape(str(example.get("q1asm_program_count", 0)))}</dd>
    <dt>source</dt><dd>{source}</dd>
    {error_row}
  </dl>
</a>"""
