# Release gates

Audited on 2026-11-14 at `cf724b0`. "Offline" means scripted model answers,
a local fake GitHub, and the real sandbox on a local Docker engine.

| Gate | Status | Evidence | Remaining action | Owner approval needed |
|---|---|---|---|---|
| A. Offline engineering | **Passed** | `main` = `origin/main`, clean; PRs #15 to #17 merged; 1054 passed, 3 skipped, 1 expected failure; 94% coverage; ruff, format, strict mypy, pre-commit pass | None | No |
| B. Real-model validation | **Blocked** | 8 generation requests on `gemini-3.8-flash` and `gemini-3.6-flash`, all HTTP 503 ([pilot-plan.md](pilot-plan.md)) | One successful `probe`, then one baseline and one repair run on `inj-001-cachekit`, graded | Yes: every live request, and any provider or paid-tier change |
| C. Real GitHub draft PR | **Passed** | one draft pull request (#2) on the private synthetic repository `Dj1236/patchpilot-demo-cachekit`, 2026-11-14, run `inj-001-cachekit-1` (scripted, `candidate_verified`), base `3f3c79f`, commit `c664799`. Remote tree matched the fixture; branch bytes, paths and ancestry matched the approved proposal; `main` unchanged; a second `submit` created nothing; token revoked by the owner ([github-submission.md](github-submission.md)) | None | No (each further submission needs its own approval) |
| D. Hosted Linux CI | **Passed** | Run [`43586146851`](https://github.com/Dj1236/patchpilot-ai/actions/runs/43586146851) attempt 3 on `469074e`: ruff, format, strict mypy; 1,061 passed, 3 skipped, 1 expected failure; Docker sandbox and grading tests ran on Linux ([ci-status.md](ci-status.md)) | None | No |
| E. Public portfolio release | **Passed, experimental** | Repository public; landing page at https://patchpilot-ai-site.vercel.app/; A, C and D passed | A non-experimental release needs gate B | Yes: any further publication |

## What may proceed without approval

- Re-running gate A locally after any change.
- Documentation corrections that make the docs match the code.
- Preparing, but not running, the commands for gates B and C.

## Proven controls versus documented limitations

Proven by tests (locally): allowlist and pinned snapshots, path confinement,
patch policy, sandbox network and filesystem and privilege and resource
limits against a real container, hidden-test isolation from the agent,
spend ledger and pacing and attempt caps, no model fallback, checkpoint
validation and fail-closed resume, approval binding to the exact proposal,
the write allowlist, and idempotent submission against a fake GitHub.

Documented limitations, not solved: tests can forge their own results in the
same process; the container shares the host kernel; the approval is an
unsigned local file; the token's repository scope is not checked by code;
GitHub's real behavior and list consistency are unverified; no live model
result exists.

## Before a public release

- [ ] Gates B and D passed, reported with model, date and run ids
- [x] Gate C passed on an owned repository (draft PR #2, private synthetic repository)
- [ ] Owner review of README, usage, architecture, evaluation, security review and portfolio summary
- [ ] No credential in git history; keys used in the pilot rotated
- [ ] Only `report.md` files shared from `runs/`, never traces or checkpoints
- [x] Owner decision on repository visibility (public)

## Deferred, not needed for release

Stronger isolation, real open-source tasks, cross-process collection of test
results, resuming live runs, building proposals from a real intake snapshot.
