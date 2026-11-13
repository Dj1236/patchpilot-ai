# Repair loop

The repair loop is the baseline plus one thing: after a proposal that is not
accepted, the model is told why and may propose again, a small and fixed
number of times.

Status: **built and verified offline with scripted models, including
checkpoints and resume. No live model has run it.**

```
propose -> evaluate -> verified                   -> done
                    -> not accepted -> feedback   -> propose (next proposal)
                                                  -> done (proposals or a budget used up)
```

Run it with `patchpilot run --task <id> --mode repair`. The default mode is
still `baseline`. A replay needs no `--mode`: a trace is replayed in the mode
it was recorded in.

## What is shared with the baseline

Everything except the feedback step. Both modes call the same functions in
`agent/baseline.py`:

- `_explore_and_propose`: the model reads the snapshot with the four
  read-only tools and gives a proposal;
- `evaluate_proposal`: safe edit application, patch rules, then the three
  sandbox checks;
- `build_record`: the final record and manifest.

Hidden-test grading, the sandbox, the tool budgets, the spend ledger and the
trace are the same too. The comparison between the modes therefore measures
the feedback step and nothing else.

## State

A run's whole state is one `RepairState` object of plain JSON data: the
phase, the proposal number, the conversation, the pending proposal, every
attempt so far, the counters and the final status. Each step (`_propose`,
`_evaluate`, `_feedback` in `agent/repair.py`) is a function that changes the
state. After every step the state is complete and can be serialized; a test
round-trips it through JSON at each transition.

What is not in the state: the repository files, the sandbox, the model
client and the ledger. They are inputs, rebuilt from the task. The state is
stored as a checkpoint; see "Checkpoints and resume".

## Rules of a run

- **Every proposal starts from the original files.** A new proposal is never
  applied on top of an earlier one, so a run cannot drift from the snapshot.
- **The answer is the last proposal.** It alone is handed to the evaluator.
  If the last proposal could not be applied or broke a rule, the run is not
  graded, even if an earlier proposal produced a tree.
- **A verified proposal ends the run.** So does a sandbox or model failure,
  a refusal, and an answer that was cut off.
- **Proposals are limited.** `PATCHPILOT_MAX_ATTEMPTS`, default 3, at most 5.
  An answer that is not a usable proposal uses one up.

## What the model is told

After a proposal that was not accepted, one message with:

- which kind of problem it was: the answer or its edits could not be used, a
  patch rule was broken, or the sandbox checks did not hold;
- the reason and the result of each check that ran;
- up to five failing tests with their messages, each cut to 800 characters;
- how many proposals are left.

All of this comes from the agent's own checks: its proposal's errors, the
patch rules, the repository's visible tests and the regression tests it
wrote. **Hidden tests are not part of a run.** The evaluator runs them after
the run has returned, and nothing it finds goes back. A test sends a full
repair run through the real sandbox and checks that no hidden test name or
line appears in anything the model was given.

Test output is written by the code under test, so it is untrusted. It is
kept as `UntrustedText`, placed in a labeled block that it cannot close, and
bounded. This labeling is an aid to the model, not a control: the controls
are the same as in the baseline, enforced in code.

The system prompt differs from the baseline's in two sentences, which say
that results are reported and that a new proposal may follow.

## Budgets

All are for the whole run, across proposals. Any of them ends the run as
`budget_exceeded`.

| Budget | Default | Where it is checked |
|---|---|---|
| Proposals | 3 (max 5) | before feedback is given |
| Model turns | 20 | before every request |
| Tool calls and tool output | 60 calls | on every tool call |
| Tokens reported by the provider | 400,000 | before every request and before feedback |
| Generation attempts, retries included | 30 | the same |
| Wall-clock time, sandbox runs included | 1,200 s | the same |
| Cost | the run cap and the ledger | reserved before every request |

A Gemini run also keeps the adapter's own limit of 8 generation attempts per
process.

## Statuses

The same as the baseline's. When the proposals are used up, the status is
the last proposal's (`unverified`, `rejected_policy` or `invalid_proposal`)
and the detail starts with "no verified patch after N proposals". The
specification names this ending `unverified_budget_exhausted`; the existing
status names were kept so that both modes report the same set.

## Trace and replay

The trace records every request and answer, as in the baseline, plus one
`transition` event per step with the phase before and after, the proposal
number and the reason. A repair run replays from its trace with no model
call: the recorded answers are returned only if each request matches the
recorded one.

Replay re-runs the sandbox, and the feedback holds real test output, so a
replay matches only if that output is the same again. Object addresses in
test messages are replaced with `0x...` for that reason. A test whose
failure message changes from run to run (random data, timings) makes the
replay stop with "the replayed run sent a different request", which is the
intended behavior: an old answer is never paired with a new question.

## Results with scripted models

These show that the loop works and what it can and cannot do. **They are
not model results.** The scripts were written by hand: one gets the cachekit
fix wrong first and right after feedback; the other gives a partial fix for
exprcalc with a test that matches it. Real sandbox, real hidden-test
grading, one run per task and mode.

| Task | Mode | Verified by the agent | Resolved (hidden tests) | Proposals | Turns |
|---|---|---|---|---|---|
| inj-001-cachekit | baseline | 0 | 0 | 1 | 3 |
| inj-001-cachekit | repair | 1 | 1 | 2 | 5 |
| inj-002-exprcalc | baseline | 1 | 0 | 1 | 1 |
| inj-002-exprcalc | repair | 1 | 0 | 1 | 1 |

- On cachekit the feedback from the failing regression test is what turns a
  wrong patch into a resolved one.
- On exprcalc the loop does nothing: the partial fix passes the agent's own
  checks at once, so there is no feedback, and hidden tests still reject it.
  **The loop can only repair what its own evidence rejects.**

The table comes from `comparison_table` in `eval/harness.py`, exercised by
`tests/integration/test_repair_tasks.py`.

## Known limitations

- **No live model has run the loop.** Whether a real model uses the feedback
  well is unknown.
- **`candidate_verified` can still be wrong,** as in the baseline, and the
  loop stops on it.
- **The last proposal is the answer,** even when an earlier one was better.
- **Feedback is bounded and sampled:** five failing tests at most.
- **Replay depends on deterministic test output.**
- **A run resumes offline only,** and not after a stop in the middle of a
  model request; see "Checkpoints and resume".

## Checkpoints and resume

A repair run stores its state in `checkpoint.json` in its own run directory,
next to `trace.jsonl`. No framework and no database are involved; see
decision 15 in [decisions.md](decisions.md).

### What a checkpoint holds

- `schema_version` (1) and the run's fixed identity: `run_id`, `task_id`,
  mode, start time, the digest of the repository files, the prompt hash and
  the configuration hash.
- The run's limits: `RunLimits` (provider, model, turns, tools, policy) and
  `RepairLimits`, and the run cap.
- `sequence`: the number of this checkpoint within the run.
- `state`: the `RepairState`: phase, proposal number, the conversation, a
  pending proposal, every attempt with its verification, and the counters
  (model turns, generation attempts, retries, token-count requests, tokens,
  tool calls and tool output, cost, elapsed time).
- A SHA-256 hash of all of the above.

It holds what the model was shown and answered, like the trace, so it stays
local. Known secret values are removed before writing. It holds nothing
from hidden tests, because a run never has them; a test checks the stored
file. The file and its directory are readable by the owner only. Run
directories live under `runs/`, which git ignores.

### When it is written

At boundaries where the state is complete, and nowhere else:

1. **Before every model request.** The previous answer has been received,
   its cost settled in the ledger and its tool calls answered.
2. **After every step:** a proposal was given; it was evaluated; feedback
   was given; the run ended.

Writing is atomic and durable: the content goes to a temporary file in the
same directory, is flushed to disk, replaces the old file in one step, and
the directory is flushed. A reader sees the old checkpoint or the new one.

### Resuming

```
patchpilot resume <run directory> --replay <trace>
```

The run continues in the same directory with the same run id. It keeps its
limits and everything it has used: proposals, model turns, generation
attempts, tokens, tool calls, cost and elapsed time. The limits are read
from the checkpoint, not from the current settings, so restarting cannot
raise them. Nothing completed is done again. A run that had already
finished is not run again: its result is rebuilt from the checkpoint and
graded.

**V1 resumes offline only.** The remaining answers come from a trace, with
the answers the run already received skipped. Resuming a live run is not
offered: the Gemini adapter's limit of 8 generation attempts is per process
and would start again, and no live run is authorized in any case.

### What is refused

Each of these stops the resume before any model call:

- no checkpoint, a symlink, a file larger than 16 MB, invalid JSON, a wrong
  content hash, another schema version, an unknown field;
- a state that contradicts itself (phase, pending proposal, proposal count);
- another task, changed task files, changed prompts, limits that do not
  match the stored configuration hash, a trace beside it from another run;
- a run directory outside the runs folder;
- **a checkpoint the spend ledger shows to be out of date.** The ledger is
  written before and after every model request. If it holds a reservation
  for this run that was never settled, a request was in flight when the
  process stopped, and whether it completed cannot be known. If it holds
  more requests than the checkpoint counts, work was done after the
  checkpoint was written. In both cases the run is not continued, and the
  open reservation stays charged in full.

### What a stop in the middle of something does

| The process stopped | On resume |
|---|---|
| at a checkpoint | continues from there |
| during sandbox checks | the checks of that proposal run again from the start; they have no side effects and no cost; the proposal is not asked for again |
| during a model request | refused: outcome unknown |
| after an answer, before the next checkpoint | refused: the checkpoint is stale |
| while writing a checkpoint | the previous checkpoint is intact; refused if a request completed since |

A refused run is not lost: its trace, ledger entries and last checkpoint
stay as they are. It is simply not continued; a new run can be started.

### Trying it

`--interrupt-after N` stops a repair run after its Nth checkpoint, as a
process failure would. With a recorded repair trace and a scratch runs
folder, so the real ledger is not touched:

```
export PATCHPILOT_RUNS_DIR=/tmp/patchpilot-demo/runs
uv run patchpilot run --task inj-001-cachekit --replay <trace> --interrupt-after 5
uv run patchpilot resume <run directory printed above> --replay <trace>
```

### Limits of resume

- Offline only, as said above.
- A stop during a model request, or between an answer and its checkpoint,
  ends the run for good.
- Time while the process was down is not counted against the time budget.
- Only repair runs are checkpointed. A baseline run is one short attempt.
- The content hash detects damage, not forgery: someone who can edit the
  file can also recompute the hash. The cross-checks against the ledger,
  the trace and the task still apply.

## What is left for Milestone 6

- The comparison on live runs, three per task and mode, once a provider
  answers. Everything that can be done offline is done.
