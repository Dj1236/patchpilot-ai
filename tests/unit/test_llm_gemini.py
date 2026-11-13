"""The Gemini adapter, through the real SDK with every request answered locally.

No request leaves the machine and nothing is generated.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from patchpilot.agent.baseline import RunLimits, RunStatus, run_baseline
from patchpilot.agent.prompts import tool_specs
from patchpilot.agent.proposal import PROPOSAL_SCHEMA
from patchpilot.agent.report import public_report
from patchpilot.agent.trace import TraceWriter, replay_model, secrets_from_environment
from patchpilot.llm.base import ModelError, ModelRequest, Usage
from patchpilot.llm.budget import RunBudget, SpendLedger
from patchpilot.llm.gemini_client import connect, padded
from patchpilot.sandbox.models import SandboxLimits
from tests.gemini_wire import (
    MODEL,
    FakeClock,
    Reply,
    Wire,
    answer,
    api_error,
    call,
    interaction,
    thought,
)
from tests.unit.test_baseline import BUG, COMMANDS, FILES, GOOD_SANDBOX, ScriptedSandbox, proposal

SECRET = "patchpilot-gemini-canary-not-a-real-key"
FIRST = {"role": "user", "content": "Fix the bug."}


@pytest.fixture(autouse=True)
def canary_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)


def request(*messages: dict[str, Any], **changes: Any) -> ModelRequest:
    values: dict[str, Any] = {
        "model": MODEL,
        "system": "You fix bugs.",
        "messages": messages or (FIRST,),
        "tools": tool_specs(),
        "output_schema": PROPOSAL_SCHEMA,
        "max_tokens": 16_000,
    }
    return ModelRequest(**{**values, **changes})


# -- the request ------------------------------------------------------------------------


def test_the_request_is_stateless_and_declares_only_the_function_tools() -> None:
    wire = Wire([interaction(answer({"done": True}))])

    turn = wire.model().complete(request())

    (body,) = wire.sent
    assert body["store"] is False, "the provider must keep no copy of the conversation"
    assert body["model"] == MODEL and body["system_instruction"] == "You fix bugs."
    assert body["generation_config"] == {"max_output_tokens": 16_000, "thinking_level": "medium"}
    assert body["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "Fix the bug."}]}
    ]
    assert {tool["type"] for tool in body["tools"]} == {"function"}
    assert [tool["name"] for tool in body["tools"]] == [spec.name for spec in tool_specs()]
    assert body["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": PROPOSAL_SCHEMA,
    }
    assert not {"service_tier", "previous_interaction_id", "background", "agent"} & set(body)
    assert SECRET not in json.dumps(body)
    assert turn.stop_reason == "end_turn" and json.loads(turn.text) == {"done": True}
    assert turn.model == MODEL and turn.request_id == "interaction-1"


def test_model_steps_and_thought_signatures_go_back_exactly_as_received() -> None:
    received = [
        thought("c2lnbmF0dXJlLW9uZQ=="),
        call("call-a", "read_file", {"path": "src/pkg/core.py"}),
        call("call-b", "search_code", {"query": "total"}),
    ]
    wire = Wire([interaction(*received), interaction(answer({"done": True}))])
    model = wire.model()

    first = model.complete(request())
    results = [
        {"type": "tool_result", "tool_use_id": "call-a", "content": '{"ok": 1}', "is_error": False},
        {
            "type": "tool_result",
            "tool_use_id": "call-b",
            "content": '{"error": "x"}',
            "is_error": True,
        },
    ]
    model.complete(
        request(
            FIRST,
            {"role": "assistant", "content": list(first.content)},
            {"role": "user", "content": results},
        )
    )

    assert first.stop_reason == "tool_use"
    assert [(item.id, item.name, item.arguments) for item in first.tool_calls] == [
        ("call-a", "read_file", {"path": "src/pkg/core.py"}),
        ("call-b", "search_code", {"query": "total"}),
    ]
    sent = wire.sent[1]["input"]
    assert sent[1:4] == received, "thought steps and signatures must not be altered"
    assert sent[4:] == [
        {
            "type": "function_result",
            "call_id": "call-a",
            "name": "read_file",
            "result": '{"ok": 1}',
            "is_error": False,
        },
        {
            "type": "function_result",
            "call_id": "call-b",
            "name": "search_code",
            "result": '{"error": "x"}',
            "is_error": True,
        },
    ]


def test_an_effort_the_model_does_not_offer_is_refused_before_anything_is_sent() -> None:
    wire = Wire()

    with pytest.raises(ModelError) as caught:
        wire.model().count_input_tokens(request(effort="max"))

    assert caught.value.kind == "not_configured" and wire.requests == []


# -- the response -----------------------------------------------------------------------

BLOCKED = [{"code": "https://errors.example/safety", "message": "blocked"}]


@pytest.mark.parametrize(
    ("reply", "stop"),
    [
        (interaction(answer("{}")), "end_turn"),
        (interaction(call("c", "list_files", {})), "tool_use"),
        (interaction(answer("{"), status="incomplete"), "max_tokens"),
        (interaction(answer("{"), status="requires_continuation"), "max_tokens"),
        # A cut-off turn is never acted on, even if it holds a tool call.
        (interaction(call("c", "list_files", {}), status="incomplete"), "max_tokens"),
        (interaction(status="failed", errors=BLOCKED), "refusal"),
        (interaction(status="failed"), "other"),
        (
            interaction(
                {"type": "model_output", "content": [], "error": {"code": 3, "message": "SAFETY"}}
            ),
            "refusal",
        ),
        (interaction(status="cancelled"), "other"),
    ],
)
def test_statuses_map_to_stop_reasons(reply: dict[str, Any], stop: str) -> None:
    assert Wire([reply]).model().complete(request()).stop_reason == stop


def test_usage_counts_thinking_as_output_and_tool_use_as_input() -> None:
    usage = {
        "total_input_tokens": 100,
        "total_tool_use_tokens": 5,
        "total_output_tokens": 20,
        "total_thought_tokens": 7,
        "total_cached_tokens": 40,
    }

    turn = Wire([interaction(answer("{}"), usage=usage)]).model().complete(request())

    assert turn.usage == Usage(input_tokens=105, output_tokens=27, cache_read_input_tokens=40)


@pytest.mark.parametrize("reported", [MODEL, f"models/{MODEL}", f"{MODEL}-001"])
def test_the_pinned_model_is_accepted_under_its_usual_spellings(reported: str) -> None:
    turn = Wire([interaction(answer("{}"), model=reported)]).model().complete(request())

    assert turn.model.startswith(MODEL)


@pytest.mark.parametrize(
    "reported",
    ["gemini-3.5-flash-lite", f"{MODEL}-lite", f"{MODEL}-preview", "gemini-4-carbon", ""],
)
def test_an_answer_from_a_different_or_unnamed_model_is_an_error_not_a_fallback(
    reported: str,
) -> None:
    wire = Wire([interaction(answer("{}"), model=reported)])

    with pytest.raises(ModelError, match="different model") as caught:
        wire.model().complete(request())

    assert caught.value.kind == "api_error"


def test_a_cut_off_answer_is_not_continued_with_a_hidden_request() -> None:
    cut_off = interaction(
        answer("{"), status="requires_continuation", continuation_token="resume-here"
    )
    wire = Wire([cut_off, interaction(answer("}"))])

    turn = wire.model().complete(request())

    assert turn.stop_reason == "max_tokens"
    assert len(wire.sent) == 1, "a continuation hop would be an uncounted request"


# -- failures ---------------------------------------------------------------------------

DAILY = {
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": "quota",
        "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
    }
}


@pytest.mark.parametrize(
    ("reply", "kind", "wait"),
    [
        (api_error(429, "rate_limit_exceeded"), "rate_limited", 60.0),
        (api_error(429, "too_many_requests"), "rate_limited", 60.0),
        (api_error(429, "quota_exceeded"), "quota_exhausted", 0.0),
        ((429, DAILY), "quota_exhausted", 0.0),
        (api_error(503, "service_unavailable"), "unavailable", 30.0),
        (api_error(500, "api_error"), "unavailable", 30.0),
        (api_error(401, "authentication"), "api_error", 0.0),
        (api_error(402, "payment_required"), "api_error", 0.0),
        (api_error(404, "model_not_found"), "api_error", 0.0),
        (httpx.ReadTimeout("slow"), "timeout", 0.0),
        (httpx.ConnectError("refused"), "connection", 0.0),
    ],
)
def test_failures_are_classified_and_never_retried_by_the_sdk(
    reply: Reply, kind: str, wait: float
) -> None:
    wire = Wire([reply, interaction(answer("{}"))])

    with pytest.raises(ModelError) as caught:
        wire.model().complete(request())

    assert caught.value.kind == kind and caught.value.retry_after_seconds == wait
    assert len(wire.sent) == 1, "a hidden retry would be an uncounted request"
    assert SECRET not in str(caught.value) and "provider detail" not in str(caught.value)


def test_a_client_error_names_the_providers_short_code() -> None:
    with pytest.raises(ModelError, match=r"HTTP 402 \(payment_required\)"):
        Wire([api_error(402, "payment_required")]).model().complete(request())


# -- sizing -----------------------------------------------------------------------------


def test_input_is_estimated_from_the_flattened_request_with_a_margin() -> None:
    wire = Wire(count=1800)

    tokens = wire.model().count_input_tokens(request())

    assert tokens == padded(1800) == 2236
    assert wire.requests == [
        f"POST https://generativelanguage.googleapis.com/v1/models/{MODEL}:countTokens"
    ]
    counted = wire.counted[0]["contents"][0]["parts"][0]["text"]
    assert "You fix bugs." in counted and "read_file" in counted and "Fix the bug." in counted
    assert wire.sent == [], "sizing must not generate anything"


def test_a_failed_count_is_reported_not_guessed() -> None:
    wire = Wire(count=api_error(429, "rate_limit_exceeded"))

    with pytest.raises(ModelError) as caught:
        wire.model().count_input_tokens(request())

    assert caught.value.kind == "rate_limited"


# -- configuration ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GOOGLE_GENAI_USE_VERTEXAI", "true"),
        ("GOOGLE_GENAI_USE_ENTERPRISE", "1"),
        ("GOOGLE_API_KEY", "another-key-entirely"),
    ],
)
def test_billed_modes_and_ambiguous_keys_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ModelError, match=name) as caught:
        connect()

    assert caught.value.kind == "not_configured" and value not in str(caught.value)


def test_a_missing_key_is_reported_without_contacting_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY")

    with pytest.raises(ModelError, match="GEMINI_API_KEY") as caught:
        connect()

    assert caught.value.kind == "not_configured"


def test_each_provider_runs_only_its_pinned_model() -> None:
    gemini = RunLimits.for_provider("gemini")

    assert (gemini.provider, gemini.model) == ("gemini", MODEL)
    assert gemini.digest() != RunLimits().digest()
    with pytest.raises(ValueError, match="runs only"):
        RunLimits(provider="gemini")
    with pytest.raises(ValueError, match="runs only"):
        RunLimits(model=MODEL)


# -- the whole baseline through the adapter ------------------------------------------------


def _run(model: Any, directory: Path, ledger: SpendLedger, run_id: str) -> Any:
    record, _ = run_baseline(
        task_id="demo",
        bug=BUG,
        files=FILES,
        source_roots=("src",),
        commands=COMMANDS,
        sandbox_limits=SandboxLimits(),
        model=model,
        sandbox=ScriptedSandbox(GOOD_SANDBOX),
        budget=RunBudget(ledger, run_id, 0.75),
        trace=TraceWriter(directory, secrets_from_environment()),
        limits=RunLimits.for_provider("gemini"),
        run_id=run_id,
    )
    return record


def test_the_baseline_runs_unchanged_and_its_trace_replays(tmp_path: Path) -> None:
    wire = Wire(
        [
            interaction(thought(), call("call-a", "list_files", {})),
            interaction(
                thought("c2lnbmF0dXJlLXR3bw=="),
                call("call-b", "read_file", {"path": "src/pkg/core.py"}),
                call("call-c", "search_code", {"query": "total"}),
            ),
            interaction(answer(proposal())),
        ]
    )
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=5.0)

    record = _run(wire.model(), tmp_path / "live", ledger, "live")
    replayed = _run(
        replay_model(tmp_path / "live" / "trace.jsonl"), tmp_path / "replay", ledger, "replay"
    )

    assert record.status is RunStatus.CANDIDATE_VERIFIED
    assert (record.model_turns, record.tool_calls) == (3, 3)
    assert record.manifest.provider == "gemini"
    assert record.manifest.model_requested == MODEL
    assert record.manifest.models_reported == (MODEL,)
    assert record.cost_micro_usd > 0, "the ledger still bounds list-price exposure"
    # The third request carries both earlier turns, signatures intact.
    signatures = [step["signature"] for step in wire.sent[2]["input"] if step["type"] == "thought"]
    assert signatures == ["c2lnbmF0dXJlLTE=", "c2lnbmF0dXJlLXR3bw=="]
    assert SECRET not in (tmp_path / "live" / "trace.jsonl").read_text()
    assert (replayed.status, replayed.diff) == (record.status, record.diff)
    report = public_report(record)
    assert "Provider: `gemini`" in report and "list-price upper bound" in report


# -- free-tier pacing and the attempt limit -------------------------------------------------

LIMITED = api_error(429, "rate_limit_exceeded")


def _answers(count: int) -> list[Reply]:
    return [interaction(answer("{}")) for _ in range(count)]


def test_generation_is_paced_to_four_requests_in_any_sixty_seconds() -> None:
    clock, wire = FakeClock(), Wire(_answers(8))
    model = wire.model(clock.limiter())
    sent_at: list[float] = []

    for _ in range(8):
        model.complete(request())
        sent_at.append(clock.now)

    assert clock.sleeps == [60.0], "the fifth request waits for the first to leave the window"
    assert sent_at == [0.0] * 4 + [60.0] * 4
    for start in sent_at:
        assert sum(start <= moment < start + 60.0 for moment in sent_at) <= 4


def test_requests_that_are_already_spread_out_are_not_delayed() -> None:
    clock, wire = FakeClock(), Wire(_answers(8))
    model = wire.model(clock.limiter())

    for _ in range(8):
        model.complete(request())
        clock.now += 15.0

    assert clock.sleeps == []


def test_a_partly_elapsed_window_is_waited_out_only_for_the_remainder() -> None:
    clock, wire = FakeClock(), Wire(_answers(5))
    model = wire.model(clock.limiter())

    for _ in range(4):
        model.complete(request())
        clock.now += 10.0
    model.complete(request())

    assert clock.sleeps == [20.0]


def test_no_more_than_eight_generation_attempts_are_made_and_failures_count() -> None:
    clock = FakeClock()
    wire = Wire([LIMITED, *_answers(2), LIMITED, LIMITED, *_answers(3), *_answers(2)])
    model = wire.model(clock.limiter())
    outcomes: list[str] = []

    for _ in range(10):
        try:
            outcomes.append(model.complete(request()).stop_reason)
        except ModelError as error:
            outcomes.append(error.kind)

    assert outcomes[:8].count("rate_limited") == 3 and outcomes[8:] == ["attempt_limit"] * 2
    assert model.limiter.attempts == 8 and len(wire.sent) == 8, "retries are attempts too"


def test_sizing_is_neither_paced_nor_counted_and_stops_once_attempts_are_gone() -> None:
    clock, wire = FakeClock(), Wire(_answers(2))
    model = wire.model(clock.limiter(max_attempts=2))

    for _ in range(10):
        model.count_input_tokens(request())
    assert (model.count_requests, model.limiter.attempts, clock.sleeps) == (10, 0, [])

    model.complete(request())
    model.complete(request())
    with pytest.raises(ModelError) as caught:
        model.count_input_tokens(request())

    assert caught.value.kind == "attempt_limit"
    assert len(wire.counted) == 10, "no count request is made for a run that cannot continue"


def _endless() -> list[Reply]:
    return [interaction(thought(), call(f"call-{index}", "list_files", {})) for index in range(20)]


def test_a_run_that_needs_a_ninth_attempt_stops_with_nothing_more_reserved(
    tmp_path: Path,
) -> None:
    clock, wire = FakeClock(), Wire(_endless())
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=5.0)

    record = _run(wire.model(clock.limiter()), tmp_path / "run", ledger, "run")

    assert record.status is RunStatus.FAILED_INFRASTRUCTURE and "attempt_limit" in record.detail
    assert record.model_turns == 8 and len(wire.sent) == 8 and len(wire.counted) == 8
    assert clock.sleeps == [60.0]
    assert ledger.committed() == record.cost_micro_usd, "the refused ninth attempt cost nothing"
    assert record.cost_micro_usd < 750_000


def test_a_rate_limited_turn_is_retried_once_and_both_attempts_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("patchpilot.agent.baseline.time.sleep", waits.append)
    clock, wire = FakeClock(), Wire([LIMITED, interaction(answer(proposal()))])
    model = wire.model(clock.limiter())

    record = _run(model, tmp_path / "run", SpendLedger(tmp_path / "ledger.jsonl", 5.0), "run")

    assert record.status is RunStatus.CANDIDATE_VERIFIED and waits == [60.0]
    assert (model.limiter.attempts, model.count_requests, len(wire.sent)) == (2, 2, 2)


@pytest.mark.parametrize("reply", [api_error(429, "quota_exceeded"), (429, DAILY)])
def test_a_spent_daily_quota_ends_the_run_after_one_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: Reply
) -> None:
    monkeypatch.setattr(
        "patchpilot.agent.baseline.time.sleep", lambda _: pytest.fail("a spent quota was waited on")
    )
    clock, wire = FakeClock(), Wire([reply, interaction(answer(proposal()))])
    model = wire.model(clock.limiter())

    record = _run(model, tmp_path / "run", SpendLedger(tmp_path / "ledger.jsonl", 5.0), "run")

    assert record.status is RunStatus.FAILED_INFRASTRUCTURE and "quota_exhausted" in record.detail
    assert model.limiter.attempts == 1 and len(wire.sent) == 1
    assert record.manifest.models_reported == (), "no other model was tried"


def test_the_live_client_is_built_with_the_pilot_limits() -> None:
    from patchpilot.llm.gemini_client import GenerationLimiter

    limiter = GenerationLimiter()

    assert (limiter._per_window, limiter._window, limiter._max_attempts) == (4, 60.0, 8)


# -- provider unavailability ------------------------------------------------------------------

UNAVAILABLE = api_error(503, "service_unavailable")


def _waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr("patchpilot.agent.baseline.time.sleep", waits.append)
    return waits


def test_two_503_answers_wait_once_then_stop_and_the_report_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits, clock = _waits(monkeypatch), FakeClock()
    wire = Wire([UNAVAILABLE, UNAVAILABLE, interaction(answer(proposal()))])
    model = wire.model(clock.limiter())
    ledger = SpendLedger(tmp_path / "ledger.jsonl", 5.0)

    record = _run(model, tmp_path / "run", ledger, "run")

    assert record.status is RunStatus.FAILED_INFRASTRUCTURE
    assert record.detail == (
        "the model request failed (unavailable): "
        "the model API returned HTTP 503 (service_unavailable)"
    )
    assert waits == [30.0], "one pause before the one retry, none after it"
    assert (record.model_turns, record.generation_attempts, record.retries) == (0, 2, 1)
    assert record.token_count_requests == 2
    assert len(wire.sent) == 2 and model.limiter.attempts == 2, "the retry allowance is unchanged"
    assert record.manifest.models_reported == (), "no other model was tried"
    # Each failure is charged its full reservation, and stays under the run cap.
    assert ledger.committed() == record.cost_micro_usd
    assert 0 < record.cost_micro_usd < 750_000
    report = public_report(record)
    assert "HTTP 503 (service_unavailable)" in report
    assert "**Generation attempts:** 2, of which 1 were retries" in report
    assert "**token-count requests:** 2" in report and "**Model turns:** 0" in report
    assert "not a bill" in report and "provider detail" not in report and SECRET not in report


def test_an_answer_after_one_503_completes_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits, clock = _waits(monkeypatch), FakeClock()
    wire = Wire([UNAVAILABLE, interaction(answer(proposal()))])
    model = wire.model(clock.limiter())

    record = _run(model, tmp_path / "run", SpendLedger(tmp_path / "ledger.jsonl", 5.0), "run")

    assert record.status is RunStatus.CANDIDATE_VERIFIED and waits == [30.0]
    assert (record.model_turns, record.generation_attempts, record.retries) == (1, 2, 1)
    assert record.token_count_requests == 2 and len(wire.sent) == 2
    assert clock.sleeps == [], "two requests are well inside the pacing window"


def test_a_connection_failure_is_not_reported_as_the_provider_being_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits, clock = _waits(monkeypatch), FakeClock()
    wire = Wire([httpx.ConnectError("refused"), httpx.ConnectError("refused")])

    record = _run(
        wire.model(clock.limiter()), tmp_path / "run", SpendLedger(tmp_path / "l.jsonl", 5.0), "run"
    )

    assert record.detail == (
        "the model request failed (connection): the model API could not be reached"
    )
    assert waits == [] and record.generation_attempts == 2


def test_a_clean_run_reports_its_attempts_without_retries(tmp_path: Path) -> None:
    clock = FakeClock()
    wire = Wire(
        [interaction(thought(), call("call-a", "list_files", {})), interaction(answer(proposal()))]
    )

    record = _run(
        wire.model(clock.limiter()), tmp_path / "run", SpendLedger(tmp_path / "l.jsonl", 5.0), "run"
    )

    assert (record.model_turns, record.generation_attempts, record.retries) == (2, 2, 0)
    assert record.token_count_requests == 2
