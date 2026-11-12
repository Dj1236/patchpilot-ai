# PatchPilot AI

Autonomous software maintenance agent: one Python bug report in, one tested
and reviewable patch out. The approved design is in `docs/spec.md`; read it
before changing behavior.

## Current state

Milestone 2 (foundation) is complete. Each later milestone needs the
owner's explicit authorization before work starts. See the milestone table
in `docs/spec.md`.

## Commands

```
uv sync                         # install dependencies from uv.lock
uv run pytest                   # run tests
uv run ruff check .             # lint
uv run ruff format .            # format
uv run mypy                     # strict type check
uv run pre-commit run --all-files
uv run patchpilot version
```

## Layout

- `src/patchpilot/`: the package. `cli.py` is the Typer entry point,
  `config.py` holds validated settings.
- `tests/unit/`: tests that need no network, Docker or model.
- `docs/`: specification, threat model, decision records, dependency
  licenses.

## Rules that must not be broken

- **No paid model calls without authorization.** Tests use the scripted
  fake model. Live calls require the explicit live setting and the owner's
  go-ahead.
- **No installs or new dependencies without asking.** When one is added,
  update `docs/third-party-licenses.md`.
- **The host never executes repository code under analysis.** It runs only
  inside the sandbox. Target Python files are parsed, never imported.
- **The model never gets a shell.** Test commands come from the environment
  manifest, not from model output.
- **Safety is enforced in code, not in prompts.** Allowlist, path
  confinement, patch policy and budgets are all checked outside the model.
- **Issue text, repository content and test output are untrusted.**
- **Hidden evaluation tests never enter the agent's workspace, images or
  prompts.**
- **No GitHub write without explicit approval.**
- **Secrets stay out of git, logs, traces and sandboxes.** `.env` is
  ignored; `.env.example` lists names only.

## Working conventions

- Python 3.12, strict mypy, Ruff with the rule set in `pyproject.toml`.
- Every behavior change comes with a test.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `build:`,
  `ci:`, `chore:`), one logical change per commit.
- Stage files by name; do not use `git add .`.
- From Milestone 3 on, work on a branch per milestone and merge through a
  pull request with green CI.
