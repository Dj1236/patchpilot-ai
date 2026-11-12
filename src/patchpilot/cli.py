"""Command-line entry point for PatchPilot AI."""

import typer

from patchpilot import __version__

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
