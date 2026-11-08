"""Serve the standalone demo with local, event-driven branch exploration."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import shutil
from urllib.parse import urlsplit

import build


def replay(payload):
    if not isinstance(payload, dict) or set(payload) != {"caseId", "scenarioId", "choices"}:
        raise ValueError("Expected caseId, scenarioId and choices.")
    case = next((item for item in build.CASES if item[0] == payload["caseId"]), None)
    if case is None:
        raise ValueError("Unknown program.")
    config = next((item for item in build.configurations(case[0]) if item["id"] == payload["scenarioId"]), None)
    if config is None:
        raise ValueError("Unknown input scenario.")
    path = build.ROOT / "examples" / case[3]
    program = build.parse_q1asm(path.read_text(encoding="utf-8-sig"), file=str(path))
    params_path = path.with_name("params.json")
    params = json.loads(params_path.read_text()) if params_path.exists() else {}
    return build.project_scenario(case[0], program, params, config, choices=payload["choices"])


class Handler(BaseHTTPRequestHandler):
    def send_content(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Switching scenarios can cancel an in-flight browser request.

    def send_json(self, status, value):
        self.send_content(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def local_request(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return host == expected and (origin is None or origin == f"http://{expected}")

    def do_GET(self):
        if not self.local_request():
            self.send_json(403, {"error": "Use the local demo address."})
            return
        name = urlsplit(self.path).path.lstrip("/") or "index.html"
        types = {"index.html": "text/html", "style.css": "text/css", "timeline.css": "text/css", "instructionColors.js": "text/javascript", "app.js": "text/javascript", "history.js": "text/javascript", "views.js": "text/javascript", "data.js": "text/javascript"}
        if name not in types:
            self.send_json(404, {"error": "Not found."})
            return
        content = (build.OUTPUT / name).read_bytes()
        if name == "data.js":
            content += b"\nwindow.EXECUTION_FLOW_LIVE = true;\n"
        self.send_content(200, content, types[name] + "; charset=utf-8")

    def do_POST(self):
        if not self.local_request():
            self.send_json(403, {"error": "Use the local demo address."})
            return
        if self.path != "/api/run":
            self.send_json(404, {"error": "Not found."})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_json(415, {"error": "Expected JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size.")
            result = replay(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        self.send_json(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8741)
    args = parser.parse_args()
    build.OUTPUT.mkdir(parents=True, exist_ok=True)
    data = build.build_data()
    (build.OUTPUT / "data.js").write_text("window.EXECUTION_FLOW_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    for name in ("index.html", "style.css", "history.js", "views.js", "app.js"):
        shutil.copyfile(build.DEMO / name, build.OUTPUT / name)
    for source, name in [("renderer.css", "timeline.css"), ("instructionColors.js", "instructionColors.js")]:
        shutil.copyfile(build.ROOT / "vscode-extension/src/shared/timeline" / source, build.OUTPUT / name)
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Execution Flow: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
