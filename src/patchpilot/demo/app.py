"""The public demo: a small FastAPI service that runs one repair at a time.

Visitors pick one of the built-in synthetic tasks; nothing else can be
submitted. A single worker thread runs PatchPilot's normal repair pipeline
with the local Ollama model and the Docker sandbox, and the page polls for
progress read from the run's own trace. Abuse controls: an optional access
code, a per-address hourly limit, a daily limit, a short queue, one job at a
time, and every run budget of the agent itself. No GitHub token is used and
the model server is never exposed.
"""

import hashlib
import json
import os
import queue
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from patchpilot.eval.harness import TaskRun
from patchpilot.tasks.loader import discover_tasks

TASKS_ROOT = Path(__file__).resolve().parents[3] / "evals" / "tasks"
STATIC = Path(__file__).with_name("static")
MAX_EVENTS = 200
MAX_JOBS_KEPT = 200

Runner = Callable[[str, Path], dict[str, Any]]


@dataclass
class Limits:
    access_code: str = ""
    per_address_per_hour: int = 3
    per_day: int = 40
    max_queue: int = 3

    @classmethod
    def from_env(cls) -> "Limits":
        env = os.environ.get
        return cls(
            access_code=env("PATCHPILOT_DEMO_ACCESS_CODE", ""),
            per_address_per_hour=int(env("PATCHPILOT_DEMO_PER_ADDRESS_PER_HOUR", "3")),
            per_day=int(env("PATCHPILOT_DEMO_PER_DAY", "40")),
            max_queue=int(env("PATCHPILOT_DEMO_MAX_QUEUE", "3")),
        )


@dataclass
class Job:
    id: str
    task_id: str
    directory: Path
    created: float
    state: str = "queued"
    started: float | None = None
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: str = ""


class JobRequest(BaseModel):
    task_id: str


def trace_events(trace: Path) -> list[str]:
    """Readable progress lines from a run's trace. Model text is not echoed."""
    events: list[str] = []
    if not trace.is_file():
        return events
    for line in trace.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind == "response":
            turn = entry.get("turn") or {}
            for call in turn.get("tool_calls") or ():
                args = json.dumps(call.get("arguments", {}), sort_keys=True)[:160]
                events.append(f"tool call: {call.get('name')} {args}")
            if turn.get("stop_reason") == "end_turn":
                events.append("model answered with a proposal")
        elif kind == "transition":
            events.append(f"{entry.get('from')} -> {entry.get('to')}: {entry.get('reason')}")
        elif kind == "finish":
            events.append("run finished")
    return events[-MAX_EVENTS:]


def summarize(run: TaskRun) -> dict[str, Any]:
    """The public result of a run. Hidden tests are reported only as a verdict."""
    record = run.record
    verification = record.verification
    return {
        "status": record.status.value,
        "detail": record.detail,
        "resolved": bool(run.resolved) if run.graded else None,
        "diff": record.diff[:20_000],
        "checks": [
            {"name": step.name, "expected": step.expected, "held": step.ok}
            for step in (verification.steps if verification else ())
        ],
        "proposals": [
            {"outcome": attempt.status.value, "detail": attempt.detail[:300]}
            for attempt in record.attempts
        ],
        "model": ", ".join(record.manifest.models_reported) or record.manifest.model_requested,
        "model_turns": record.model_turns,
        "tool_calls": record.tool_calls,
        "tokens": {"input": record.usage.input_tokens, "output": record.usage.output_tokens},
        "duration_seconds": round(record.duration_seconds, 1),
    }


def live_runner(task_id: str, directory: Path) -> dict[str, Any]:
    """Run the real pipeline: local model, Docker sandbox, hidden-test grading."""
    from patchpilot.agent.baseline import RepairLimits, RunLimits
    from patchpilot.eval.harness import run_task
    from patchpilot.llm.budget import SpendLedger
    from patchpilot.llm.ollama_client import OllamaModel
    from patchpilot.sandbox.docker import DockerSandbox
    from patchpilot.tasks.loader import load_task

    sandbox = DockerSandbox()
    if not sandbox.is_available():
        raise RuntimeError("the sandbox engine is not available")
    run = run_task(
        load_task(TASKS_ROOT / task_id),
        1,
        model=OllamaModel(),
        sandbox=sandbox,
        ledger=SpendLedger(directory.parent / "ledger.jsonl", 5.0),
        run_cap_usd=0.75,
        output_dir=directory,
        limits=RunLimits.for_provider("ollama"),
        mode="repair",
        repair=RepairLimits(),
    )
    return summarize(run)


def create_app(
    *,
    runner: Runner = live_runner,
    runs_dir: Path | None = None,
    limits: Limits | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    bounds = limits or Limits.from_env()
    runs = runs_dir or Path(os.environ.get("PATCHPILOT_DEMO_RUNS_DIR", "runs/demo"))
    tasks = {
        loaded.task.task_id: loaded.task.agent_view().issue.title
        for loaded in discover_tasks(TASKS_ROOT)
        if loaded.task.task_id.startswith("inj-")
    }
    jobs: dict[str, Job] = {}
    pending: queue.Queue[Job] = queue.Queue()
    recent: dict[str, deque[float]] = {}
    day: deque[float] = deque()
    admit = threading.Lock()

    def work() -> None:
        while True:
            job = pending.get()
            job.state, job.started = "running", clock()
            try:
                job.result = runner(job.task_id, job.directory)
                job.state = "done"
            except Exception as error:  # a failed run must not stop the worker
                job.state, job.error = "failed", f"{type(error).__name__}: {str(error)[:200]}"
            job.finished = clock()

    threading.Thread(target=work, name="patchpilot-demo-worker", daemon=True).start()
    app = FastAPI(title="PatchPilot AI demo", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "model": "qwen3:8b (local, Ollama)",
            "queued": pending.qsize(),
            "running": sum(1 for job in jobs.values() if job.state == "running"),
            "access_code_required": bool(bounds.access_code),
        }

    @app.get("/api/tasks")
    def list_tasks() -> list[dict[str, str]]:
        return [{"id": task_id, "title": title} for task_id, title in sorted(tasks.items())]

    @app.post("/api/jobs", status_code=202)
    def submit(
        body: JobRequest, request: Request, x_demo_code: str = Header(default="")
    ) -> dict[str, str]:
        if bounds.access_code and not secrets.compare_digest(x_demo_code, bounds.access_code):
            raise HTTPException(401, "a valid access code is required")
        if body.task_id not in tasks:
            raise HTTPException(422, "only the built-in synthetic tasks can be run")
        address = request.client.host if request.client else "unknown"
        key = hashlib.sha256(address.encode()).hexdigest()[:16]
        now = clock()
        with admit:
            hits = recent.setdefault(key, deque())
            while hits and now - hits[0] > 3600:
                hits.popleft()
            while day and now - day[0] > 86_400:
                day.popleft()
            if len(hits) >= bounds.per_address_per_hour:
                raise HTTPException(429, "hourly limit for your address reached; try later")
            if len(day) >= bounds.per_day:
                raise HTTPException(429, "the demo's daily limit is reached; try tomorrow")
            if pending.qsize() >= bounds.max_queue:
                raise HTTPException(503, "the queue is full; try again in a few minutes")
            hits.append(now)
            day.append(now)
            job = Job(secrets.token_hex(12), body.task_id, Path(), now)
            job.directory = runs / f"job-{job.id}"
            finished = [j.id for j in jobs.values() if j.state in ("done", "failed")]
            for old in finished[: max(0, len(jobs) + 1 - MAX_JOBS_KEPT)]:
                jobs.pop(old, None)
            jobs[job.id] = job
            pending.put(job)
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        ahead = sum(1 for j in list(pending.queue) if j.created <= job.created)
        end = job.finished or clock()
        return {
            "job_id": job.id,
            "task_id": job.task_id,
            "state": job.state,
            "queue_position": ahead if job.state == "queued" else 0,
            "elapsed_seconds": round(end - job.started, 1) if job.started else 0,
            "events": trace_events(job.directory / f"{job.task_id}-1" / "trace.jsonl"),
            "result": job.result,
            "error": job.error,
        }

    return app
