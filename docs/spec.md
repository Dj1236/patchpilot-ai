# PatchPilot AI: V1 specification

Status: approved (revision 2). Everything here is a V1 requirement unless it
appears under "Future enhancements".

PatchPilot AI takes one bug report against an approved Python repository and
returns a tested patch with evidence, for a human to accept or reject.

## 1. Scope

### V1 does

- Accept a GitHub issue URL or a structured bug file for an approved Python
  repository.
- Investigate the code with read-only, confined tools.
- Produce a targeted patch plus a regression test.
- Verify in a restricted container with bounded retries.
- Emit a report with the diff, test evidence, run manifest, trace and cost.
- Run a controlled evaluation comparing a single-attempt baseline with the
  stateful repair loop.

### V1 does not

- Support other languages or test frameworks (Python and pytest only).
- Run repositories that are not explicitly approved.
- Give test execution any network access.
- Merge anything, or write to GitHub without explicit approval.
- Use multiple agents or provide a web UI.

### Future enhancements

- Draft pull requests beyond the approval-gated Milestone 7 extension.
- Stronger isolation (microVM or gVisor) before accepting untrusted public
  repositories.
- More model providers. A second one, Gemini, was added in Milestone 5 by
  the owner's decision; see [decisions.md](decisions.md), record 14.
- Semantic code search.
- SWE-bench Lite evaluation.
- A web UI.
- Relaxing the add-only test rule.

## 2. Architecture and boundaries

### Trust zones

| Zone | Contains | Trust |
|---|---|---|
| Host orchestrator | CLI, config, agent, policy, store, model client, secrets | Trusted. Never imports or executes repository code. |
| Sandbox container | Workspace copy, pytest | Untrusted. No secrets, no network. |
| Evaluator | Hidden tests, grading | Trusted, and unreachable by the agent. |

### Components

| Component | Responsibility | Boundary rule |
|---|---|---|
| CLI | `run`, `resume`, `report`, `eval` | Paid calls require an explicit live flag; the default is the fake model. |
| Config | Allowlist, budgets, model ID, sandbox limits | Validated at start-up. |
| Intake | Fetch and normalize the issue | Allowlist is checked in code before any clone. |
| Workspace | Clone at a pinned SHA, apply edits, produce the diff | Hooks, submodules and LFS are disabled. |
| Repo tools | List, read, search | Paths are resolved and confined in code, including symlinks. Python files are parsed, never imported. |
| Model client | One thin interface; Claude Sonnet 5.5 (`claude-sonnet-5-5`), free-tier Gemini 3.8 Flash (`gemini-3.8-flash`) and a scripted fake | One pinned model per provider, chosen by configuration and never by fallback. A hard spend cap is enforced in code. |
| Patch policy | Enforce edit rules | Runs outside the model, before anything is written. |
| Sandbox | Run allowlisted test commands | Commands come from the environment manifest, never from the model. |
| Store and trace | Run state, manifest, per-step trace | Secrets are redacted before writing. |
| Evaluator | Grade with hidden tests | Runs only after the agent run has ended. |

### Approved repository

A repository is approved only when it is on the allowlist and has an
environment manifest written by the PatchPilot maintainers. The manifest
holds the base image digest, locked dependencies with hashes, the test
commands, and the protected paths. A repository without a manifest cannot
run.

## 3. States and termination

Happy path:

```
INTAKE -> PREPARE -> BASELINE_TESTS -> LOCALIZE -> GENERATE -> POLICY_CHECK -> APPLY -> VERIFY -> REPORT
```

| From | Condition | To |
|---|---|---|
| INTAKE | Repository not approved | REPORT (`refused`) |
| PREPARE, BASELINE_TESTS | Clone, image or runner fails | REPORT (`failed_infrastructure`) |
| POLICY_CHECK | Protected file touched or size limit exceeded | ANALYZE |
| APPLY | Edit target missing or ambiguous | ANALYZE |
| VERIFY | Regression test or suite fails | ANALYZE |
| ANALYZE | Budget remains | GENERATE |
| ANALYZE | Any budget spent | REPORT |
| VERIFY | All checks pass | REPORT (`candidate_verified`) |

### Budgets

Each of these ends the run when reached: attempts, tool calls, tokens, cost,
and wall-clock time.

### Terminal statuses

- `candidate_verified`
- `unverified_budget_exhausted`
- `rejected_policy` (the last failure was a policy violation)
- `failed_infrastructure`
- `refused`

The agent never reports `resolved`. Only the evaluator assigns that, using
hidden tests.

### Baseline mode

Baseline mode uses the same path with no ANALYZE step: one attempt, verified
once, no feedback. It is implemented as plain Python functions. The repair
loop reuses those same functions as LangGraph nodes.

### Safe edit application

- Each edit names a file, the exact original text expected, and the
  replacement.
- The original text must match exactly once. Zero or multiple matches reject
  the edit; nothing is guessed.
- A set of edits applies completely or not at all.
- V1 allows new files but not file deletion.
- The reported diff is always `git diff` of the modified workspace, never
  model output.

## 4. Threat model

See [threat-model.md](threat-model.md).

## 5. Testing and evaluation

### Testing

- Unit tests use the scripted fake model, so CI makes no paid calls.
- Sandbox safety tests run on Linux CI, the reference environment. They
  attempt network access, writes outside the workspace, resource exhaustion
  and reading a planted canary secret.
- Policy, path-confinement and edit-ambiguity tests are part of the suite.

### Evaluation

- **Tasks:** about eight injected bugs and five real open-source bugs,
  starting with two or three validated fixtures and expanding gradually.
- **Task validation:** before a task is admitted, its hidden tests must fail
  on the buggy commit and pass on the reference fix.
- **Hidden tests** live outside the workspace, are not in any image the
  agent's runs use, and their output is never shown to the model.
- **Grading:** after the agent stops, the evaluator copies the final tree and
  hidden tests into a fresh container. `resolved` means all fail-to-pass
  tests pass and no pass-to-pass test breaks.
- **Evidence levels:** the agent's own regression test is supporting
  evidence only; hidden-test grading is the independent result.
- **Comparison:** baseline against repair loop with the same model, tools
  and localization, three runs per task, reported as counts and ranges with
  cost per resolved task.
- **Metrics:** resolution, test pass, regression, edit-application failures,
  tool failures, attempts, time and cost.
- **Stated limits:** the set is small, and real open-source bugs may be in
  the model's training data.

### Reproducibility

- Every run records a manifest: repository commit, image digest, dependency
  versions, test commands, model ID, configuration, budgets and results.
- **Replay** re-reads a stored trace and reproduces the recorded run exactly,
  with no model calls.
- **Rerun** executes the tests again against the stored patch. Matching
  results are claimed only for tasks whose tests were shown to be
  deterministic by repeated baseline runs. Flaky tests are flagged and
  excluded.
- A fresh model run is not expected to produce the same patch.

### Spend

- Live experiments have a provisional ceiling of 10 USD.
- No paid call is made without explicit authorization.
- The cost of the full benchmark is estimated from measured pilot runs
  before a larger budget is requested.

## 6. Acceptance criteria

1. `patchpilot run` produces a complete report for an approved repository.
2. An unapproved repository, or one without an environment manifest, is
   refused before any clone.
3. All sandbox safety tests pass on Linux CI.
4. Missing or ambiguous edit targets are rejected, with tests proving it.
5. Patches touching protected files are rejected.
6. Hidden tests are provably absent from the agent's workspace, images and
   prompts.
7. Zero GitHub write calls occur without approval, proven against a fake API.
8. Every budget ends a run when reached, including the spend cap.
9. Replay reproduces a stored run; rerun matches on tasks marked
   deterministic.
10. The canary secret never appears in logs, traces or prompts.
11. Results for all tasks, both modes and three runs are published,
    including failures.
12. CI is green on lint, type check and tests.

## 7. Milestones and gates

| # | Milestone | Verification gate | Needs owner authorization for |
|---|---|---|---|
| 1 | Specification | Approval of this document | n/a |
| 2 | Foundation: repository, `CLAUDE.md`, tooling, CI | CI green on an empty package | Starting implementation |
| 3 | Sandbox and task format; first two or three fixtures | Safety tests pass on Linux CI | Installing a Docker runtime |
| 4 | Intake and repository tools | Allowlist and path-confinement tests pass | n/a |
| 5 | Baseline end to end, plus evaluator | One fixture graded `resolved` by hidden tests; pilot cost measured | First live calls. The pilot uses free-tier Gemini; any paid call stays within 10 USD |
| 6 | Stateful repair loop on LangGraph | Resume works; loop compared with baseline on the fixtures | n/a |
| 7 | Final report; approval-gated draft PR as an extension | Fake-API write test passes | Creating a write token |
| 8 | Full task set, results, documentation, release | Criteria 1-12 | Benchmark budget, estimated from the pilot |

The repository stays private until Milestone 8 is complete.

## 8. Technology decisions

See [decisions.md](decisions.md).

## 9. Remaining risks

- Container isolation is not a guarantee; a container shares the host
  kernel.
- The wheels-only, run-from-source rule excludes real projects that need a
  build step, so the real-bug tasks must come from pure-Python libraries.
- The add-only test rule will reject some legitimate fixes.
- Thirteen tasks give direction, not statistical proof.
- The full benchmark is about 78 live runs and may exceed the provisional
  ceiling; the Milestone 5 pilot will measure it.
- Source code from approved repositories is sent to the model provider,
  which is acceptable for public fixtures only. On the Gemini free tier the
  provider may also use it to improve its products.
- A free-tier pilot with a small, fast model measures the pipeline, not the
  best achievable quality. Results name their provider and model and are
  not comparable across models.
