# PatchPilot AI

Autonomous software maintenance agent: one Python bug report in, one tested
and reviewable patch out. The approved design is in `docs/spec.md`; read it
before changing behavior.

## Current state

Milestones 2 to 4 and the Milestone 5 baseline agent (steps 1 to 11) are
merged and verified locally, and so is the free `patchpilot preflight`
command. The owner will not buy model credits during development: the pilot
uses free-tier Gemini (`PATCHPILOT_PROVIDER=gemini`, `gemini-3.8-flash`),
the Anthropic adapter stays as an optional provider. Two live pilot runs and
one minimal probe were made on 2026-11-13; all five generation requests got
HTTP 503 from the provider, so there is still no model output and no graded
result. See "Outcome so far" in `docs/pilot-plan.md`. Each further live
request needs its own go-ahead. Keep `runs/` and its ledger as they are.
Milestone 6 is built offline with the owner's authorization: the repair
loop (`--mode repair`), JSON checkpoints and `patchpilot resume` are
verified with scripted models only (`docs/repair-loop.md`). LangGraph was
not adopted (decision 15). Milestone 7 (approval-gated draft PRs,
`docs/github-submission.md`) and the offline part of Milestone 8 (four tasks,
`patchpilot benchmark --scripted`, security review, release docs) are done.
One real draft PR was validated on a private synthetic repository (gate C).
Still open: any live model result (gate B). See
`docs/release-checklist.md`.
PatchPilot does not load `.env`; the key comes from the process environment.
Milestone 6 is not started and waits for measured live results. Hosted Linux CI passed on `469074e` (run 43586146851,
sandbox tests included); see `docs/ci-status.md`. Describe CI as green only
for a run that completed successfully, never for a cancelled one. Each
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
uv run patchpilot probe                   # one real Gemini generation request: needs a go-ahead
uv run patchpilot resume <run dir> --replay <trace>   # continue a stopped repair run, offline
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
  ledger, Anthropic client, Gemini client, preflight.
- `src/patchpilot/agent/`: prompts, proposal format, baseline pipeline,
  repair loop, checkpoints,
  sandbox verification, traces, reports.
- `src/patchpilot/patch/`: safe edit application, diff, patch policy.
- `src/patchpilot/eval/`: evaluation harness.
- `src/patchpilot/submit/`: pull request proposal, approval and the
  approval-gated GitHub writer.
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

- **No live model calls without authorization, free tier included.** Tests
  use the scripted fake model or a local transport. Never pass `--live`, set
  an API key or construct `AnthropicModel()` or `GeminiModel()` for a real
  request without the owner's go-ahead for that specific run.
- **No fallback to another model or provider.** One model per process, taken
  from the approved list in `llm/base.py`; the owner selects a Gemini model
  with `PATCHPILOT_GEMINI_MODEL`. Add a model to that list only after its
  documentation, free-tier limits and price are checked, and never switch
  model after a failure. Gemini runs only on the free tier: never enable Vertex AI,
  enterprise mode or a billed key, and never set
  `PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED` yourself; that is the owner's
  statement.
- **Results name their provider and model.** Never pool or compare results
  from different models as if they were one experiment.
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
- **Feedback to the model comes only from the agent's own checks.** In a
  repair run the model may be told about its proposal's errors, the patch
  rules and tests it can see. Never pass anything from grading or hidden
  tests back into a run.
- **Never repeat a model request whose outcome is unknown.** A resume is
  refused when the ledger shows work after the checkpoint. Do not weaken
  that check, and do not edit or reset the ledger to get past it.
- **Grade only through `grade_candidate`.** It rebuilds the tree from pinned
  tests; never run hidden tests on a tree the candidate assembled.
- **Never weaken a sandbox restriction to make a test pass.** Security tests
  must attack the real container, not check flags.
- **No GitHub write without explicit approval.** Only `patchpilot submit`
  writes, only after `patchpilot approve` bound a person's approval to the exact
  proposal, and only drafts. Never add a merge, ref update, force push or
  write to a default branch. Never run `submit` against real GitHub, or create
  a write token, without the owner's go-ahead for that submission. Tests use
  `tests/fake_github.py`.
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
