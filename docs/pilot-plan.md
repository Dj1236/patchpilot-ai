# Milestone 5, step 12: live model pilot plan

Status: **planned, not authorized, not run.** No paid model call has been
made. Each live run needs the owner's explicit go-ahead for that run.

## Decided

- The free `patchpilot preflight` command exists (pull request #5). The
  owner has approved its two free requests, to be run by the owner once the
  key is configured. It has not been run with a credential yet.
- Key handling: `ANTHROPIC_API_KEY` is provided through the environment of
  the process that starts PatchPilot. PatchPilot does not load `.env` by
  itself; see "Preflight and credentials" in [baseline.md](baseline.md). The
  key is set by the owner in a separate terminal, never printed and never
  pasted into chat.

## Open decision (owner has not answered yet)

- Pause points: 0.40 USD for one run, 3 USD cumulative.

## Configuration

| Setting | Value |
|---|---|
| Model | `claude-sonnet-5-5` |
| Effort | `medium`, adaptive thinking |
| Output limit | 16,000 tokens per request |
| Limits per run | 20 model turns, 60 tool calls |
| Retries | SDK retries off; at most 2 attempts per request, each budgeted |
| Refusal fallback | Off |
| Caps | 0.75 USD per run, 5 USD total, enforced by `runs/ledger.jsonl` |

## Step 0: no-cost checks

1. Full local suite and sandbox tests green on `main`.
2. `uv run patchpilot preflight --task inj-001-cachekit`. It makes two free
   requests and no generation request, and writes nothing to the ledger:
   - model metadata, to confirm the key and the model;
   - a token count for the real first prompt, with tools, schema and
     thinking settings, to get the true input size and catch a malformed
     request before paying.

The preflight contacts the API with the key, so it needs a go-ahead too. A
clean preflight does not guarantee that the first paid request succeeds.

## Sequence: one run at a time

| Step | Command | Gate |
|---|---|---|
| 1 | `patchpilot run --task inj-001-cachekit --live` | Stop. Owner reviews report and cost. |
| 2 | `patchpilot run --task inj-002-exprcalc --live` | Stop. Owner reviews. |
| 3 to 6 | Two more runs of each task | Each only after approval. |

Do not use `patchpilot eval --live` for the pilot: it runs every task and
repeat in one go.

## Report after each run

Model turns, tool calls, input, output, cache-read and cache-write tokens,
cost, the ledger total against 5 USD, status, hidden-test result, whether
the right file was edited, and how the diff compares with the reference fix.

Ledger notes:

- Each request reserves its worst case first, at least 0.16 USD for the
  output limit alone, so a run that has spent about 0.58 USD stops as
  `budget_exceeded` although it is under its cap.
- A failed request is charged its full reservation, so the ledger can
  overstate spend. Reconcile it against the provider's usage page after
  run 1.

## Grading

- Success is `resolved` by hidden tests, decided after the run ends.
- `candidate_verified` is reported separately, with the count of runs where
  the two disagree.

## Stop conditions

Stop and report, without starting another run, on any of:

- `failed_infrastructure`, `budget_exceeded` or `model_refused`.
- A reported model that differs from the one requested.
- A run above 0.40 USD, or the ledger above 3 USD.
- A missing artifact, a sandbox container left behind, or a change to
  tracked files.

## Artifacts

- Per run, under `runs/<timestamp>/<task>-1/`: `report.md` and `record.json`
  (shareable for these fixtures), `trace.jsonl` (stays local).
- `runs/ledger.jsonl`.
- A sanitized results table for `evals/results/`, through a pull request.

## Comparison with the scripted baseline

The scripted reference on `inj-001-cachekit` is: correct fix,
`candidate_verified`, `resolved`, 3 model turns, 3 tool calls. Set each live
run beside it. Replay every live trace offline with `--replay`, at no cost,
to confirm it reproduces.

## What the pilot can show

Six runs on two tasks prove the pipeline against the real API and measure
its cost. They say almost nothing about quality. The cost estimate of under
0.30 USD per run is a guess until run 1 measures it.
