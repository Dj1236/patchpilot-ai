# CI status and the Milestone 2 gate exception

## Current state

**Hosted Linux CI has passed** on `main` commit `469074e`:
GitHub Actions run [`43586146851`](https://github.com/Dj1236/patchpilot-ai/actions/runs/43586146851), attempt 3, on 2026-11-14.
Ruff, formatting and strict mypy passed, and `pytest` reported 1,061 passed,
3 skipped and 1 expected failure. The run log shows the Docker sandbox and
grading tests executing on the Linux runner (`test_sandbox_security.py`,
`test_sandbox_limits.py`, `test_sandbox_controls.py`,
`test_grading_integrity.py`, `test_repair_tasks.py`). The three skips are
the live GitHub intake tests, which run only with `PATCHPILOT_LIVE_GITHUB=1`;
the expected failure is the known same-process forgery limitation
([grading-integrity.md](grading-integrity.md)).

Attempts 1 and 2 of that run were cancelled, not failed: the workflow's
`concurrency` group (`ci-refs/heads/main`, `cancel-in-progress: true`)
cancels a running `main` run when another `main` run is queued, and older
`main` runs were rerun at the same time. Rerun only the latest `main` run.

## History: the account billing lock (2026-11-12 to 2026-11-14)

Until the run above, no GitHub Actions run had completed for this
repository. Every run ended with `startup_failure` within a second of being created, with
zero jobs. An independent workflow containing a single `echo` step, pushed on
a throwaway branch on 2026-11-12, failed the same way. The workflow file
itself is registered by GitHub as valid and active. The cause is a
restriction on the GitHub account, outside this repository.

**Checked again on 2026-11-14, read-only:** the latest runs still end in
`startup_failure`, while Actions is enabled for the repository with all
actions allowed. The restriction is at account level (likely billing or a
spending limit); fixing it needs the owner in GitHub's account settings.
The same exception now covers Milestones 6 to 8.

**Cause confirmed on 2026-11-14:** after the repository became public, runs
are created with a job instead of failing at start-up, but the job ends
`failure` with no steps run. GitHub's annotation on the job reads: "The job
was not started because your account is locked due to a billing issue."
(runs `43571178275` for PR #20 and `43576231429` for its merge). No code
ran, so this says nothing about the code. The owner must clear the billing
lock at https://github.com/settings/billing; nothing in the repository can.

## Exception

The exception below applied while the account restriction blocked CI. It
is closed now that hosted Linux CI has passed. The project owner authorized
Milestone 3 to proceed while this external blocker remained, on these terms:

- The Milestone 2 CI gate stays recorded as **not passed**.
- Verification is done locally and reported as local verification only.
- No milestone report may describe CI as green until a real run succeeds.

## What local verification covers

Run on the developer machine before every commit:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Since Milestone 3 this includes the sandbox tests, which run real containers
through Colima. Since Milestone 4 it includes fetch tests that run real git
against local repositories. Since Milestone 5 it includes the baseline
pipeline with a scripted model; no test makes a paid call.

The same exception applied to Milestones 3 to 8 until run `43586146851`
passed on hosted Linux CI.

## What hosted CI now covers

- **Linux, the reference environment for the sandbox:** the sandbox tests
  ran against the GitHub runner's Docker engine, not only Colima on macOS.
- **A clean install from the lock file** (`uv sync --locked`) on an
  independent runner.

## Closing the exception (done)

1. The account restriction was resolved by the owner.
2. The `CI` workflow completed successfully on `main` (`469074e`).
3. The run log shows the sandbox tests running on the Linux runner, not
   skipped.
4. This file, the release checklist and the README were updated.
