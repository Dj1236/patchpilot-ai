# Security review

A systematic pass over the threats, the controls in code, and the tests
that exercise them, as of 2026-11-14. **None of this makes prompt injection
or sandbox escape impossible.** The design assumes the model can be
manipulated and limits what a manipulated model can do.

## Repository and tools

| Threat | Control | Evidence |
|---|---|---|
| Unapproved repository | Allowlist checked before any request; repository id must match | `tests/unit/test_intake_allowlist.py`, `test_intake_pipeline.py` |
| Moving target | Commit pinned; snapshot read from git objects, never checked out | `tests/integration/test_repo_fetch.py`, `test_git_task.py` |
| Path traversal, symlinks, odd paths | Paths resolved and confined in code; symlinks and special entries skipped | `tests/unit/test_repo_tools.py` |
| Git hooks, submodules, LFS, filters | Fetched into a bare repository with a fixed configuration | `tests/integration/test_repo_fetch.py` |
| Huge repositories or outputs | Size limit per repository; tool call and output budgets | `tests/unit/test_repo_tools.py` |
| Prompt injection in issues and code | Kept as `UntrustedText`, shown in labeled blocks; the model has only four read tools | `tests/unit/test_intake_text.py`, scripted injection tests in `test_baseline.py` |

## Sandbox

| Threat | Control | Evidence |
|---|---|---|
| Network access | `--network none` | `tests/integration/test_sandbox_security.py` |
| Writing outside the workspace | Read-only root, tmpfs scratch space, no host mounts | same |
| Privilege | Non-root `--user`, `--cap-drop ALL`, `no-new-privileges` | same |
| Resource exhaustion | Memory, CPU, PID and time limits; output capped | `tests/integration/test_sandbox_limits.py` |
| Leftover processes | `--init`, process group kill, container removal checked | `test_sandbox_controls.py`, `tests/unit/test_sandbox_process.py` |
| Commands from the model | Test commands come from the task manifest only | `tests/unit/test_sandbox_models.py` |

Security tests attack a real container, not flags. Linux CI, the reference
environment, has not run them (see [ci-status.md](ci-status.md)).

## Model

| Threat | Control | Evidence |
|---|---|---|
| Keys in traces, reports, checkpoints, proposals | Known secret values redacted; canary tests | `test_baseline.py`, `test_resume.py`, `test_submit.py` |
| Silent switch to another model or a paid tier | One approved model per process; exact model identity check; Vertex and enterprise modes and a second key variable refused | `tests/unit/test_llm_gemini.py`, `test_model_selection.py` |
| Uncounted requests | SDK retries and continuation off; every request reserved in the ledger first; failures charged in full | `test_llm_gemini.py`, `test_llm_budget.py` |
| Budget bypass by restarting | Limits and counters restored from the checkpoint; stale checkpoints refused | `tests/unit/test_resume.py` |
| Untrusted model output | Strict proposal schema; edits must match exactly once; patch policy in code | `tests/unit/test_patch.py`, `test_baseline.py` |
| Rate limits and outages | Pacing, a cap of 8 generation attempts, one bounded retry, quota not retried | `test_llm_gemini.py` |

## GitHub writes

| Threat | Control | Evidence |
|---|---|---|
| Write without approval | `submit` refuses before any request without a valid approval | `tests/unit/test_submit.py` |
| Approval reused for another patch or target | Approval bound to the digest of the whole proposal and repeated fields | same |
| Model approving itself | Only `patchpilot approve` creates approvals, and a person must type the digest | same, plus the agent has no shell |
| Reviewer sees less than is pushed | Proposals that would be cut, or hold invisible or control characters, are refused; the review file shows every file in full | same |
| Cross-repository writes, merges, force pushes | Writer allows only listed requests to one repository; refs are only created | same |
| Broad credentials | Separate write token; classic tokens refused | same |
| Duplicate or replayed writes | Reads before every write; uncertain writes not repeated; existing PRs found | same |
| Stale base | Base branch head must equal the approved commit | same |

Two findings from automated security review of the submission code were
fixed: a proposal body that could cut the diff before review, and changed
files whose hidden characters could display differently from the pushed
bytes.

## Residual risks

- A container shares the host kernel. V1 accepts only approved repositories.
- Tests can forge their own results in the same process (expected failure in
  `test_grading_integrity.py`).
- Labeling untrusted text helps the model; it is not a control.
- The approval records the local user name, not a verified identity.
- No real GitHub write has been made; GitHub's real behavior is assumed from
  its documentation.
- Free-tier Gemini may use prompts for product improvement; only synthetic
  or approved public code may be sent.
