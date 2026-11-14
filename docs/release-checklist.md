# Release gates

Audited on 2026-11-14 at `cf724b0`. "Offline" means scripted model answers,
a local fake GitHub, and the real sandbox on a local Docker engine.

| Gate | Status | Evidence | Remaining action | Owner approval needed |
|---|---|---|---|---|
| A. Offline engineering | **Passed** | `main` = `origin/main`, clean; PRs #15 to #17 merged; 1054 passed, 3 skipped, 1 expected failure; 94% coverage; ruff, format, strict mypy, pre-commit pass | None | No |
| B. Real-model validation | **Passed (one run)** | Local `qwen3:8b` (Ollama 0.40.16, Q4_K_M, digest `500a1f067a9f`), 2026-11-14, repair mode on `inj-001-cachekit`: 5 model-chosen tool calls, proposal 1 rejected (edit text not found), proposal 2 `candidate_verified`, hidden tests **resolved**; 7 generation attempts, 16,249 input and 874 output tokens, 60 s, $0 ([live-model-run.md](live-model-run.md)). Gemini free tier: 9 requests, all HTTP 503 | A statistically meaningful live benchmark is not done | Yes: every live request |
| C. Real GitHub draft PR | **Passed** | one draft pull request (#2) on the private synthetic repository `Dj1236/patchpilot-demo-cachekit`, 2026-11-14, run `inj-001-cachekit-1` (scripted, `candidate_verified`), base `3f3c79f`, commit `c664799`. Remote tree matched the fixture; branch bytes, paths and ancestry matched the approved proposal; `main` unchanged; a second `submit` created nothing; token revoked by the owner ([github-submission.md](github-submission.md)) | None | No (each further submission needs its own approval) |
| D. Hosted Linux CI | **Passed** | Run [`43586146851`](https://github.com/Dj1236/patchpilot-ai/actions/runs/43586146851) attempt 3 on `469074e`: ruff, format, strict mypy; 1,061 passed, 3 skipped, 1 expected failure; Docker sandbox and grading tests ran on Linux ([ci-status.md](ci-status.md)) | None | No |
| E. Public portfolio release | **Passed, experimental** | Repository public; landing page at https://patchpilot-ai-site.vercel.app/; A, C and D passed | All five gates passed | Yes: any further publication |

| Gate | Status | Evidence | Remaining action | Owner approval needed |
|---|---|---|---|---|
| F. Public online demo | **Built, not deployed** | `patchpilot.demo` (FastAPI) verified locally end to end with `qwen3:8b` and Docker: `inj-004-pager` resolved through the API in 33 s; `inj-003-rangespec` ended `invalid_proposal` after 3 proposals in the browser; 8 API tests for limits and failures ([deploy/README.md](../deploy/README.md)) | Create a host (Oracle Cloud Always Free or another Docker host), deploy, measure CPU-only run time, then link it from the website | Yes: account, host and public endpoint |

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
GitHub's real behavior and list consistency are unverified; the live model
result is one run of one small local model on one synthetic task.

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
