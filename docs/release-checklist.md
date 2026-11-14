# Release checklist

State on 2026-11-14. "Offline" means scripted model answers, a local fake
GitHub and the real sandbox.

## Complete and validated offline

- [x] Allowlist, pinned snapshot, read-only tools with budgets
- [x] Sandbox: no network, read-only root, non-root, resource limits (local engine; not on Linux CI)
- [x] Baseline agent, patch policy, sandbox verification
- [x] Independent hidden-test grading
- [x] Repair loop with bounded, controlled feedback
- [x] Traces, replay, JSON checkpoints, resume that fails closed
- [x] Spend ledger, pacing, attempt caps, no model fallback
- [x] Pull request proposal, approval bound to the exact proposal, idempotent submission (fake GitHub)
- [x] Four fixture tasks and a scripted baseline-versus-repair benchmark with JSON output
- [x] Security review and adversarial tests
- [x] Documentation, architecture diagram, offline demonstration
- [x] Local gates: ruff, format, strict mypy, pre-commit, full test suite, coverage

## Implemented, awaiting live validation

- [ ] A real model completing a baseline run (every live request so far: HTTP 503)
- [ ] A real model completing a repair run
- [ ] Live benchmark: one model, three runs per task and mode
- [ ] A first real draft pull request on an owned test repository (needs a write token and the owner's go-ahead)
- [ ] Building proposals from a real repository's intake snapshot (not wired)

## Blocked

- [ ] Hosted CI: runs end in `startup_failure` at account level ([ci-status.md](ci-status.md))
- [ ] Gemini free tier: HTTP 503 on both approved models ([pilot-plan.md](pilot-plan.md))

## Before a public portfolio release

- [ ] At least one live, graded run per mode, reported with its model and date
- [ ] Hosted CI green on Linux, sandbox tests included
- [ ] Owner review of all documentation for accuracy
- [ ] Owner decision to make the repository public (private until Milestone 8 is complete)
- [ ] Remove or rotate any credential ever used; confirm none is in git history
- [ ] Decide whether `runs/` artifacts from the pilot may be shared (`report.md` only, never `trace.jsonl`)

## Deferred

- Stronger isolation (gVisor or a microVM)
- Real open-source bugs in the task set; SWE-bench Lite
- A separate process to collect test results, against in-process forgery
- Resuming live runs
