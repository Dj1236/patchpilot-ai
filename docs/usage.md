# Setup and usage

## Requirements

- macOS or Linux, Python 3.12, [uv](https://docs.astral.sh/uv/).
- A Docker engine for the sandbox. On macOS: `brew install colima docker`,
  then `colima start`.
- No model API key is needed for anything on this page except the live
  commands marked as such.

```
git clone <this repository> && cd patchpilot-ai
uv sync
uv run patchpilot version
colima start                     # macOS
uv run pytest                    # full suite; sandbox tests need the engine
```

## Configuration

PatchPilot reads environment variables only. It does not load `.env`;
export variables in the shell that starts it. See `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `PATCHPILOT_PROVIDER` | `anthropic` | `anthropic`, `gemini`, or `ollama` (local `qwen3:8b` at `127.0.0.1:11434`, no key) |
| `PATCHPILOT_GEMINI_MODEL` | `gemini-3.8-flash` | Or `gemini-3.6-flash`; nothing else is accepted |
| `PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED` | off | The owner's statement that the Gemini key's project has no billing |
| `PATCHPILOT_MAX_ATTEMPTS` | 3 | Proposals in a repair run, at most 5 |
| `PATCHPILOT_RUN_CAP_USD` | 0.75 | Ledger cap per run |
| `PATCHPILOT_MAX_COST_USD` | 5.00 | Ledger cap across all runs |
| `PATCHPILOT_RUNS_DIR` | `runs` | Where runs, traces and the ledger go (git-ignored) |
| `PATCHPILOT_ALLOWLIST_PATH` | none | TOML list of approved repositories; nothing is approved without it |
| `PATCHPILOT_GITHUB_TOKEN` | none | Optional read-only token for intake |
| `PATCHPILOT_GITHUB_WRITE_TOKEN` | none | Fine-grained token for one repository, used only by `submit` |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | none | Model keys, read by the SDKs |

`PATCHPILOT_MODEL_ID` exists in the settings but has no effect: models are
selected per provider.

## Commands

Free and offline:

| Command | Does |
|---|---|
| `patchpilot run --task <id> --replay <trace> [--mode repair]` | Replays a recorded run, grades it |
| `patchpilot run ... --interrupt-after N` | Stops a repair run after N checkpoints |
| `patchpilot resume <run dir> --replay <trace>` | Continues a stopped repair run |
| `patchpilot benchmark --scripted` | Scripted baseline-versus-repair benchmark |
| `patchpilot show <run dir>` | Explains a run |
| `patchpilot propose-pr <run dir> --repository o/n --issue N` | Writes a pull request proposal |
| `patchpilot approve <run dir>` | Records a person's approval of that proposal |

Contact external services, each needs the owner's go-ahead:

| Command | Contacts |
|---|---|
| `patchpilot intake <issue url>` | GitHub, read-only |
| `patchpilot preflight --task <id>` | Model provider, free endpoints only |
| `patchpilot probe` | Gemini, one generation request |
| `patchpilot run --task <id> --live` | Model provider, generation |
| `patchpilot eval --live` | Model provider, many runs |
| `patchpilot submit <run dir>` | GitHub, **writes** a draft pull request |

## Offline demonstration

Everything below runs without a model API or a GitHub write. Steps 1 to 7
use the CLI; steps 8 to 10 run inside a test, because the GitHub side is a
local fake.

```
# 1-6. Scripted repair on the cachekit task: a wrong patch, rejected by the
#      sandbox, feedback, a correct patch, hidden-test grading. Records a trace.
uv run pytest -q tests/integration/test_resume_tasks.py -k commands --basetemp /tmp/pp-demo
TRACE=/tmp/pp-demo/test_the_commands_stop_and_res0/recorded/inj-001-cachekit-1/trace.jsonl

# 7. Stop the same run after 5 checkpoints, then resume it.
export PATCHPILOT_RUNS_DIR=/tmp/pp-demo/runs
uv run patchpilot run --task inj-001-cachekit --replay "$TRACE" --interrupt-after 5
uv run patchpilot resume <run directory printed above> --replay "$TRACE"
uv run patchpilot show <run directory>

# 8-10. Proposal, approval by typing the digest, submission to a fake GitHub,
#       and a second submission that creates nothing.
uv run pytest -q -s tests/integration/test_submit_tasks.py

# The scripted benchmark, about a minute.
uv run patchpilot benchmark --scripted --output /tmp/pp-demo/benchmark
unset PATCHPILOT_RUNS_DIR
```

Everything in the demonstration is scripted or simulated: the model's
answers are hand-written and GitHub is a local fake.

## Quality gates

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run pytest -m sandbox          # sandbox and end-to-end tests only
uv run pre-commit run --all-files
```

Hosted CI does not run for this account; see [ci-status.md](ci-status.md).

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `no Docker engine is reachable` | Start the engine: `colima start` |
| Sandbox tests skipped | Same: no engine was reachable |
| `refused: PATCHPILOT_...` | A setting is invalid; the message names it |
| `preflight failed (not_configured)` | The provider key is not exported in this shell |
| `HTTP 503 (service_unavailable)` | Provider overload; see `docs/pilot-plan.md` |
| `the checkpoint is stale` | The ledger shows work after the checkpoint; the run cannot be resumed safely |
| `a model request was in flight` | The process stopped during a request; the run is not resumed |
| `the approval does not cover this proposal` | The proposal changed after approval; approve again |
| `... has moved since the proposal was made` | The base branch moved; make a new proposal |
