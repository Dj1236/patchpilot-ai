# Milestone 5, step 12: live model pilot plan

Status: **planned, not authorized, not run.** No live model call has been
made, paid or free. Each live run needs the owner's explicit go-ahead for that run.

## Decided

- The free `patchpilot preflight` command exists (pull request #5). The
  owner has approved its two free requests, to be run by the owner once the
  key is configured. It has not been run with a credential yet.
- Key handling: `GEMINI_API_KEY` is provided through the environment of
  the process that starts PatchPilot. PatchPilot does not load `.env` by
  itself; see "Preflight and credentials" in [baseline.md](baseline.md). The
  key is set by the owner in a separate terminal, never printed and never
  pasted into chat.
- Spending thresholds, confirmed by the owner: hard caps of 0.75 USD per run
  and 5 USD for the whole pilot, enforced by the ledger; pause for review
  after a run above 0.40 USD or once the ledger passes 3 USD.

- Provider: the owner will not buy model credits during development. The
  pilot runs on free-tier Gemini 3.8 Flash; see decision 14 in
  [decisions.md](decisions.md). The Anthropic adapter stays, unused.

No decision is open. Each live run still needs its own go-ahead.

## Configuration

| Setting | Value |
|---|---|
| Provider and model | `gemini`, `gemini-3.8-flash`, free tier. No fallback |
| Effort | thinking level `medium` |
| Output limit | 16,000 tokens per request |
| Limits per run | 20 model turns, 60 tool calls |
| Retries | SDK retries off; at most 2 attempts per request, each budgeted; one 60 s wait after a rate limit |
| Server state | None: `store` off, no automatic continuation |
| Caps | 0.75 USD per run, 5 USD total, at list price, enforced by `runs/ledger.jsonl`. Expected real charge: zero |

## Step 0: no-cost checks

1. Full local suite and sandbox tests green on `main`.
2. The owner confirms in Google AI Studio that the key's project is on the
   free tier and has no billing account. Code cannot check this.
3. With `PATCHPILOT_PROVIDER=gemini` and `GEMINI_API_KEY` set in a separate
   terminal: `uv run patchpilot preflight --task inj-001-cachekit`. It makes
   two free requests and no generation request, and writes nothing to the
   ledger:
   - model metadata, to confirm the key and the model;
   - a token count of the first request, flattened to text. This is an
     estimate, and it does not validate the tools, the schema or the
     thinking setting.

The preflight contacts the API with the key, so it needs a go-ahead too. A
clean preflight does not guarantee that the first generation request
succeeds.

## Sequence: one run, then stop

| Step | Command | Gate |
|---|---|---|
| 1 | `patchpilot run --task inj-001-cachekit --live`, with `PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED=1` | Stop. Owner reviews the report and the provider's usage page. |

Any further run needs a new plan and a new go-ahead. Do not use
`patchpilot eval --live`: it runs every task and repeat in one go.

## Report after each run

Model turns, tool calls, input, output, cache-read and cache-write tokens,
cost, the ledger total against 5 USD, status, hidden-test result, whether
the right file was edited, and how the diff compares with the reference fix.

Ledger notes:

- Ledger amounts are list-price exposure, not money spent: the free tier
  charges nothing while the project has no billing account.
- Each request reserves its worst case first, about 0.13 USD for the output
  limit alone, so a run can stop as `budget_exceeded` while under its cap.
- A failed request is charged its full reservation, so the ledger can
  overstate spend. Reconcile it against the provider's usage page after
  run 1.

## Grading

- Success is `resolved` by hidden tests, decided after the run ends.
- `candidate_verified` is reported separately, with the count of runs where
  the two disagree.

## Stop conditions

Stop and report, without starting another run, on any of:

- `failed_infrastructure` (this includes a spent daily quota),
  `budget_exceeded` or `model_refused`.
- Any charge on the provider's billing or usage page.
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
`candidate_verified`, `resolved`, 3 model turns, 3 tool calls. Set the live
run beside it, labelled as scripted, not as a model result. Replay the live
trace offline with `--replay` and `PATCHPILOT_PROVIDER=gemini`, at no cost,
to confirm it reproduces.

## What the pilot can show

One run on one task shows whether the pipeline works against the real API:
that the provider accepts the request, that thought signatures survive a
tool round trip, and what the token usage is. It says nothing about quality.
A result that is not `resolved` is a valid outcome. Every result is recorded
with its provider and model and is never pooled with another model's.
