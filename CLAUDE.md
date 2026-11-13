# PatchPilot AI

Autonomous software maintenance agent: one Python bug report in, one tested
and reviewable patch out. The approved design is in `docs/spec.md`; read it
before changing behavior.

## Current state

Milestones 2 to 4 and the Milestone 5 baseline agent (steps 1 to 11) are
merged and verified locally, and so is the free `patchpilot preflight`
command. No live model call has been made, and the preflight has not been run
with a credential. The next step is the live pilot in `docs/pilot-plan.md`:
it is planned but not authorized, its thresholds are confirmed, and each
live run needs its own go-ahead. PatchPilot does not load `.env`; the
key comes from the process environment.
Milestone 6 is not started and waits for measured live results. GitHub Actions cannot start on this account, so no CI gate
has passed; see `docs/ci-status.md` and never describe CI as green. Each
later milestone needs the owner's explicit authorization before work starts.
See the milestone table in `docs/spec.md`.

## Commands

```
uv sync                         # install dependencies from uv.lock
uv run pytest                   # run tests (sandbox tests skip without Docker)
uv run pytest -m sandbox        # sandbox tests only; needs `colima start` on macOS
uv run ruff check .             # lint
uv run ruff format .            # format
uv run mypy                     # strict type check
uv run pre-commit run --all-files
uv run patchpilot version
uv run patchpilot preflight --task <id>   # free, but contacts the API: needs a go-ahead
```

## Layout

- `src/patchpilot/`: the package. `cli.py` is the Typer entry point,
  `config.py` holds validated settings.
- `src/patchpilot/sandbox/`: restricted Docker runner, result models, safe
  tree packaging, JUnit parsing. `image/` holds the pinned image definition.
- `src/patchpilot/tasks/`: evaluation task schema, loader and grading.
- `src/patchpilot/intake/`: issue URL validation, allowlist, read-only GitHub
  client, untrusted-text handling, intake pipeline.
- `src/patchpilot/llm/`: provider interface, scripted fake model, spend
  ledger, Anthropic client.
- `src/patchpilot/agent/`: prompts, proposal format, baseline pipeline,
  sandbox verification, traces, reports.
- `src/patchpilot/patch/`: safe edit application, diff, patch policy.
- `src/patchpilot/eval/`: evaluation harness.
- `src/patchpilot/repo/`: hardened git fetch without checkout, in-memory
  snapshot, read-only tools with budgets.
- `evals/tasks/`: evaluation tasks. `repo/` is agent-visible; `internal/`
  never is. Fixture files are digest-pinned: do not reformat or edit them
  without updating the pin in `task.toml`.
- `tests/unit/`: tests that need no network, Docker or model.
- `tests/integration/`: tests that run real containers (marker `sandbox`).
- `docs/`: specification, threat model, decision records, sandbox, intake,
  baseline agent, evaluation tasks, grading integrity, CI status, licenses.

## Rules that must not be broken

- **No paid model calls without authorization.** Tests use the scripted
  fake model. Never pass `--live`, set an API key or construct
  `AnthropicModel()` for a real request without the owner's go-ahead for
  that specific run.
- **Every model request is reserved in the spend ledger before it is sent**
  and settled after. Failed requests are charged in full.
- **`candidate_verified` is not `resolved`.** Only hidden-test grading
  decides `resolved`; never report one as the other.
- **Raw traces stay local.** Share `report.md`, never `trace.jsonl`.
- **No installs or new dependencies without asking.** When one is added,
  update `docs/third-party-licenses.md`.
- **The host never executes repository code under analysis.** It runs only
  inside the sandbox. Target Python files are parsed, never imported.
- **The model never gets a shell.** Test commands come from the environment
  manifest, not from model output.
- **Safety is enforced in code, not in prompts.** Allowlist, path
  confinement, patch policy and budgets are all checked outside the model.
- **Issue text, repository content and test output are untrusted.** Keep them
  in `UntrustedText`; do not add `reveal()` calls outside the modules listed
  in `tests/unit/test_intake_text.py` without a reason stated in review.
- **Never build a command, path or URL from issue or file content.** Remote
  URLs come only from an allowlist entry.
- **Never check out a fetched repository on the host.** Read it from git
  objects into a snapshot.
- **Hidden evaluation tests never enter the agent's workspace, images or
  prompts.** Give an agent `Task.agent_view()` and `LoadedTask.agent_files()`
  only.
- **Grade only through `grade_candidate`.** It rebuilds the tree from pinned
  tests; never run hidden tests on a tree the candidate assembled.
- **Never weaken a sandbox restriction to make a test pass.** Security tests
  must attack the real container, not check flags.
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
