# PatchPilot AI

Autonomous software maintenance agent that turns a Python bug report into a
tested, reviewable patch.

PatchPilot AI takes a GitHub issue for an approved Python repository,
investigates the code, proposes a targeted fix with a regression test,
verifies it in a restricted container, and produces an evidence-backed
report for a human to accept or reject.

## Status

Early development. The project foundation is in place: packaging, linting,
type checking, tests and CI. No agent functionality exists yet.

| Milestone | State |
|---|---|
| 1. Specification | Done |
| 2. Foundation | Built; CI gate not passed ([details](docs/ci-status.md)) |
| 3. Sandbox and task format | Not started |
| 4. Intake and repository tools | Not started |
| 5. Baseline end to end, plus evaluator | Not started |
| 6. Stateful repair loop | Not started |
| 7. Report and approval-gated draft PR | Not started |
| 8. Evaluation and release | Not started |

## Design

- [Specification](docs/spec.md)
- [Threat model](docs/threat-model.md)
- [Technology decisions](docs/decisions.md)
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
