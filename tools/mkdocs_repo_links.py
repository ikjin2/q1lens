"""MkDocs hook: point links that leave ``docs/`` at the repository on GitHub.

The Markdown sources use relative links such as ``../examples/demo/README.md`` so
that they work when read on GitHub. Those targets are outside the built site, so
this hook rewrites them to absolute repository URLs at build time.
"""

from __future__ import annotations

import posixpath
import re

REPO_BLOB = "https://github.com/ikjin2/q1lens/blob/main/"
_LINK = re.compile(r"\]\(((?:\.\./)+[^)\s#]+)(#[^)\s]*)?\)")


def on_page_markdown(markdown: str, page, **_kwargs) -> str:
    page_dir = posixpath.dirname(page.file.src_uri)

    def rewrite(match: re.Match[str]) -> str:
        target = posixpath.normpath(posixpath.join(page_dir, match.group(1)))
        if not target.startswith("../"):
            return match.group(0)  # still inside docs/
        return f"]({REPO_BLOB}{target[3:]}{match.group(2) or ''})"

    return _LINK.sub(rewrite, markdown)
