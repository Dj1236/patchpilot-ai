# PatchPilot AI

Autonomous software maintenance agent that turns a Python bug report into a
tested, reviewable patch.

PatchPilot AI takes a GitHub issue for an approved Python repository,
investigates the code, proposes a targeted fix with a regression test,
verifies it in a restricted container, and produces an evidence-backed
report for a human to accept or reject.

## Status

Early development. The sandbox, the evaluation tasks, read-only issue intake
and a single-attempt baseline agent are in place. The agent has only been run
with a scripted model. Two providers are supported, Claude and free-tier
Gemini. Two live pilot runs on free-tier Gemini were attempted; the provider
answered every request with HTTP 503, so there is no live result yet.

| Milestone | State |
|---|---|
| 1. Specification | Done |
| 2. Foundation | Built; CI gate not passed ([details](docs/ci-status.md)) |
| 3. Sandbox and task format | Built and verified locally; not yet run on Linux CI |
| 4. Intake and repository tools | Built and verified locally; not yet run on Linux CI |
| 5. Baseline end to end, plus evaluator | Built and verified locally with a scripted model; live pilot not run |
| 6. Stateful repair loop | Loop, checkpoints and resume built and verified offline with scripted models ([details](docs/repair-loop.md)); no live comparison yet |
| 7. Report and approval-gated draft PR | Built and verified offline against a fake GitHub ([details](docs/github-submission.md)); no real write made |
| 8. Evaluation and release | Not started |

## Design

- [Specification](docs/spec.md)
- [Repair loop](docs/repair-loop.md)
- [Threat model](docs/threat-model.md)
- [Technology decisions](docs/decisions.md)
- [Sandbox](docs/sandbox.md)
- [Issue intake and repository tools](docs/intake.md)
- [Baseline coding agent](docs/baseline.md)
- [Evaluation tasks](docs/evaluation-tasks.md)
- [Grading integrity](docs/grading-integrity.md)
- [Third-party licenses](docs/third-party-licenses.md)
- [CI status](docs/ci-status.md)

## Development

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv
if it is missing.

```
uv sync
uv run pre-commit install
uv run pytest
uv run patchpilot version
```

Tests marked `sandbox` need a running Docker engine (on macOS: `colima
start`). Without one they are skipped, not passed. The one test marked
`network` makes real read-only requests to GitHub and runs only with
`PATCHPILOT_LIVE_GITHUB=1`.

Checks that must pass before a change is merged:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Safety

PatchPilot AI runs only against explicitly approved repositories. Its
sandbox reduces risk but does not guarantee isolation against hostile code.
See the [threat model](docs/threat-model.md).

## License

[MIT](LICENSE)
