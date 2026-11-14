# Portfolio summary

**PatchPilot AI: an autonomous bug-fixing agent with safety enforced in code.**

## Problem

Maintainers of small libraries spend time on reproducible bugs that have a
clear fix. Coding agents can propose such fixes, but most demos let the
model run commands, edit anything and report its own success. That is
neither safe nor measurable.

## Who it is for

Maintainers who want a reviewable, tested patch proposal for a bug report,
and teams evaluating whether an agent's fixes hold up under independent
tests.

## What it does

Given an issue in an approved Python repository, PatchPilot reads a pinned
snapshot with four read-only tools, asks a model for a structured patch with
a regression test, applies it under strict rules, verifies it in a sandbox,
optionally lets the model repair it from the test feedback, grades it with
hidden tests the agent never sees, and, after a person types the patch
digest to approve it, opens a draft pull request.

## Architecture and decisions

- Thin provider interface (Anthropic and free-tier Gemini), one approved
  model per run, no fallback, a spend ledger that reserves every request.
- Sandbox: Docker with no network, read-only root, non-root user, dropped
  capabilities and resource limits; test commands only from the task.
- Edits by exact search and replace, all or nothing; diffs computed by
  PatchPilot; a patch policy that forbids test and configuration changes.
- Repair loop as a small state machine of plain functions with JSON
  checkpoints, chosen over LangGraph because the loop is bounded and
  sequential.
- Resume fails closed by cross-checking the spend ledger, so a model request
  whose outcome is unknown is never repeated.
- Approval-gated GitHub writer: binds approval to a digest of the whole
  proposal, reads before every write, never merges.

## Evaluation

Four synthetic tasks (state, logic, validation, edge case) with hidden tests
and reference fixes. A scripted benchmark runs five scenarios per task in
both modes through the real sandbox and grading: baseline resolves 8 of 20,
repair 16 of 20. **These are hand-written answers that exercise the
pipeline, not a model's success rate.** A live run was attempted with
free-tier Gemini; the provider answered every request with HTTP 503, so no
model result exists yet.

## Lessons

- Separating "the agent says it works" from "independent tests say so"
  changed the design: grading is a separate step that feeds nothing back.
- Most of the engineering is in failure handling: uncertain network writes,
  stale checkpoints, rate limits, provider outages.
- Small diagnostics (a one-request probe) beat guessing when an external
  service fails.
- Automated security review found two real issues in the approval flow
  (truncated review content, hidden characters), both fixed.

## Resume bullet candidates

- Built an autonomous bug-fixing agent in Python that proposes, sandboxes
  and verifies patches, with allowlists, path confinement, patch policy and
  budgets enforced in code rather than prompts; about 1,050 tests, 94%
  coverage, strict typing.
- Designed independent evaluation that separates agent-reported
  verification from hidden-test grading, with four fixture tasks and a
  reproducible scripted baseline-versus-repair benchmark producing
  machine-readable results.
- Implemented a bounded repair loop as a JSON state machine with atomic
  checkpoints and a resume that fails closed by reconciling against an
  append-only spend ledger, so no request with an unknown outcome is
  repeated.
- Built an approval-gated GitHub pull request workflow that binds human
  approval to a digest of the exact proposal, reconciles remote state with
  read-only requests before every write, verified against a stateful fake
  GitHub API and validated with one real, approved draft pull request.
- Hardened a Docker test sandbox (no network, read-only root, non-root,
  dropped capabilities, resource limits) with tests that attack a real
  container.

Do not state real-world adoption, production use, or model success rates:
none exists yet.
