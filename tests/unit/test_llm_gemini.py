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
from tests.gemini_wire import MODEL, Reply, Wire, answer, api_error, call, interaction, thought
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


def test_an_answer_from_a_different_model_is_an_error_not_a_fallback() -> None:
    wire = Wire([interaction(answer("{}"), model="gemini-3.5-flash-lite")])

    with pytest.raises(ModelError, match="different model") as caught:
        wire.model().complete(request())

    assert caught.value.kind == "api_error"


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
        (api_error(503, "service_unavailable"), "connection", 0.0),
        (api_error(500, "api_error"), "connection", 0.0),
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
