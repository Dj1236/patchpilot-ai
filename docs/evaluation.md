# Evaluation

## What is measured, and what is not

PatchPilot separates two kinds of evidence:

- **`candidate_verified`**: the agent's own checks in the sandbox. Its new
  regression test fails on the original code, passes on the patched code,
  and the existing tests still pass. This is supporting evidence: the same
  agent wrote the fix and the test.
- **`resolved`**: decided only by `grade_candidate`, after the run has
  ended. It rebuilds the tree from the pinned starting state, adds the
  hidden tests last, and requires every fail-to-pass test to pass and every
  pass-to-pass test to keep passing. Nothing from grading goes back to the
  agent.

**No real model has completed a run.** Every result below comes from
scripted, hand-written answers. They show what the pipeline does with known
kinds of answers. They are not the success rate of any language model, and
nothing here supports a claim about one.

## Tasks

Four small synthetic Python libraries, each with an injected bug, hidden
tests, a reference fix and a pinned tree digest. See
[evaluation-tasks.md](evaluation-tasks.md).

| Task | Kind of bug | Fail-to-pass | Pass-to-pass |
|---|---|---|---|
| `inj-001-cachekit` | State management: rewritten key not marked recently used | 3 | 14 |
| `inj-002-exprcalc` | Logic: subtraction and division grouped right to left | 6 | 26 |
| `inj-003-rangespec` | Input validation: reversed and zero-start ranges accepted | 2 | 9 |
| `inj-004-pager` | Edge case: partial last page lost | 2 | 7 |

Each task is checked automatically (`tests/integration/test_fixture_tasks.py`):
the bug reproduces, the reference fix resolves it, a plausible wrong fix
does not, and nothing from the hidden tests is visible to the agent.

## Scripted scenarios

`uv run patchpilot benchmark --scripted` runs each scenario on each task in
both modes, with the real sandbox and real hidden-test grading.

| Scenario | The scripted answers |
|---|---|
| `right-first` | The reference fix and a regression test |
| `wrong-then-right` | A plausible wrong fix, then the reference fix |
| `policy-then-right` | The fix plus a new `conftest.py` (forbidden), then the fix alone |
| `probe-hidden-then-right` | Tool calls aimed at hidden files and paths outside the snapshot, then the fix |
| `no-usable-answer` | Text that is not a proposal, every time |

The scripted regression tests and wrong fixes are in `evals/scripted/`; a
test checks that none of them copies a line found only in hidden tests.

## Results (scripted / offline)

From `evals/results/scripted-benchmark.md`, 20 runs per mode:

| Mode | Resolved (hidden tests) | Verified by the agent | Proposals | Sandbox runs, grading included |
|---|---|---|---|---|
| Baseline | 8 of 20 | 8 | 16 | 44 |
| Repair | 16 of 20 | 16 | 36 | 72 |

How to read this:

- The difference is **built into the scripts**: four of the five scenarios
  give a good answer second. It shows that the repair loop's feedback,
  bounds and grading work as designed, not that feedback helps a model.
- `probe-hidden-then-right` resolves in both modes: the tool calls get
  errors, nothing hidden is returned, and the run continues.
- `no-usable-answer` never resolves; repair uses all three proposals.
- In `wrong-then-right` on `inj-004-pager`, the baseline's wrong fix passes
  both fail-to-pass tests but breaks 3 of 7 pass-to-pass tests: the hidden
  regression checks matter.
- No benchmark scenario produces "verified but not resolved". That case is
  shown in `tests/integration/test_repair_tasks.py`: a partial exprcalc fix
  with a matching test is verified by the agent and rejected by hidden
  tests, in both modes.

Token counts and ledger figures in the JSON are the script's placeholders.

## Integrity

| Concern | Status |
|---|---|
| Agent sees hidden tests | Prevented in code: hidden tests are not in the agent's files, images or prompts; tests check every message sent |
| Feedback leaks grading | Prevented: grading runs after the run returns; feedback comes only from visible checks |
| Patch edits or adds tests to game grading | Patch policy forbids changing existing tests and adding config files; grading takes tests from the pin and drops candidate test files |
| Overfitting to the agent's own test | Not prevented by the agent; caught by hidden tests (see the exprcalc case) |
| Test results forged by code under test in the same process | **Not prevented.** Known V1 limitation, kept as an expected failure; see [grading-integrity.md](grading-integrity.md). Mitigation needs a second, separate process to collect results, which is a sandbox redesign |
| False negatives or positives from flaky tests | Not checked: no task is run repeatedly to prove its tests deterministic. The fixture tests are written without randomness or timing |
| Replay determinism | Replay matches requests by digest and stops on any difference; object addresses in test output are normalised |
| Failure classification | Every run ends in one status; infrastructure failures are separate from patch failures |

## Limits of this evaluation

- Four tasks, all written for this project. Real open-source bugs are not
  in the set yet.
- The answers are scripted by the same developer who wrote the tasks.
- One run per task, scenario and mode; scripted runs are deterministic.
- Nothing here is a model benchmark. The live comparison, three runs per
  task and mode with one real model, is pending: every live request so far
  was answered with HTTP 503 by the provider.
