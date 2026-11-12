"""Command-line entry point for PatchPilot AI."""

import json
from pathlib import Path
from typing import Annotated

import typer

from patchpilot import __version__
from patchpilot.config import Settings
from patchpilot.intake.allowlist import AllowlistError, RepositoryNotApprovedError, load_allowlist
from patchpilot.intake.github import GitHubClient, GitHubError
from patchpilot.intake.pipeline import run_intake
from patchpilot.intake.urls import IssueUrlError
from patchpilot.repo.fetch import FetchError

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
