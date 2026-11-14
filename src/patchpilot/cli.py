"""Command-line entry point for PatchPilot AI."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import SecretStr, ValidationError

from patchpilot import __version__
from patchpilot.agent.baseline import RepairLimits, RunLimits, RunRecord
from patchpilot.agent.checkpoint import CheckpointError, load_checkpoint
from patchpilot.agent.prompts import BugDescription
from patchpilot.agent.repair import Interrupted
from patchpilot.agent.trace import (
    TraceError,
    recorded_mode,
    recorded_outcome,
    replay_model,
    secrets_from_environment,
    text_digest,
)
from patchpilot.config import Settings
from patchpilot.eval.benchmark import run_benchmark
from patchpilot.eval.harness import (
    Mode,
    TaskRun,
    evaluate,
    resume_task,
    run_task,
    summary_table,
)
from patchpilot.intake.allowlist import AllowlistError, RepositoryNotApprovedError, load_allowlist
from patchpilot.intake.github import GitHubClient, GitHubError
from patchpilot.intake.pipeline import run_intake
from patchpilot.intake.urls import IssueUrlError
from patchpilot.llm import gemini_probe
from patchpilot.llm.base import ModelClient, ModelError
from patchpilot.llm.budget import (
    BudgetExceededError,
    RunBudget,
    SpendLedger,
    prices_for,
    usd,
    worst_case_cost,
)
from patchpilot.llm.gemini_client import connect as gemini_connect
from patchpilot.llm.preflight import (
    PreflightError,
    connect,
    first_request,
    run_gemini_preflight,
    run_preflight,
)
from patchpilot.repo.fetch import FetchError
from patchpilot.sandbox.docker import DockerSandbox
from patchpilot.submit.approval import (
    CONFIRM_CHARS,
    ApprovalError,
    check_approval,
    load_approval,
    save_approval,
)
from patchpilot.submit.approval import approve as make_approval
from patchpilot.submit.proposal import ProposalError, build_proposal, load_proposal, save_proposal
from patchpilot.submit.submission import (
    GitHubWriter,
    SubmissionError,
    load_submission,
    submit_proposal,
)
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
    settings = _settings()
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
_LIVE_HELP = (
    "Make real model API calls, paid unless the provider's free tier applies. "
    "Needs credentials and the owner's authorization."
)
_INTERRUPT_HELP = (
    "For exercising resume: stop a repair run after this many checkpoints, as a "
    "process failure would."
)
_MODE_HELP = (
    "baseline: one proposal, no feedback. repair: up to PATCHPILOT_MAX_ATTEMPTS "
    "proposals, each after feedback from the agent's own checks."
)


def _refuse(message: str) -> typer.Exit:
    typer.echo(f"refused: {message}", err=True)
    return typer.Exit(code=2)


def _settings() -> Settings:
    """Read the settings, refusing invalid ones in one line instead of a traceback."""
    try:
        return Settings()
    except ValidationError as error:
        first = error.errors()[0]
        name = "PATCHPILOT_" + "_".join(str(part) for part in first["loc"]).upper()
        # The message, not the rejected value: a setting can hold a secret.
        raise _refuse(f"{name}: {first['msg']}") from None


def _require_free_tier(settings: Settings) -> None:
    if not settings.gemini_free_tier_confirmed:
        # Whether a key is billed is a property of its project that code cannot see.
        raise _refuse(
            "a live Gemini run needs the owner's confirmation that the key's project "
            "has no billing account: check it, then set "
            "PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED=1"
        )


def _live_model(settings: Settings) -> ModelClient:
    """Build the client of the configured provider. There is no fallback to another one."""
    if settings.provider == "gemini":
        _require_free_tier(settings)
        from patchpilot.llm.gemini_client import GeminiModel

        try:
            return GeminiModel()
        except ModelError as error:
            raise _refuse(str(error)) from None
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
    mode: Annotated[Mode, typer.Option(help=_MODE_HELP)] = "baseline",
    interrupt_after: Annotated[int | None, typer.Option(min=1, help=_INTERRUPT_HELP)] = None,
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Run one mode on one task, then grade it with hidden tests."""
    if live == (replay is not None):
        raise _refuse("choose exactly one of --replay <trace> (free) or --live (paid)")
    settings = _settings()
    try:
        loaded = load_task(tasks_root / task)
        if replay is not None:
            # A trace is replayed in the mode it was recorded in.
            mode = "repair" if recorded_mode(replay) == "repair" else "baseline"
        model = _live_model(settings) if live else replay_model(replay)  # type: ignore[arg-type]
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
            limits=RunLimits.for_provider(settings.provider, settings.selected_model),
            mode=mode,
            repair=RepairLimits(max_attempts=settings.max_attempts),
            interrupt_after=interrupt_after,
        )
    except Interrupted as stop:
        raise _interrupted(stop, directory / f"{task}-1", replay) from None
    except (TraceError, CheckpointError) as error:
        typer.echo(f"failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    record = result.record
    if replay is not None:
        expected = recorded_outcome(replay)
        if expected is not None and expected != (record.status.value, text_digest(record.diff)):
            typer.echo("failed: the replay does not match the outcome the trace recorded", err=True)
            raise typer.Exit(code=1)
    _show(result, directory / record.run_id)


def _show(result: TaskRun, directory: Path) -> None:
    record = result.record
    graded = ("resolved" if result.resolved else "not resolved") if result.graded else "not graded"
    typer.echo(f"status: {record.status.value}")
    typer.echo(f"detail: {record.detail}")
    typer.echo(f"hidden tests: {graded}")
    typer.echo(f"cost: ${usd(record.cost_micro_usd):.4f}")
    typer.echo(f"report: {directory / 'report.md'}")


def _interrupted(stop: Interrupted, directory: Path, replay: Path | None) -> typer.Exit:
    typer.echo(f"interrupted: {stop}", err=True)
    source = f" --replay {replay}" if replay is not None else " --replay <trace>"
    typer.echo(f"resume with: patchpilot resume {directory}{source}", err=True)
    return typer.Exit(code=3)


@app.command()
def resume(
    run_dir: Annotated[Path, typer.Argument(help="Directory of an interrupted repair run.")],
    replay: Annotated[
        Path, typer.Option(help="Trace that supplies the model's answers. Makes no model call.")
    ],
    interrupt_after: Annotated[int | None, typer.Option(min=1, help=_INTERRUPT_HELP)] = None,
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Continue an interrupted repair run from its checkpoint, then grade it.

    Offline only: the model's remaining answers come from a trace. The run
    keeps its identity, its limits and everything it has already used.
    """
    settings = _settings()
    directory, root = run_dir.resolve(), settings.runs_dir.resolve()
    if directory == root or not directory.is_relative_to(root):
        raise _refuse(f"a run is resumed only from inside the runs folder {root}")
    try:
        checkpoint = load_checkpoint(directory)
        loaded = load_task(tasks_root / checkpoint.task_id)
        # The answers the run already received are not asked for again.
        model = replay_model(replay, skip=checkpoint.state.generation_attempts)
    except (CheckpointError, TaskError, TraceError) as error:
        raise _refuse(str(error)) from None
    try:
        result = resume_task(
            loaded,
            directory,
            model=model,
            sandbox=_ready_sandbox(),
            ledger=SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd),
            interrupt_after=interrupt_after,
        )
    except Interrupted as stop:
        raise _interrupted(stop, directory, replay) from None
    except (CheckpointError, TraceError, BudgetExceededError) as error:
        typer.echo(f"failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"resumed from checkpoint {checkpoint.sequence} of run {checkpoint.run_id}")
    _show(result, directory)


@app.command(name="eval")
def evaluate_tasks(
    live: Annotated[bool, typer.Option("--live", help=_LIVE_HELP)] = False,
    runs: Annotated[int, typer.Option(min=1, max=10, help="Runs per task.")] = 3,
    mode: Annotated[Mode, typer.Option(help=_MODE_HELP)] = "baseline",
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Run the baseline over every task and report counts. Paid: needs --live."""
    if not live:
        raise _refuse("an evaluation makes paid model calls; pass --live once it is authorized")
    settings = _settings()
    try:
        tasks = discover_tasks(tasks_root)
    except TaskError as error:
        raise _refuse(str(error)) from None
    directory = _output_dir(settings)
    model = _live_model(settings)
    summary = evaluate(
        tasks,
        runs_per_task=runs,
        model_factory=lambda loaded, index: model,
        sandbox=_ready_sandbox(),
        ledger=SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd),
        run_cap_usd=settings.run_cap_usd,
        output_dir=directory,
        limits=RunLimits.for_provider(settings.provider, settings.selected_model),
        mode=mode,
        repair=RepairLimits(max_attempts=settings.max_attempts),
    )
    table = summary_table(summary)
    (directory / "summary.md").write_text(table)
    typer.echo(table)


@app.command()
def preflight(
    task: Annotated[str, typer.Option(help="ID of an evaluation task under the tasks folder.")],
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Check the model and count the first request's tokens. Free: nothing is generated.

    Contacts the provider with the configured credential, using only the
    model-metadata and token-counting endpoints.
    """
    settings = _settings()
    try:
        loaded = load_task(tasks_root / task)
    except TaskError as error:
        raise _refuse(str(error)) from None
    view = loaded.task.agent_view()
    request = first_request(
        BugDescription.from_issue(view.issue),
        source_roots=view.environment.source_roots,
        commands=view.commands,
        limits=RunLimits.for_provider(settings.provider, settings.selected_model),
    )
    ledger = SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd)
    gemini = settings.provider == "gemini"
    try:
        check = run_gemini_preflight if gemini else run_preflight
        report = check(
            gemini_connect() if gemini else connect(),
            request,
            task_id=view.task_id,
            run_cap_usd=settings.run_cap_usd,
            total_cap_usd=settings.max_cost_usd,
            ledger_committed_micro_usd=ledger.committed(),
        )
    except PreflightError as error:
        typer.echo(f"preflight failed ({error.kind}): {error}", err=True)
        raise typer.Exit(code=1) from None
    except ModelError as error:
        # Raised while building the client, before any request is sent.
        typer.echo(f"preflight failed ({error.kind}): {error}", err=True)
        raise typer.Exit(code=1) from None
    except BudgetExceededError as error:
        # Raised while reading the ledger, before any request is sent.
        typer.echo(f"preflight failed (ledger_unreadable): {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"task: {report.task_id}")
    typer.echo(f"provider: {report.provider}")
    typer.echo(f"model: {report.model} ({report.model_display_name or 'no display name'})")
    typer.echo(f"effort: {report.effort}, adaptive thinking")
    typer.echo(f"prompt hash: {report.prompt_sha256}")
    typer.echo(f"input tokens counted by the provider: {report.input_tokens_counted}")
    typer.echo(
        f"estimated cost of that input alone: ${usd(report.estimated_input_cost_micro_usd):.4f}"
    )
    typer.echo(
        f"reservation for the first request: ${usd(report.reservation_micro_usd):.4f} "
        f"({report.input_tokens_reserved} padded input tokens at the dearest input rate, "
        f"plus the full {report.max_output_tokens}-token output limit)"
    )
    typer.echo(
        f"run cap: ${usd(report.run_cap_micro_usd):.2f}, "
        f"fits: {'yes' if report.fits_run_cap else 'NO'}"
    )
    typer.echo(
        f"total cap: ${usd(report.total_cap_micro_usd):.2f}, already committed: "
        f"${usd(report.ledger_committed_micro_usd):.4f}, "
        f"fits: {'yes' if report.fits_total_cap else 'NO'}"
    )
    if gemini:
        confirmed = "yes" if settings.gemini_free_tier_confirmed else "NO"
        typer.echo(
            "the token count is an estimate: the request was flattened to text for the "
            "counting endpoint, so tools, schema and thinking settings were not validated"
        )
        typer.echo(
            "free tier: the amounts above are list-price exposure; nothing is charged "
            "unless the key's project has billing enabled"
        )
        typer.echo(f"free tier confirmed by the owner: {confirmed}")
    typer.echo("no generation request was made; this does not guarantee a paid request succeeds")
    if not (report.fits_run_cap and report.fits_total_cap):
        raise typer.Exit(code=1)


@app.command()
def probe() -> None:
    """Send one minimal Gemini request, to see whether generation is available at all.

    A real generation request: it needs the owner's authorization, uses one
    of the day's requests and is entered in the spend ledger. It is sent
    once, with no retry, no tools and no answer schema.
    """
    settings = _settings()
    if settings.provider != "gemini":
        raise _refuse("the probe is for Gemini only: set PATCHPILOT_PROVIDER=gemini")
    _require_free_tier(settings)
    model = settings.selected_model
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = f"probe-{stamp}"
    reserved = worst_case_cost(
        gemini_probe.INPUT_TOKENS_RESERVED, gemini_probe.MAX_OUTPUT_TOKENS, prices_for(model)
    )
    try:
        client = gemini_connect(gemini_probe.TIMEOUT_SECONDS)
        ledger = SpendLedger(settings.runs_dir / "ledger.jsonl", settings.max_cost_usd)
        budget = RunBudget(ledger, run_id, settings.run_cap_usd)
        # Reserved before anything is sent, like every other model request.
        reservation = budget.reserve(
            model, gemini_probe.INPUT_TOKENS_RESERVED, gemini_probe.MAX_OUTPUT_TOKENS
        )
    except (ModelError, BudgetExceededError) as error:
        typer.echo(f"probe not sent: {error}", err=True)
        raise typer.Exit(code=1) from None
    result = gemini_probe.run_probe(client, model)
    if result.response_received:
        committed = budget.settle(reservation, model, result.usage)
    else:
        # A failed request is charged in full: it may have been billed.
        committed = budget.settle_failed(reservation, result.provider_code or "no answer")

    directory = settings.runs_dir / "probes" / stamp
    directory.mkdir(parents=True)
    record = directory / "probe.json"
    record.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "provider": "gemini",
                "result": result.model_dump(mode="json"),
                "ledger": {"reserved_micro_usd": reserved, "committed_micro_usd": committed},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    record.chmod(0o600)

    typer.echo(f"model requested: {result.model}")
    typer.echo(f"response received: {'yes' if result.response_received else 'no'}")
    typer.echo(f"http status: {result.http_status if result.http_status else 'none'}")
    if result.response_received:
        typer.echo(f"interaction status: {result.interaction_status}")
        typer.echo(f"model reported: {result.model_reported or 'none'}")
        typer.echo(f"answer was exactly OK: {'yes' if result.answer_is_ok else 'no'}")
        typer.echo(
            f"tokens reported by the provider: {result.usage.input_tokens} input, "
            f"{result.usage.output_tokens} output"
        )
    else:
        typer.echo(f"provider code: {result.provider_code or 'none'}")
        typer.echo(f"reason: {result.reason}")
    typer.echo(f"duration: {result.duration_seconds:.1f} s")
    typer.echo(
        f"ledger: reserved ${usd(reserved):.4f}, committed ${usd(committed):.4f} "
        "(internal accounting, not a bill)"
    )
    typer.echo(f"record: {record}")
    typer.echo("one generation request was sent; nothing was retried")
    if not result.response_received:
        raise typer.Exit(code=1)


# -- pull request proposal, approval and submission -----------------------------------------


def _run_directory(settings: Settings, run_dir: Path) -> Path:
    directory, root = run_dir.resolve(), settings.runs_dir.resolve()
    if directory == root or not directory.is_relative_to(root) or not directory.is_dir():
        raise _refuse(f"use a run directory inside the runs folder {root}")
    return directory


@app.command(name="propose-pr")
def propose_pr(
    run_dir: Annotated[Path, typer.Argument(help="Directory of a finished, verified run.")],
    repository: Annotated[str, typer.Option(help="Approved repository, owner/name.")],
    issue: Annotated[int, typer.Option(min=1, help="Issue number in that repository.")],
    base_branch: Annotated[str, typer.Option(help="Branch the pull request targets.")] = "main",
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Write a reviewable pull request proposal for a verified run. Sends nothing."""
    settings = _settings()
    directory = _run_directory(settings, run_dir)
    try:
        record_bytes = (directory / "record.json").read_bytes()
        record = RunRecord.model_validate(json.loads(record_bytes)["record"])
        owner, _, name = repository.lower().partition("/")
        approved = load_allowlist(settings.allowlist_path).find(owner, name)
        if approved is None:
            raise ProposalError(f"{repository} is not in the allowlist")
        loaded = load_task(tasks_root / record.task_id)
        proposal = build_proposal(
            record,
            record_bytes,
            loaded.agent_files(),
            approved,
            issue_number=issue,
            base_branch=base_branch,
            secrets=secrets_from_environment(),
        )
    except (OSError, ValueError, KeyError, ProposalError, TaskError, AllowlistError) as error:
        raise _refuse(f"no proposal: {error}") from None
    save_proposal(directory, proposal)
    typer.echo(f"proposal: {directory / 'pr-proposal.md'}")
    typer.echo(f"patch digest: {proposal.patch_sha256}")
    typer.echo("nothing was sent; review the proposal, then run patchpilot approve")


@app.command()
def approve(
    run_dir: Annotated[Path, typer.Argument(help="Directory holding a pull request proposal.")],
) -> None:
    """Show a proposal and record a person's approval of exactly that proposal."""
    settings = _settings()
    directory = _run_directory(settings, run_dir)
    try:
        proposal = load_proposal(directory)
    except ProposalError as error:
        raise _refuse(str(error)) from None
    typer.echo(f"Repository: {proposal.repository} (id {proposal.repository_id})")
    typer.echo(f"Issue: #{proposal.issue_number}")
    typer.echo(f"Base: {proposal.base_branch} at {proposal.base_commit}")
    typer.echo(f"Files: {', '.join(change.path for change in proposal.changes)}")
    typer.echo("Intended GitHub operations:")
    for index, item in enumerate(proposal.operations, start=1):
        typer.echo(f"  {index}. {item}")
    typer.echo(f"Patch digest: {proposal.patch_sha256}")
    typer.echo(f"Review the full proposal in {directory / 'pr-proposal.md'} first.")
    answer = typer.prompt(
        f"To approve, type the first {CONFIRM_CHARS} characters of the patch digest"
    )
    try:
        approval = make_approval(proposal, answer)
    except ApprovalError as error:
        typer.echo(f"not approved: {error}", err=True)
        raise typer.Exit(code=1) from None
    save_approval(directory, approval)
    typer.echo(f"approved until {approval.expires_at.isoformat()}; run patchpilot submit")


def _github_writer(repository: str, token: SecretStr) -> GitHubWriter:
    return GitHubWriter(repository, token)


@app.command()
def submit(
    run_dir: Annotated[Path, typer.Argument(help="Directory holding an approved proposal.")],
) -> None:
    """Open the approved proposal as a draft pull request. A real GitHub write.

    Needs PATCHPILOT_GITHUB_WRITE_TOKEN and a valid approval of this exact
    proposal. Checks GitHub with read-only requests before every write, never
    repeats a write whose outcome is unknown, and never merges.
    """
    settings = _settings()
    directory = _run_directory(settings, run_dir)
    try:
        proposal = load_proposal(directory)
        approval = load_approval(directory)
        check_approval(approval, proposal)
    except (ProposalError, ApprovalError) as error:
        raise _refuse(str(error)) from None
    if settings.github_write_token is None:
        raise _refuse(
            "set PATCHPILOT_GITHUB_WRITE_TOKEN to a fine-grained token for one repository"
        )
    writer = _github_writer(proposal.repository, settings.github_write_token)
    try:
        result = submit_proposal(proposal, approval, writer, directory)
    except (SubmissionError, ApprovalError) as error:
        state = "uncertain" if getattr(error, "uncertain", False) else "failed"
        typer.echo(f"{state}: {error}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        writer.close()
    typer.echo(f"draft pull request #{result.pull_request_number}: {result.pull_request_url}")
    typer.echo(f"branch {result.branch} at {result.commit_sha}; nothing was merged")


@app.command()
def benchmark(
    scripted: Annotated[
        bool, typer.Option("--scripted", help="Use the hand-written scripted answers. Required.")
    ] = False,
    scenario: Annotated[
        list[str] | None, typer.Option(help="Run only these scenarios. Repeatable.")
    ] = None,
    output: Annotated[Path | None, typer.Option(help="Folder for the results.")] = None,
    tasks_root: Annotated[Path, typer.Option(help="Folder holding the tasks.")] = TASKS_ROOT,
) -> None:
    """Compare baseline and repair on every task with scripted answers. Offline, free.

    Results are labelled scripted: they exercise the pipeline and are not a
    model's success rate. A live benchmark needs the owner's authorization.
    """
    if not scripted:
        raise _refuse("only the scripted benchmark exists; pass --scripted")
    settings = _settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = output or settings.runs_dir / f"benchmark-{stamp}"
    try:
        tasks = discover_tasks(tasks_root)
    except TaskError as error:
        raise _refuse(str(error)) from None
    result = run_benchmark(
        tasks,
        _ready_sandbox(),
        directory,
        scripted_root=tasks_root.parent / "scripted",
        only=tuple(scenario or ()),
    )
    typer.echo(result["label"])
    for mode, summary in result["summary"].items():
        typer.echo(
            f"{mode}: {summary['resolved_by_hidden_tests']} of {summary['runs']} resolved, "
            f"{summary['verified_by_agent']} verified by the agent"
        )
    typer.echo(f"results: {directory / 'benchmark.json'} and {directory / 'benchmark.md'}")


def _grading(resolved: object) -> str:
    if resolved is None:
        return "not graded"
    return "resolved" if resolved else "not resolved"


@app.command()
def show(run_dir: Annotated[Path, typer.Argument(help="Directory of a run.")]) -> None:
    """Explain what happened in a run: mode, proposals, budgets, grading, submission."""
    settings = _settings()
    directory = _run_directory(settings, run_dir)
    lines: list[str] = []
    record_path = directory / "record.json"
    if record_path.is_file():
        payload = json.loads(record_path.read_text())
        record = RunRecord.model_validate(payload["record"])
        graded = payload.get("resolved")
        lines += [
            f"run {record.run_id}, task {record.task_id}, mode {record.mode}",
            f"status: {record.status.value}: {record.detail[:300]}",
            f"proposals: {len(record.attempts) or (1 if record.proposal else 0)}",
            *[
                f"  {number}. {attempt.status.value}: {attempt.detail[:120]}"
                for number, attempt in enumerate(record.attempts, start=1)
            ],
            f"model turns {record.model_turns}, generation attempts {record.generation_attempts}, "
            f"tool calls {record.tool_calls}, ledger ${usd(record.cost_micro_usd):.4f}",
            f"hidden tests: {_grading(graded)}",
        ]
    else:
        lines.append("no record: the run did not finish")
    try:
        checkpoint = load_checkpoint(directory)
        state = checkpoint.state
        lines.append(
            f"checkpoint {checkpoint.sequence}: phase {state.phase}, proposal {state.attempt}"
        )
    except CheckpointError:
        lines.append("checkpoint: none")
    for name, label in (
        ("pr-proposal.json", "pull request proposal"),
        ("approval.json", "approval"),
    ):
        lines.append(f"{label}: {'present' if (directory / name).is_file() else 'none'}")
    try:
        submitted = load_submission(directory)
    except SubmissionError as error:
        lines.append(f"submission: unreadable ({error})")
    else:
        if submitted is None:
            lines.append("submission: none")
        else:
            pull = submitted.pull_request_url or "no pull request"
            lines.append(f"submission: {submitted.state}, {pull}")
    for line in lines:
        typer.echo(line)
