import json
from pathlib import Path
import subprocess
import sys

import pytest


DEMO = Path(__file__).resolve().parents[1] / "examples" / "verification-demo"


def invoke(*options):
    run = subprocess.run([sys.executable, "-m", "q1lens", *map(str, options), "--json"], capture_output=True,
                         text=True, timeout=35, check=False)
    assert not run.stderr
    report = json.loads(run.stdout)
    assert run.returncode == {"proved": 0, "counterexample": 1, "unknown": 2}[report["status"]]
    return report


def test_queue_and_assertions_report_the_exact_replay_event():
    report = invoke("verify", DEMO / "queue.q1asm", "--include-replay")
    queue, = report["checks"]
    assert queue["safe_prefix"] == {"name": "N", "min": 1, "max": 37}
    assert queue["first_failure"] == {"name": "N", "value": 38}
    witness = queue["counterexample"]
    assert witness["inputs"] == {"N": 38}
    event, = [e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"]]
    assert (event["kind"], event["t0"]["value"]) == ("underflow_warning", 1188)

    report = invoke("verify", DEMO / "assertions.q1asm", "--include-replay")
    timing, bound = [c for c in report["checks"] if c["kind"] == "assertion"]
    assert timing["status"] == "proved" and timing["domain"] == {"N": [1, 513]}
    witness = bound["counterexample"]
    assert (witness["inputs"], witness["register"], witness["value"]) == ({"N": 513}, "R2", 1024)
    assert any(w["register"] == "R2" and w["value"]["value"] == 1024
               for w in witness["timeline"]["register_history"])


@pytest.mark.parametrize("args,status", [
    (["verify", DEMO / "alignment/q1timeline.yml", "--alignment"], "proved"),
    (["verify", DEMO / "alignment/late/q1timeline.yml", "--alignment"], "counterexample"),
    (["equiv", DEMO / "before.q1asm", DEMO / "optimized.q1asm"], "proved"),
    (["equiv", DEMO / "before.q1asm", DEMO / "changed.q1asm"], "counterexample"),
    (["verify", DEMO / "alignment/q1timeline.yml", "--alignment", "--max-cases", "1"], "unknown"),
    (["verify", DEMO / "absent.q1asm"], "unknown"),
])
def test_ci_status_and_witnesses_for_the_demo(args, status):
    report = invoke(*args, "--include-replay")
    assert report["status"] == status
    check, = report["checks"]
    if status == "proved":
        assert check["checked_cases"] == (16 if check["kind"] == "alignment" else 8)
    if status == "counterexample":
        witness = check["counterexample"]
        assert witness["event_id"] in {e["id"] for e in witness["timeline"]["events"]}
        events = witness["observations"]
        if check["kind"] == "alignment":
            assert sum(witness["inputs"].values()) % 2 == 1
            assert events["seq3"]["time_ns"] - events["seq0"]["time_ns"] == 20
        else:
            assert witness["inputs"] == {"N": 7}
            assert events["after"]["end_ns"] - events["before"]["end_ns"] == 4
