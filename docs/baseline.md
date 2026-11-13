# Baseline coding agent

The baseline turns one bug report into one proposed patch in a single
attempt. It is the reference point that the Milestone 6 repair loop will be
compared against.

```
bug report + snapshot
  -> EXPLORE   the model calls the read-only tools, within budgets
  -> PROPOSE   the model gives one structured patch
  -> APPLY     edits are applied to an in-memory copy
  -> POLICY    patch rules are checked in code
  -> VERIFY    three sandbox runs; results are recorded, never shown to the model
  -> REPORT
```

The model sees no test result and gets no second proposal. Tool calls while
exploring are reading, not retrying.

**No live model call has been made yet.** Everything described here was
built and tested with a scripted model and a stand-in for the provider's
SDK. The first real calls are a separate, authorized step.

## Statuses

| Status | Meaning |
|---|---|
| `candidate_verified` | A new regression test failed before the patch, that same test passed after it, and the existing tests passed |
| `unverified` | The patch applied, but one of those three checks did not hold, or a test run hit a limit |
| `invalid_proposal` | The answer was cut off, was not valid, or an edit's target was missing or ambiguous |
| `rejected_policy` | The patch broke a patch rule. It is never run |
| `model_refused` | The model declined. No other model is tried |
| `budget_exceeded` | A spend, model-turn or tool-call limit was reached |
| `failed_infrastructure` | The model API or the sandbox failed |

`candidate_verified` is the agent's own evidence. The regression test was
written by the same model as the fix, and test results come from a process
that runs the code under test. `resolved` is decided only by the evaluator,
with hidden tests, after the run has ended. The evaluation counts the runs
where the two disagree.

## Patch rules

- An edit names exact text that must occur exactly once in the file. A
  missing, ambiguous or no-op edit rejects the whole proposal.
- Existing tests, `conftest.py`, pytest and packaging configuration, and CI
  files may not be edited.
- A new file must be a test file named `test_*.py` in a test directory.
- No deletions. At most 5 files and 300 lines changed.
- The diff is computed from the before and after trees with `difflib`, in
  git's format. Tests confirm the diffs apply with `git apply`.

## Spend control

Every model request goes through reserve, send, settle.

- **Reserve.** Before sending, the input is counted and a worst case is
  reserved: every input token at the dearest input rate, plus the whole
  output limit, which also bounds thinking tokens. If that does not fit the
  run cap (0.75 USD) or the total cap (5 USD), the request is not sent.
- **Settle.** After a response, the reservation is replaced by the cost of
  the usage it reported: input, output, cache writes and cache reads.
- **Failures are charged.** A request that errors is settled at its full
  reservation, because it may have been billed. Retries are done by
  PatchPilot, at most two attempts, each with its own reservation; the SDK's
  own retries are off.
- **The ledger persists.** It is an append-only file under `runs/`, locked
  across processes, so the total cap holds between invocations and under
  concurrent runs. If the ledger cannot be read, nothing is sent. A reservation left open by a crash keeps counting in full.

Prices are pinned in `llm/budget.py` with the date they were read. A model
with no pinned price cannot be called.

## Preflight and credentials

`patchpilot preflight --task <id>` makes two free requests and no generation
request:

1. the model-metadata endpoint, to confirm the credential works and the model
   is available to it;
2. the token-counting endpoint, for the exact first request the baseline
   would send, with its tools, answer schema, effort and thinking settings.

It then prints the provider's token count, the cost of that input alone, and
the larger amount the paid path would reserve for the first request, and
says whether that reservation fits the run cap and the total cap. It writes
nothing to the ledger.

A clean preflight shows the credential, the model and the request shape are
acceptable for counting. **It does not guarantee the first paid request
succeeds.**

Failures are reported by kind (`no_credentials`, `authentication`,
`permission`, `model_unavailable`, `malformed_request`, `rate_limited`,
`connection`, `provider_error`). The provider's own explanation of a
rejected request is shown, bounded and with known secrets removed. Prompt
contents are never printed.

**Credentials.** Use a dedicated key for PatchPilot. Preferred: export
`ANTHROPIC_API_KEY` in the shell that starts PatchPilot, from a secret store
or a file outside the repository. Acceptable: a git-ignored `.env` in the
repository with permissions `600`; PatchPilot does not load it by itself, so
it must be loaded into the environment by the person running the command.
The key is read only by the SDK. PatchPilot never reads, prints, logs or
stores it.

## Traces, reports and replay

| File | Contents | Audience |
|---|---|---|
| `trace.jsonl` | Every request and response, including thinking blocks and file contents | Local only. Owner-readable, under the ignored `runs/` folder, known secrets redacted |
| `record.json` | Status, evidence, usage, cost, diff, manifest | Local |
| `report.md` | Sanitized summary: status, evidence, diff, cost, manifest | Shareable for public repositories. It contains the diff, so it shows the repository lines around the change |

The report and the record never contain prompts, thinking or the trace, and
known secret values are removed from them as they are from the trace. Text the model
wrote and the diff are bounded, cleaned and rendered so they cannot break
out of their place in the document.

`patchpilot run --task <id> --replay <trace>` re-runs a recorded run with no
model call. If the replayed run would send a different request than the one
recorded, it stops instead of pairing an old answer with a new question. A
trace that is damaged or incomplete is refused, and the command fails if the
replayed status or diff differs from the outcome the trace recorded. That
check catches damage and drift. It is not protection against someone who
edits a trace deliberately, since the recorded outcome sits in the same file.

The manifest records the model requested and the model the API reported,
the effort, hashes of the prompts and the configuration, the repository
digest, the sandbox image and the caps.

## Model settings

- `claude-sonnet-5-5`, effort `medium`, adaptive thinking.
- No sampling settings: this model rejects non-default values, so runs are
  not deterministic. Reproducibility comes from replaying traces, and
  variation is measured by repeating runs.
- No refusal fallback, so a result is always from the model that was asked.
- The conversation is append-only and content blocks are sent back exactly
  as received.

## Security boundaries

- The model's output can only become a read-only tool call, or a proposal
  that passes validation and policy and then runs in the sandbox.
- Tests script the model to follow injected instructions: edit a test, add a
  `conftest.py` or `sitecustomize.py`, read `../../etc/passwd`, call a tool
  that does not exist, exceed its budgets. Each is stopped by code.
- Hidden tests and reference fixes are never in the agent's files, and a
  test checks nothing given to the model contains them.
- All generated code and tests run only in the sandbox.
- Every test runs with model credentials removed and the SDK pointed at an
  unused local address, so no test can reach the provider.
- The API key is read by the SDK from the environment. PatchPilot never
  reads, stores or logs it, and redacts it from traces if it ever appears.
- Labeling untrusted text in prompts is an aid to the model, not a control.

## Known limitations

- **Unproven against the real API.** Request shapes follow the SDK and are
  tested against its types, but no request has been sent. The token-count
  margin, the cost estimate and returning thinking blocks unchanged are all
  unverified until the pilot.
- **`candidate_verified` can be wrong,** as described above. One test shows
  a partial fix that the agent verifies and hidden tests reject.
- **In-process forgery of test results** is still not detected. See
  [grading-integrity.md](grading-integrity.md).
- **The reservation is conservative,** so a run can stop for budget while
  real spend is well under its cap.
- **Redaction covers known secret values only,** in traces, records and
  reports alike. Traces hold
  repository content and must stay local.
- **Only evaluation tasks can be run from the command line.** Running from
  a GitHub issue URL is wired up to the intake pipeline but has no command
  yet.
- **Two fixtures** are too few to say anything about quality.
- **Not run on hosted Linux CI.** See [ci-status.md](ci-status.md).
