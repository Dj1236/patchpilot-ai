from typer.testing import CliRunner

from patchpilot import __version__
from patchpilot.cli import app

runner = CliRunner()


def test_version_command_prints_installed_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"patchpilot-ai {__version__}"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "version" in result.output


def test_run_needs_exactly_one_of_replay_or_live(tmp_path) -> None:  # type: ignore[no-untyped-def]
    neither = runner.invoke(app, ["run", "--task", "inj-001-cachekit"])
    both = runner.invoke(
        app, ["run", "--task", "inj-001-cachekit", "--live", "--replay", str(tmp_path / "t")]
    )

    for result in (neither, both):
        assert result.exit_code == 2
        assert "exactly one of --replay" in result.output


def test_run_refuses_an_unknown_task_and_a_missing_trace(tmp_path) -> None:  # type: ignore[no-untyped-def]
    unknown = runner.invoke(app, ["run", "--task", "no-such-task", "--replay", "x"])
    missing = runner.invoke(
        app, ["run", "--task", "inj-001-cachekit", "--replay", str(tmp_path / "none.jsonl")]
    )

    assert unknown.exit_code == 2 and "refused" in unknown.output
    assert missing.exit_code == 2 and "not a trace file" in missing.output


def test_an_evaluation_cannot_start_without_the_live_flag() -> None:
    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 2
    assert "paid model calls" in result.output


def test_no_command_makes_a_paid_call_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import patchpilot.cli as cli

    def forbidden() -> None:
        raise AssertionError("a live model was created without --live")

    monkeypatch.setattr(cli, "_live_model", forbidden)

    assert runner.invoke(app, ["eval"]).exit_code == 2
    assert runner.invoke(app, ["run", "--task", "inj-001-cachekit"]).exit_code == 2
    assert runner.invoke(app, ["version"]).exit_code == 0
