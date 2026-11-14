"""The demo service's API and abuse controls, with a fake runner: no model, no Docker."""

import json
import threading
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from patchpilot.demo.app import Limits, create_app, trace_events

RESULT = {
    "status": "candidate_verified",
    "resolved": True,
    "diff": "+fix",
    "checks": [],
    "proposals": [],
    "model": "qwen3:8b",
    "model_turns": 1,
    "tool_calls": 1,
    "tokens": {"input": 1, "output": 1},
    "duration_seconds": 1.0,
    "detail": "",
}


def _write_trace(task_id: str, directory: Path) -> None:
    run = directory / f"{task_id}-1"
    run.mkdir(parents=True)
    lines = [
        {"type": "start"},
        {
            "type": "response",
            "turn": {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.py"}}]},
        },
        {
            "type": "transition",
            "from": "propose",
            "to": "evaluate",
            "reason": "the model gave a proposal",
        },
        {"type": "finish"},
    ]
    (run / "trace.jsonl").write_text("\n".join(json.dumps(line) for line in lines))


def _client(tmp_path: Path, runner: Any = None, **limits: Any) -> TestClient:
    def ok(task_id: str, directory: Path) -> dict[str, Any]:
        _write_trace(task_id, directory)
        return RESULT

    app = create_app(runner=runner or ok, runs_dir=tmp_path, limits=Limits(**limits))
    return TestClient(app)


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def test_page_tasks_and_health(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert "Run a real repair" in client.get("/").text
    ids = [task["id"] for task in client.get("/api/tasks").json()]
    assert ids == ["inj-001-cachekit", "inj-002-exprcalc", "inj-003-rangespec", "inj-004-pager"]
    health = client.get("/api/health").json()
    assert health["ok"] and health["access_code_required"] is False
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_a_job_runs_and_reports_progress_and_result(tmp_path: Path) -> None:
    client = _client(tmp_path)
    accepted = client.post("/api/jobs", json={"task_id": "inj-001-cachekit"})
    assert accepted.status_code == 202
    job = _wait(client, accepted.json()["job_id"])
    assert job["state"] == "done" and job["result"]["resolved"] is True
    assert job["events"] == [
        'tool call: read_file {"path": "a.py"}',
        "propose -> evaluate: the model gave a proposal",
        "run finished",
    ]
    assert client.get("/api/jobs/nope").status_code == 404


def test_only_built_in_tasks_and_no_other_input(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for task in ("../evals/tasks/inj-001-cachekit", "https://github.com/x/y", "inj-999"):
        assert client.post("/api/jobs", json={"task_id": task}).status_code == 422
    assert client.post("/api/jobs", json={"command": "rm -rf /"}).status_code == 422


def test_access_code_is_enforced_when_set(tmp_path: Path) -> None:
    client = _client(tmp_path, access_code="s3cret-demo")
    assert client.get("/api/health").json()["access_code_required"] is True
    body = {"task_id": "inj-001-cachekit"}
    assert client.post("/api/jobs", json=body).status_code == 401
    assert client.post("/api/jobs", json=body, headers={"X-Demo-Code": "wrong"}).status_code == 401
    accepted = client.post("/api/jobs", json=body, headers={"X-Demo-Code": "s3cret-demo"})
    assert accepted.status_code == 202
    assert "s3cret-demo" not in json.dumps(_wait(client, accepted.json()["job_id"]))


def test_per_address_and_daily_limits(tmp_path: Path) -> None:
    client = _client(tmp_path, per_address_per_hour=2, per_day=10)
    body = {"task_id": "inj-002-exprcalc"}
    assert [client.post("/api/jobs", json=body).status_code for _ in range(3)] == [202, 202, 429]
    daily = _client(tmp_path / "d", per_address_per_hour=10, per_day=1)
    assert [daily.post("/api/jobs", json=body).status_code for _ in range(2)] == [202, 429]


def test_queue_is_bounded_and_one_job_runs_at_a_time(tmp_path: Path) -> None:
    gate = threading.Event()
    running: list[str] = []

    def slow(task_id: str, directory: Path) -> dict[str, Any]:
        running.append(task_id)
        gate.wait(5)
        return RESULT

    client = _client(tmp_path, runner=slow, per_address_per_hour=10, max_queue=1)
    body = {"task_id": "inj-003-rangespec"}
    first = client.post("/api/jobs", json=body).json()["job_id"]
    for _ in range(100):
        if running:
            break
        time.sleep(0.01)
    second = client.post("/api/jobs", json=body)
    assert second.status_code == 202
    assert client.get(f"/api/jobs/{second.json()['job_id']}").json()["queue_position"] == 1
    assert client.post("/api/jobs", json=body).status_code == 503
    assert running == ["inj-003-rangespec"], "only one job runs at a time"
    gate.set()
    assert _wait(client, first)["state"] == "done"


def test_a_failing_run_is_reported_and_the_worker_continues(tmp_path: Path) -> None:
    calls: list[str] = []

    def flaky(task_id: str, directory: Path) -> dict[str, Any]:
        calls.append(task_id)
        if len(calls) == 1:
            raise RuntimeError("the sandbox engine is not available")
        return RESULT

    client = _client(tmp_path, runner=flaky, per_address_per_hour=10)
    first = client.post("/api/jobs", json={"task_id": "inj-004-pager"}).json()["job_id"]
    failed = _wait(client, first)
    assert failed["state"] == "failed" and "sandbox engine" in failed["error"]
    second = client.post("/api/jobs", json={"task_id": "inj-004-pager"}).json()["job_id"]
    assert _wait(client, second)["state"] == "done"


def test_trace_events_ignore_broken_lines(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('not json\n{"type": "finish"}\n')
    assert trace_events(trace) == ["run finished"]
    assert trace_events(tmp_path / "missing.jsonl") == []
