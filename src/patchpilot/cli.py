"""Command-line entry point for PatchPilot AI."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from patchpilot import __version__
from patchpilot.agent.trace import TraceError, replay_model
from patchpilot.config import Settings
from patchpilot.eval.harness import evaluate, run_task, summary_table
from patchpilot.intake.allowlist import AllowlistError, RepositoryNotApprovedError, load_allowlist
from patchpilot.intake.github import GitHubClient, GitHubError
from patchpilot.intake.pipeline import run_intake
from patchpilot.intake.urls import IssueUrlError
from patchpilot.llm.base import ModelClient
from patchpilot.llm.budget import SpendLedger, usd
from patchpilot.repo.fetch import FetchError
from patchpilot.sandbox.docker import DockerSandbox
from patchpilot.tasks.loader import TaskError, discover_tasks, load_task

app = typer.Typer(
    name="patchpilot",
    help="Turn a Python bug report into a tested, reviewable patch.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Turn a Python bug report into a tested, reviewable patch."""
    # A callback keeps `patchpilot <command>` syntax while only one command exists;
    # without it Typer would collapse the single command into the root.


@app.command()
def version() -> None:
    """Print the installed PatchPilot AI version."""
    typer.echo(f"patchpilot-ai {__version__}")


@app.command()
def intake(
    issue_url: Annotated[str, typer.Argument(help="URL of a GitHub issue.")],
    allowlist: Annotated[
        Path | None,
        typer.Option(help="TOML file of approved repositories. Overrides the configured one."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")] = False,
) -> None:
    """Read an issue of an approved repository and snapshot the pinned commit.

    Read-only: nothing is written to GitHub, no code is run and no model is called.
    """
    settings = Settings()
    try:
        approved = load_allowlist(allowlist or settings.allowlist_path)
        client = GitHubClient(token=settings.github_token)
        try:
            result = run_intake(issue_url, approved, client)
        finally:
            client.close()
    except (IssueUrlError, RepositoryNotApprovedError, AllowlistError) as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=2) from None
    except (GitHubError, FetchError) as error:
        typer.echo(f"failed ({error.status.value}): {error}", err=True)
        raise typer.Exit(code=1) from None

    report, snapshot = result.bug_report, result.snapshot
    summary = {
        "issue": report.issue.canonical_url,
        "state": report.state,
        # Shown to the person at the terminal as data. It is untrusted text.
        "title": report.title.presentation.reveal(),
        "body_characters": report.body.complete_length,
        "body_sha256": report.body.complete_sha256,
        "body_captured_completely": report.body.captured_is_complete,
        "repository": snapshot.url,
        "commit": result.commit_sha,
        "pinned_by": result.pinned_by,
        "files": len(snapshot.files),
        "tree_sha256": snapshot.tree_sha256,
        "skipped_entries": snapshot.skipped_total,
    }
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
    else:
        for key, value in summary.items():
            typer.echo(f"{key.replace('_', ' ')}: {value}")


TASKS_ROOT = Path("evals/tasks")
_LIVE_HELP = "Make paid model API calls. Needs credentials and the owner's authorization."


def _refuse(message: str) -> typer.Exit:
    typer.echo(f"refused: {message}", err=True)
    return typer.Exit(code=2)


def _live_model() -> ModelClient:
    from patchpilot.llm.anthropic_client import AnthropicModel

    return AnthropicModel()


def _ready_sandbox() -> DockerSandbox:
    sandbox = DockerSandbox()
    if not sandbox.is_available():
        typer.echo("failed: no Docker engine is reachable for the sandbox", err=True)
        raise typer.Exit(code=1)
    return sandbox


def _output_dir(settings: Settings) -> Path:
    return settings.runs_dir / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


@app.command()
def run(
    task: Annotated[str, typer.Option(help="ID of an evaluation task under the tasks folder.")],
    replay: Annotated[
        Path | None, typer.Option(help="Replay this stored trace. Makes no model call.")
    ] = None,
    live: Annotated[bool, typer.Option("--live", help=_LIVE_HELP)] = False,
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Run the single-attempt baseline on one task, then grade it with hidden tests."""
    if live == (replay is not None):
        raise _refuse("choose exactly one of --replay <trace> (free) or --live (paid)")
    settings = Settings()
    try:
        loaded = load_task(tasks_root / task)
        model = _live_model() if live else replay_model(replay)  # type: ignore[arg-type]
    except (TaskError, TraceError) as error:
        raise _refuse(str(error)) from None
    directory = _output_dir(settings)
    ledger = SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd)
    try:
        result = run_task(
            loaded,
            1,
            model=model,
            sandbox=_ready_sandbox(),
            ledger=ledger,
            run_cap_usd=settings.run_cap_usd,
            output_dir=directory,
        )
    except TraceError as error:
        typer.echo(f"failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    record = result.record
    graded = ("resolved" if result.resolved else "not resolved") if result.graded else "not graded"
    typer.echo(f"status: {record.status.value}")
    typer.echo(f"detail: {record.detail}")
    typer.echo(f"hidden tests: {graded}")
    typer.echo(f"cost: ${usd(record.cost_micro_usd):.4f}")
    typer.echo(f"report: {directory / record.run_id / 'report.md'}")


@app.command(name="eval")
def evaluate_tasks(
    live: Annotated[bool, typer.Option("--live", help=_LIVE_HELP)] = False,
    runs: Annotated[int, typer.Option(min=1, max=10, help="Runs per task.")] = 3,
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Run the baseline over every task and report counts. Paid: needs --live."""
    if not live:
        raise _refuse("an evaluation makes paid model calls; pass --live once it is authorized")
    settings = Settings()
    try:
        tasks = discover_tasks(tasks_root)
    except TaskError as error:
        raise _refuse(str(error)) from None
    directory = _output_dir(settings)
    model = _live_model()
    summary = evaluate(
        tasks,
        runs_per_task=runs,
        model_factory=lambda loaded, index: model,
        sandbox=_ready_sandbox(),
        ledger=SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd),
        run_cap_usd=settings.run_cap_usd,
        output_dir=directory,
    )
    table = summary_table(summary)
    (directory / "summary.md").write_text(table)
    typer.echo(table)
