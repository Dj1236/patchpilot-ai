"""The diagnostic probe, through the real SDK with every request answered locally.

No request leaves the machine.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.llm.base import ModelError, Usage
from patchpilot.llm.budget import SpendLedger
from patchpilot.llm.gemini_probe import PROMPT, run_probe
from tests.gemini_wire import MODEL, FakeClock, Reply, Wire, answer, api_error, interaction

SECRET = "patchpilot-probe-canary-not-a-real-key"
PROVIDER_TEXT = "This model is experiencing high demand. Request id 0xDEADBEEF."
RESERVED = (len(PROMPT) + 256) * 2 + 64 * 8
runner = CliRunner()


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATCHPILOT_PROVIDER", "gemini")
    monkeypatch.setenv("PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED", "1")
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)


def _probe(monkeypatch: pytest.MonkeyPatch, wire: Wire) -> Any:
    monkeypatch.setattr(cli, "gemini_connect", lambda *args, **kwargs: wire.client())
    return runner.invoke(cli.app, ["probe"])


def _record(tmp_path: Path) -> dict[str, Any]:
    (path,) = tmp_path.glob("probes/*/probe.json")
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def _ledger(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]


def test_one_minimal_request_is_sent_and_an_answer_ends_the_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    usage = {"total_input_tokens": 7, "total_output_tokens": 2, "total_thought_tokens": 10}
    wire = Wire([interaction(answer("OK"), usage=usage), interaction(answer("again"))])

    result = _probe(monkeypatch, wire)

    assert result.exit_code == 0, result.output
    assert wire.sent == [
        {
            "model": MODEL,
            "input": "Reply with exactly OK.",
            "generation_config": {"max_output_tokens": 64},
            "store": False,
        }
    ], "exactly one request: no tools, no schema, no thinking setting, no history"
    assert wire.counted == [], "the probe makes no other request either"
    assert wire.requests == ["POST https://generativelanguage.googleapis.com/v1/interactions"]
    for line in (
        "response received: yes",
        "http status: 200",
        "interaction status: completed",
        f"model reported: {MODEL}",
        "answer was exactly OK: yes",
        "tokens reported by the provider: 7 input, 12 output",
        "one generation request was sent; nothing was retried",
    ):
        assert line in result.output
    record = _record(tmp_path)
    assert record["result"]["usage"]["output_tokens"] == 12
    assert record["ledger"] == {
        "reserved_micro_usd": RESERVED,
        "committed_micro_usd": 7 * 2 + 12 * 8,
    }
    reserve, settle = _ledger(tmp_path)
    assert reserve["micro_usd"] == RESERVED and reserve["run_id"].startswith("probe-")
    assert settle["micro_usd"] == 110
    assert SECRET not in result.output + json.dumps(record)


def test_a_503_is_recorded_and_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wire = Wire([api_error(503, "service_unavailable", PROVIDER_TEXT), interaction(answer("OK"))])

    result = _probe(monkeypatch, wire)

    assert result.exit_code == 1
    assert len(wire.sent) == 1, "a retry would be a second generation request"
    for line in (
        "response received: no",
        "http status: 503",
        "provider code: service_unavailable",
        "reason: the provider is temporarily overloaded or down",
    ):
        assert line in result.output
    record = _record(tmp_path)
    everything = result.output + json.dumps(record) + (tmp_path / "ledger.jsonl").read_text()
    assert "high demand" not in everything and "DEADBEEF" not in everything
    assert SECRET not in everything and "Traceback" not in everything
    # Charged in full, never assumed free.
    assert record["ledger"] == {"reserved_micro_usd": RESERVED, "committed_micro_usd": RESERVED}
    assert SpendLedger(tmp_path / "ledger.jsonl", 5.0).committed() == RESERVED


@pytest.mark.parametrize(
    ("reply", "status", "code", "reason"),
    [
        (api_error(429, "quota_exceeded"), "429", "quota_exceeded", "the daily quota is used up"),
        (api_error(429, "rate_limit_exceeded"), "429", "rate_limit_exceeded", "per-minute"),
        (api_error(400, "invalid_request"), "400", "invalid_request", "malformed"),
        (api_error(500, "made_up_code"), "500", "unrecognized", "not classified"),
        ((503, {"error": {"code": "<b>overloaded</b>"}}), "503", "none", "not classified"),
        (httpx.ReadTimeout("slow"), "none", "none", "no answer within the time limit"),
        (httpx.ConnectError("refused"), "none", "none", "could not be reached"),
    ],
)
def test_failures_show_only_whitelisted_codes(
    monkeypatch: pytest.MonkeyPatch, reply: Reply, status: str, code: str, reason: str
) -> None:
    wire = Wire([reply, interaction(answer("OK"))])

    result = _probe(monkeypatch, wire)

    assert result.exit_code == 1 and len(wire.sent) == 1
    assert f"http status: {status}" in result.output
    assert f"provider code: {code}" in result.output and reason in result.output
    assert "made_up_code" not in result.output and "overloaded</b>" not in result.output
    assert "provider detail" not in result.output


def test_a_cut_off_answer_is_not_continued(monkeypatch: pytest.MonkeyPatch) -> None:
    cut_off = interaction(
        answer("O"), status="requires_continuation", continuation_token="resume-here"
    )
    wire = Wire([cut_off, interaction(answer("K"))])

    result = _probe(monkeypatch, wire)

    assert result.exit_code == 0 and len(wire.sent) == 1
    assert "interaction status: requires_continuation" in result.output
    assert "answer was exactly OK: no" in result.output


@pytest.mark.parametrize(
    ("name", "value", "exit_code", "fragment"),
    [
        ("PATCHPILOT_PROVIDER", "anthropic", 2, "Gemini only"),
        (
            "PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED",
            "0",
            2,
            "PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED=1",
        ),
        ("GOOGLE_GENAI_USE_VERTEXAI", "true", 1, "billed"),
        ("GOOGLE_API_KEY", "some-other-key", 1, "GEMINI_API_KEY only"),
        ("PATCHPILOT_MAX_COST_USD", "0.0005", 1, "does not fit"),
        ("PATCHPILOT_RUN_CAP_USD", "0.0005", 1, "would exceed"),
    ],
)
def test_the_probe_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    exit_code: int,
    fragment: str,
) -> None:
    monkeypatch.setenv(name, value)
    wire = Wire([interaction(answer("OK"))])
    if name.startswith("PATCHPILOT_"):
        monkeypatch.setattr(cli, "gemini_connect", lambda *args, **kwargs: wire.client())

    result = runner.invoke(cli.app, ["probe"])

    assert result.exit_code == exit_code and fragment in result.output, result.output
    assert wire.requests == [] and not list(tmp_path.glob("probes/*"))
    assert "Traceback" not in result.output


def test_no_key_and_an_unreadable_ledger_stop_the_probe_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wire = Wire([interaction(answer("OK"))])
    (tmp_path / "ledger.jsonl").write_text("not json\n")
    unreadable = _probe(monkeypatch, wire)

    monkeypatch.undo()
    monkeypatch.setenv("PATCHPILOT_PROVIDER", "gemini")
    monkeypatch.setenv("PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED", "1")
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path / "other"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    no_key = runner.invoke(cli.app, ["probe"])

    assert unreadable.exit_code == 1 and "refusing to spend" in unreadable.output
    assert no_key.exit_code == 1 and "GEMINI_API_KEY" in no_key.output
    assert wire.requests == []
    for result in (unreadable, no_key):
        assert "probe not sent" in result.output and "Traceback" not in result.output


def test_the_probe_goes_through_the_attempt_limiter() -> None:
    clock, wire = FakeClock(), Wire([interaction(answer("OK"))] * 3)
    limiter = clock.limiter(max_attempts=2)
    client = wire.client()

    first = run_probe(client, MODEL, limiter=limiter, clock=clock)
    run_probe(client, MODEL, limiter=limiter, clock=clock)
    with pytest.raises(ModelError) as caught:
        run_probe(client, MODEL, limiter=limiter, clock=clock)

    assert caught.value.kind == "attempt_limit" and len(wire.sent) == 2
    assert first.usage == Usage(input_tokens=100, output_tokens=20) and limiter.attempts == 2
