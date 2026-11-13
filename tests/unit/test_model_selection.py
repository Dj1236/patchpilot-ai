"""Choosing one approved Gemini model. No request leaves the machine."""

from pathlib import Path
from typing import Any

import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.agent.baseline import RunLimits, RunStatus, run_baseline
from patchpilot.agent.trace import TraceWriter
from patchpilot.config import Settings
from patchpilot.llm.base import APPROVED_MODELS, PINNED_MODELS
from patchpilot.llm.budget import RunBudget, SpendLedger, prices_for
from patchpilot.sandbox.models import SandboxLimits
from tests.gemini_wire import FakeClock, Reply, Wire, answer, api_error, call, interaction, thought
from tests.unit.test_baseline import BUG, COMMANDS, FILES, GOOD_SANDBOX, ScriptedSandbox, proposal

OLDER, NEWER = "gemini-3.6-flash", "gemini-3.8-flash"
SECRET = "patchpilot-selection-canary-not-a-real-key"
runner = CliRunner()


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATCHPILOT_PROVIDER", "gemini")
    monkeypatch.setenv("PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED", "1")
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)


def _wire(monkeypatch: pytest.MonkeyPatch, replies: list[Reply], model: str) -> Wire:
    wire = Wire(replies, model_info=(200, {"name": f"models/{model}", "displayName": model}))
    monkeypatch.setattr(cli, "gemini_connect", lambda *args, **kwargs: wire.client())
    return wire


# -- the setting --------------------------------------------------------------------------


def test_the_default_model_is_unchanged_and_the_setting_selects_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = Settings()
    monkeypatch.setenv("PATCHPILOT_GEMINI_MODEL", OLDER)
    selected = Settings()
    monkeypatch.setenv("PATCHPILOT_PROVIDER", "anthropic")
    other_provider = Settings()

    assert default.gemini_model == default.selected_model == NEWER == PINNED_MODELS["gemini"]
    assert selected.selected_model == OLDER
    assert other_provider.selected_model == "claude-sonnet-5-5", "the setting is Gemini's only"
    assert APPROVED_MODELS["gemini"] == (NEWER, OLDER)


@pytest.mark.parametrize(
    "name",
    ["gemini-2.5-flash", "gemini-4-carbon", "gemini-3.6-flash-lite", "claude-sonnet-5-5", "x"],
)
def test_a_model_that_is_not_approved_stops_every_command_before_any_request(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("PATCHPILOT_GEMINI_MODEL", name)
    wire = _wire(monkeypatch, [interaction(answer("OK"))], name)

    with pytest.raises(ValidationError):
        Settings()
    for command in (
        ["probe"],
        ["preflight", "--task", "inj-001-cachekit"],
        ["run", "--task", "inj-001-cachekit", "--live"],
    ):
        result = runner.invoke(cli.app, command)
        assert result.exit_code == 2, result.output
        assert "refused: PATCHPILOT_GEMINI_MODEL" in result.output
        assert "not an approved Gemini model" in result.output
        assert "Traceback" not in result.output
    assert wire.requests == []


def test_run_limits_accept_only_approved_models_of_their_own_provider() -> None:
    assert RunLimits.for_provider("gemini", OLDER).model == OLDER
    assert RunLimits.for_provider("gemini").model == NEWER
    assert (
        RunLimits.for_provider("gemini", OLDER).digest()
        != RunLimits.for_provider("gemini").digest()
    )
    for provider, model in (("gemini", "gemini-4-hydrogen"), ("anthropic", OLDER)):
        with pytest.raises(ValueError, match="runs only"):
            RunLimits.for_provider(provider, model)  # type: ignore[arg-type]


def test_every_approved_model_has_a_pinned_price() -> None:
    for models in APPROVED_MODELS.values():
        for model in models:
            assert prices_for(model).output > 0
    assert prices_for(OLDER) == prices_for(NEWER)


# -- probe, preflight and run use the selected model ------------------------------------------


def test_the_probe_uses_the_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_GEMINI_MODEL", OLDER)
    wire = _wire(monkeypatch, [interaction(answer("OK"), model=OLDER)], OLDER)

    result = runner.invoke(cli.app, ["probe"])

    assert result.exit_code == 0, result.output
    assert [body["model"] for body in wire.sent] == [OLDER]
    assert f"model requested: {OLDER}" in result.output
    assert f"model reported: {OLDER}" in result.output


def test_the_preflight_uses_the_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_GEMINI_MODEL", OLDER)
    wire = _wire(monkeypatch, [], OLDER)

    result = runner.invoke(cli.app, ["preflight", "--task", "inj-001-cachekit"])

    assert result.exit_code == 0, result.output
    assert [line.split("/v1/")[1] for line in wire.requests] == [
        f"models/{OLDER}",
        f"models/{OLDER}:countTokens",
    ]
    assert f"model: {OLDER}" in result.output and wire.sent == []


def test_a_live_run_is_started_with_the_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_GEMINI_MODEL", OLDER)
    seen: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise typer.Exit(code=0)

    monkeypatch.setattr(cli, "_live_model", lambda settings: object())
    monkeypatch.setattr(cli, "_ready_sandbox", lambda: object())
    monkeypatch.setattr(cli, "run_task", capture)

    result = runner.invoke(cli.app, ["run", "--task", "inj-001-cachekit", "--live"])

    assert result.exit_code == 0, result.output
    assert (seen["limits"].provider, seen["limits"].model) == ("gemini", OLDER)


# -- the agent run itself --------------------------------------------------------------------


def _run(wire: Wire, tmp_path: Path, model: str) -> Any:
    clock = FakeClock()
    record, _ = run_baseline(
        task_id="demo",
        bug=BUG,
        files=FILES,
        source_roots=("src",),
        commands=COMMANDS,
        sandbox_limits=SandboxLimits(),
        model=wire.model(clock.limiter()),
        sandbox=ScriptedSandbox(GOOD_SANDBOX),
        budget=RunBudget(SpendLedger(tmp_path / "ledger.jsonl", 5.0), "run", 0.75),
        trace=TraceWriter(tmp_path / "run", ()),
        limits=RunLimits.for_provider("gemini", model),
        run_id="run",
    )
    return record


def test_a_run_on_the_selected_model_records_it_and_asks_for_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire = Wire(
        [
            interaction(thought(), call("call-a", "list_files", {}), model=OLDER),
            interaction(answer(proposal()), model=OLDER),
        ]
    )

    record = _run(wire, tmp_path, OLDER)

    assert record.status is RunStatus.CANDIDATE_VERIFIED
    assert record.manifest.model_requested == OLDER
    assert record.manifest.models_reported == (OLDER,)
    assert {body["model"] for body in wire.sent} == {OLDER}
    assert all(f"models/{OLDER}:countTokens" in line for line in wire.requests if "count" in line)
    assert 0 < record.cost_micro_usd < 750_000


@pytest.mark.parametrize(("selected", "answered_by"), [(OLDER, NEWER), (NEWER, OLDER)])
def test_an_answer_from_the_other_approved_model_is_still_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str, answered_by: str
) -> None:
    monkeypatch.setattr("patchpilot.agent.baseline.time.sleep", lambda _: None)
    wire = Wire([interaction(answer(proposal()), model=answered_by)])

    record = _run(wire, tmp_path, selected)

    assert record.status is RunStatus.FAILED_INFRASTRUCTURE
    assert "different model" in record.detail and record.manifest.models_reported == ()


def test_an_unavailable_model_is_never_replaced_by_the_other_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("patchpilot.agent.baseline.time.sleep", waits.append)
    unavailable = api_error(503, "service_unavailable")
    wire = Wire([unavailable, unavailable, interaction(answer(proposal()), model=NEWER)])

    record = _run(wire, tmp_path, OLDER)

    assert record.status is RunStatus.FAILED_INFRASTRUCTURE and waits == [30.0]
    assert [body["model"] for body in wire.sent] == [OLDER, OLDER]
    assert not any(NEWER in line for line in wire.requests), "no fallback to the other model"
