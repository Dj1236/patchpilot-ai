"""Preflight against a stand-in SDK client. No request leaves the machine."""

from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.agent.baseline import RunLimits, run_baseline
from patchpilot.agent.prompts import BugDescription
from patchpilot.agent.trace import TraceWriter, request_digest
from patchpilot.llm.budget import RunBudget, SpendLedger, to_micro
from patchpilot.llm.fake import ScriptedModel, final_turn
from patchpilot.llm.preflight import PreflightError, connect, first_request, run_preflight
from patchpilot.sandbox.models import SandboxLimits
from patchpilot.tasks import load_task

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"
TASK = load_task(TASKS_ROOT / "inj-001-cachekit")
VIEW = TASK.task.agent_view()
BUG = BugDescription.from_issue(VIEW.issue)
REQUEST = first_request(
    BUG, source_roots=VIEW.environment.source_roots, commands=VIEW.commands, limits=RunLimits()
)
SECRET = "patchpilot-preflight-canary-not-a-real-key"


def _error(cls: type[anthropic.APIStatusError], status: int, message: str = "") -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/count_tokens")
    body = {"type": "error", "error": {"type": "x", "message": message}} if message else None
    return cls(message or "error", response=httpx2.Response(status, request=request), body=body)


class FakeClient:
    """Stands in for the SDK. Generating anything is an immediate test failure."""

    def __init__(self, *, model: Any = None, count: Any = 1800) -> None:
        self._model, self._count = model, count
        self.calls: list[str] = []
        self.counted: list[dict[str, Any]] = []
        self.models = self
        self.messages = self

    def retrieve(self, model_id: str) -> Any:
        self.calls.append(f"models.retrieve:{model_id}")
        if isinstance(self._model, Exception):
            raise self._model
        return type("Model", (), {"id": model_id, "display_name": "Claude Sonnet 5.5"})()

    def count_tokens(self, **kwargs: Any) -> Any:
        self.calls.append("messages.count_tokens")
        self.counted.append(kwargs)
        if isinstance(self._count, Exception):
            raise self._count
        return type("Count", (), {"input_tokens": self._count})()

    def create(self, **kwargs: Any) -> Any:
        raise AssertionError("preflight must never make a generation request")

    stream = create


def check(client: FakeClient, committed: int = 0) -> Any:
    return run_preflight(
        client,
        REQUEST,
        task_id="inj-001-cachekit",
        run_cap_usd=0.75,
        total_cap_usd=5.0,
        ledger_committed_micro_usd=committed,
    )


def test_a_clean_preflight_reports_tokens_and_a_conservative_reservation() -> None:
    client = FakeClient()

    report = check(client)

    assert client.calls == ["models.retrieve:claude-sonnet-5-5", "messages.count_tokens"]
    assert report.model == "claude-sonnet-5-5" and report.effort == "medium"
    assert report.input_tokens_counted == 1800
    assert report.input_tokens_reserved == int(1800 * 1.1) + 256
    assert report.estimated_input_cost_micro_usd == 1800 * 2
    assert report.reservation_micro_usd == int(report.input_tokens_reserved * 2.5) + 16_000 * 10
    assert report.reservation_micro_usd > report.estimated_input_cost_micro_usd
    assert report.fits_run_cap and report.fits_total_cap


def test_the_counted_request_has_the_real_tools_schema_and_thinking_settings() -> None:
    client = FakeClient()

    check(client)

    (sent,) = client.counted
    assert sent["model"] == "claude-sonnet-5-5"
    assert [tool["name"] for tool in sent["tools"]] == [
        "list_files",
        "read_file",
        "search_code",
        "outline",
    ]
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "medium"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "max_tokens" not in sent and "fallbacks" not in sent


def test_preflight_counts_exactly_the_request_the_baseline_sends_first(tmp_path: Path) -> None:
    class NoSandbox:
        def run(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the sandbox is not needed for this check")

    model = ScriptedModel([final_turn("not a proposal")])
    run_baseline(
        task_id=VIEW.task_id,
        bug=BUG,
        files=TASK.agent_files(),
        source_roots=VIEW.environment.source_roots,
        commands=VIEW.commands,
        sandbox_limits=SandboxLimits(),
        model=model,
        sandbox=NoSandbox(),
        budget=RunBudget(SpendLedger(tmp_path / "ledger.jsonl", 5.0), "run", 0.75),
        trace=TraceWriter(tmp_path / "trace"),
    )

    assert request_digest(model.requests[0]) == request_digest(REQUEST)


def test_budget_fit_reflects_what_the_ledger_already_holds() -> None:
    report = check(FakeClient(), committed=to_micro(4.95))

    assert report.fits_run_cap and not report.fits_total_cap


@pytest.mark.parametrize(
    "client,kind,during",
    [
        (FakeClient(model=_error(anthropic.AuthenticationError, 401)), "authentication", "model"),
        (FakeClient(model=_error(anthropic.PermissionDeniedError, 403)), "permission", "model"),
        (FakeClient(model=_error(anthropic.NotFoundError, 404)), "model_unavailable", "model"),
        (FakeClient(model=_error(anthropic.RateLimitError, 429)), "rate_limited", "model"),
        (FakeClient(count=_error(anthropic.BadRequestError, 400)), "malformed_request", "counting"),
        (
            FakeClient(count=_error(anthropic.InternalServerError, 500)),
            "provider_error",
            "counting",
        ),
        (
            FakeClient(
                count=anthropic.APIConnectionError(
                    request=httpx2.Request("POST", "https://api.anthropic.com")
                )
            ),
            "connection",
            "counting",
        ),
        (FakeClient(count="not a number"), "provider_error", "counting"),
    ],
)
def test_each_failure_has_a_clear_kind(client: FakeClient, kind: str, during: str) -> None:
    with pytest.raises(PreflightError) as caught:
        check(client)

    assert caught.value.kind == kind and during in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_failed_model_check_stops_before_anything_else_is_requested() -> None:
    client = FakeClient(model=_error(anthropic.AuthenticationError, 401))

    with pytest.raises(PreflightError):
        check(client)

    assert client.calls == ["models.retrieve:claude-sonnet-5-5"]


def test_provider_explanations_are_shown_bounded_and_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    explanation = f"output_config.format.schema: unsupported keyword (key {SECRET}) " + "x" * 2000
    client = FakeClient(count=_error(anthropic.BadRequestError, 400, explanation))

    with pytest.raises(PreflightError) as caught:
        check(client)

    message = str(caught.value)
    assert "output_config.format.schema" in message
    assert SECRET not in message and len(message) < 500


def test_errors_and_reports_never_contain_prompt_contents() -> None:
    issue_text = VIEW.issue.body[:40]
    client = FakeClient(count=_error(anthropic.BadRequestError, 400))

    with pytest.raises(PreflightError) as caught:
        check(client)
    report = check(FakeClient()).model_dump_json()

    for text in (str(caught.value), report):
        assert issue_text not in text and "You are the patch author" not in text


def test_without_a_credential_nothing_is_sent_and_the_failure_is_clear() -> None:
    # The suite-wide fixture has removed every model credential, so this uses
    # the real SDK client and still cannot reach the provider.
    with pytest.raises(PreflightError) as caught:
        run_preflight(
            connect(),
            REQUEST,
            task_id="inj-001-cachekit",
            run_cap_usd=0.75,
            total_cap_usd=5.0,
            ledger_committed_micro_usd=0,
        )

    assert caught.value.kind == "no_credentials"
    assert "ANTHROPIC_API_KEY" in str(caught.value)


def test_the_paid_path_also_reports_a_missing_credential_instead_of_crashing() -> None:
    from patchpilot.llm.anthropic_client import AnthropicModel
    from patchpilot.llm.base import ModelError

    with pytest.raises(ModelError, match="no model credential"):
        AnthropicModel().count_input_tokens(REQUEST)


# -- command line ---------------------------------------------------------------------

runner = CliRunner()


def test_the_command_prints_the_report_and_labels_estimates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    monkeypatch.setattr(cli, "connect", lambda: client)
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path))

    result = runner.invoke(cli.app, ["preflight", "--task", "inj-001-cachekit"])

    assert result.exit_code == 0, result.output
    assert "input tokens counted by the provider: 1800" in result.output
    assert "reservation for the first request: $0.1656" in result.output
    assert "no generation request was made" in result.output
    assert client.calls[-1] == "messages.count_tokens"
    assert not (tmp_path / "ledger.jsonl").exists(), "preflight reserves nothing"


def test_the_command_fails_cleanly_without_credentials_or_with_a_bad_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path))
    missing = runner.invoke(cli.app, ["preflight", "--task", "inj-001-cachekit"])

    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    rejected_client = FakeClient(model=_error(anthropic.AuthenticationError, 401))
    monkeypatch.setattr(cli, "connect", lambda: rejected_client)
    rejected = runner.invoke(cli.app, ["preflight", "--task", "inj-001-cachekit"])

    assert missing.exit_code == 1 and "preflight failed (no_credentials)" in missing.output
    assert rejected.exit_code == 1 and "preflight failed (authentication)" in rejected.output
    for result in (missing, rejected):
        assert SECRET not in result.output and "Traceback" not in result.output


def test_the_command_fails_cleanly_when_the_ledger_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    monkeypatch.setattr(cli, "connect", lambda: client)
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path))
    (tmp_path / "ledger.jsonl").write_text("not json\n")

    result = runner.invoke(cli.app, ["preflight", "--task", "inj-001-cachekit"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), "the command crashed instead of exiting"
    assert "preflight failed (ledger_unreadable)" in result.output
    assert "refusing to spend" in result.output and "Traceback" not in result.output
    assert client.calls == [], "the provider was contacted"


def test_the_command_refuses_an_unknown_task_before_contacting_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "connect", lambda: pytest.fail("the provider was contacted"))

    result = runner.invoke(cli.app, ["preflight", "--task", "no-such-task"])

    assert result.exit_code == 2 and "refused" in result.output
