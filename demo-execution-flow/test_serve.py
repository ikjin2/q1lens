"""Exercise the browser-facing replay endpoint with the real engine."""
from http.client import HTTPConnection
from http.server import HTTPServer
import json
from threading import Thread

import pytest

import serve


@pytest.fixture
def server():
    with HTTPServer(("127.0.0.1", 0), serve.Handler) as instance:
        worker = Thread(target=instance.serve_forever, daemon=True)
        worker.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            worker.join()


def post(server, payload, origin=None):
    connection = HTTPConnection("127.0.0.1", server.server_port)
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    try:
        connection.request("POST", "/api/run", json.dumps(payload), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_live_branch_roundtrip_preserves_condition_and_can_reset(server):
    payload = {"caseId": "retry", "scenarioId": "normal", "choices": []}
    status, original = post(server, payload)
    assert status == 200
    branch = next(step for step in original["steps"] if step["op"] == "jge")
    payload["choices"] = [{"pc": branch["pc"], "occurrence": 1, "taken": True}]
    status, alternate = post(server, payload)
    assert status == 200
    changed = next(step for step in alternate["steps"] if step["op"] == "jge")
    assert changed["branch"]["naturalTaken"] is False
    assert changed["branch"]["taken"] is True
    assert changed["reads"] == branch["reads"]
    assert changed["nextLine"] == 17
    assert len(alternate["steps"]) < len(original["steps"])
    status, restored = post(server, {**payload, "choices": []})
    assert status == 200
    assert restored == original


@pytest.mark.parametrize("payload", [
    {},
    {"caseId": "unknown", "scenarioId": "normal", "choices": []},
    {"caseId": "retry", "scenarioId": "unknown", "choices": []},
    {"caseId": "retry", "scenarioId": "normal", "choices": [{"pc": 0, "occurrence": 1, "taken": True}]},
    {"caseId": "retry", "scenarioId": "normal", "choices": "invalid"},
])
def test_bad_replay_returns_json_error(server, payload):
    status, response = post(server, payload)
    assert status == 400
    assert response["error"]


def test_other_origins_cannot_request_execution(server):
    status, response = post(server, {"caseId": "retry", "scenarioId": "normal", "choices": []}, "https://example.com")
    assert status == 403
    assert response["error"]
