# Live model run (release gate B)

One real-model repair, run on 2026-11-14. It shows that the full pipeline
works with a real model; it is one run of one small model on one synthetic
task, **not a success rate**.

## Why a local model

Free-tier Gemini answered all nine generation requests with HTTP 503
(`service_unavailable`): eight on 2026-11-13 ([pilot-plan.md](pilot-plan.md))
and one probe on 2026-11-14. That probe was made from a shell that still had
the gate C settings, so its record and its $0.0011 ledger reservation are in
`runs/gate-c-20261114T134937Z/` rather than `runs/ledger.jsonl`; neither was
moved or edited. The owner then authorized a local open-weight model.

## Setup

| | |
|---|---|
| Provider | `ollama` (`src/patchpilot/llm/ollama_client.py`), only `http://127.0.0.1:11434` |
| Model | `qwen3:8b`, 8.2B parameters, Q4_K_M, Ollama digest `500a1f067a9f` (Apache-2.0 weights, not in this repository) |
| Runtime | Ollama 0.40.16 on an Apple M5, 16 GB |
| Options | `temperature` 0, `num_ctx` 32,768, thinking off, `num_predict` = the run's output-token limit |
| Price in the ledger | 0 (local inference); every request still reserved and settled in `runs/ledger.jsonl` |
| Limits | The normal repair limits: 3 proposals, 400,000 tokens, 30 generation attempts, 1,200 s; tool budgets unchanged |

Ollama's `format` option would stop the model from calling tools, so the
answer schema is given in the system prompt; PatchPilot validates the answer
with the same parser as for every provider. One code fence around the whole
answer is removed before parsing; nothing else is changed.

## Command and result

```
PATCHPILOT_PROVIDER=ollama uv run patchpilot run --task inj-001-cachekit --live --mode repair
```

Run directory `runs/20261114T154654479553Z/inj-001-cachekit-1` (local; the
trace stays local, the report is shareable).

- The model chose 5 tool calls: `list_files` (`src`), `read_file`
  (`lru.py`), `search_code` (`put`), `outline` (`lru.py`), `read_file`.
- Proposal 1: `invalid_proposal`, its edit's expected text was not in
  `src/cachekit/lru.py`. That was fed back.
- Proposal 2: `candidate_verified`. It adds `self._entries.move_to_end(key)`
  before the assignment in `put()` (the reference fix places it after) and a
  new regression test `tests/test_lru_cache.py`.
- Independent hidden-test grading: **resolved**.
- 7 model turns, 7 generation attempts, 0 retries; 16,249 input and 874
  output tokens; 60.1 s for the run (61.5 s wall time with start-up); $0.

## Limits

- One run, one task, one model. Not a benchmark, not a success rate.
- The task is synthetic and small; real repositories are not covered.
- The model and its quantization were chosen for a 16 GB machine; a
  different machine or Ollama version may answer differently even at
  temperature 0.
