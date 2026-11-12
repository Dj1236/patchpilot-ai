# CI status and the Milestone 2 gate exception

## Current state

**The Milestone 2 CI gate has not passed.** No GitHub Actions run has ever
completed for this repository.

Every run ends with `startup_failure` within a second of being created, with
zero jobs. An independent workflow containing a single `echo` step, pushed on
a throwaway branch on 2026-11-12, failed the same way. The workflow file
itself is registered by GitHub as valid and active. The cause is a
restriction on the GitHub account, outside this repository.

## Exception

The project owner authorized Milestone 3 to proceed while this external
blocker remains, on these terms:

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
against local repositories.

The same exception applies to Milestones 3 and 4: both are verified locally
only, and neither has passed on hosted Linux CI.

## What local verification does not cover

- **Linux is the reference environment for the sandbox**, and it has not
  been exercised by CI. Locally, containers run inside a Linux virtual
  machine (Colima on macOS), so sandbox tests do run against a real Linux
  kernel, but not the one GitHub's runners use.
- A clean-machine install from the lock file has not been verified by an
  independent runner.

## To close the exception

1. Resolve the account restriction.
2. Push a commit and confirm the `CI` workflow completes successfully.
3. Confirm in the run log that the sandbox tests ran on the Linux runner
   and were not skipped. The workflow's `pytest` step includes them whenever
   a Docker engine is reachable.
4. Update this file and the milestone table in the README.
