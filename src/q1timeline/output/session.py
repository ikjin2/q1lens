"""Sample output windows on request over JSON lines.

The Hardware outputs rack draws one viewport at a time. This process keeps the scene
of a TimelineIR `output` section in memory and answers each pan, zoom or trial value
with exactly the samples that viewport needs.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from q1timeline.output.window import MAX_BINS, Scene, scene_from_output

MAX_ABS_TIME = 1 << 53


class OutputSession:
    def __init__(self) -> None:
        self.scene: Scene | None = None
        self.output: dict | None = None
        self.trial: dict[str, int] = {}

    def load(self, output: Any) -> dict:
        self.scene = scene_from_output(output)
        self.output = output
        self.trial = {}
        return {"extent": self.scene.extent(), "parameters": self.scene.parameters}

    def window(self, t0: Any, t1: Any, bins: Any = 720, params: Any = None) -> dict:
        if self.scene is None:
            raise ValueError("Load an output section before requesting a window")
        if (type(t0) is not int or type(t1) is not int or not -MAX_ABS_TIME <= t0 < t1 <= MAX_ABS_TIME
                or type(bins) is not int or not 1 <= bins <= MAX_BINS):
            raise ValueError("Expected integer ns with t0 < t1 and 1 to 2000 bins")
        if params is not None:
            if not isinstance(params, dict) or any(not isinstance(k, str) or type(v) is not int for k, v in params.items()):
                raise ValueError("Trial values must map parameter names to integers")
            if params != self.trial:
                self.scene.rebind(params)
                self.trial = dict(params)
        return self.scene.window(t0, t1, bins)


def main(argv: list[str] | None = None) -> int:
    session = OutputSession()
    for line in sys.stdin:
        request: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Output command must be an object")
            command = request.get("command")
            if command == "load":
                result = session.load(request.get("output"))
            elif command == "window":
                result = {"window": session.window(request.get("t0"), request.get("t1"),
                                                   request.get("bins", 720), request.get("params"))}
            else:
                raise ValueError(f"Unknown output command: {command}")
            result["id"] = request.get("id")
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            result = {"id": request.get("id") if isinstance(request, dict) else None, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
