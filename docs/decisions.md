# PatchPilot AI: technology decision records

Each record states the decision, the reason, and what was rejected.

## 1. Python 3.12 with uv, Ruff, mypy and pre-commit

- **Decision:** Python 3.12, uv for environments and locking, Ruff for lint
  and format, mypy in strict mode, pre-commit for local checks.
- **Reason:** standard, fast tooling with a committed lock file.
- **Rejected:** pip with requirements files (no single lock), separate
  linters and formatters (more configuration).

## 2. Claude Sonnet 5.5 behind a thin interface, fake model in CI

- **Decision:** one small provider interface. The first implementation is
  `claude-sonnet-5-5`. A scripted fake model is used in all automated tests.
- **Reason:** a configurable provider without a heavy abstraction layer, and
  no paid calls in CI.
- **Rejected:** a multi-provider gateway library (more surface than V1
  needs).

## 3. Baseline in plain Python, LangGraph only for the repair loop

- **Decision:** the single-attempt baseline is plain functions. LangGraph
  wraps the same functions for the stateful repair loop.
- **Reason:** LangGraph is used for checkpointing and resume. Node logic
  stays testable without it.
- **Rejected:** LangGraph for everything (couples the baseline to the
  framework), plain Python for everything (resume would be hand-built).

## 4. Safe checkpoint serialization

- **Decision:** checkpoints hold JSON-serializable data only, with
  pickle-based serialization disabled.
- **Reason:** avoids unsafe deserialization of stored state.
- **Open point:** the exact settings are confirmed against the pinned
  LangGraph version in Milestone 6.

## 5. Search-and-replace edits, diff from git

- **Decision:** the model returns structured edits with the expected
  original text. The orchestrator applies them and derives the diff with
  `git diff`.
- **Reason:** model-written unified diffs often fail to apply. An exact,
  unique match requirement removes guessing.
- **Changed in Milestone 5:** the diff is computed with Python's `difflib`
  from the before and after trees, because the snapshot is in memory and
  there is no working tree for `git diff`. Tests confirm the result applies
  with `git apply`.
- **Rejected:** model-written diffs, whole-file rewrites (large and
  error-prone).

## 6. Literal text search and `ast` parsing for code search

- **Decision:** literal text search over the in-memory snapshot, and symbol
  outlines from Python's `ast` module. Files are parsed, never imported.
- **Reason:** approved repositories are small. The snapshot is in memory,
  so there are no files on disk for an external search tool, and literal
  search cannot be stalled by a pattern a model wrote.
- **Changed in Milestone 4:** this record first named ripgrep.
- **Rejected:** ripgrep (needs files on disk and a new binary), regular
  expressions (denial of service through backtracking), embeddings and a
  vector database (cost with no measured benefit yet).

## 7. Controlled images, hashed lock files, wheels only

- **Decision:** PatchPilot base images pinned by digest, dependencies from a
  maintainer-written lock file with hashes, prebuilt wheels only, and the
  repository run from its source tree.
- **Reason:** no repository-controlled build step ever runs.
- **Rejected:** repository Dockerfiles, `pip install` of the repository
  itself.

## 8. Docker CLI with explicit flags

- **Decision:** the sandbox invokes the Docker CLI with every restriction
  written out.
- **Reason:** each restriction is visible and testable.
- **Rejected:** a Docker SDK wrapper (hides defaults).

## 9. SQLite and one JSONL trace per run

- **Decision:** SQLite for run state, one JSONL trace file per run.
- **Reason:** enough for a single developer, with no service to operate.
- **Rejected:** a database server.

## 10. `httpx` against the GitHub REST API

- **Decision:** direct REST calls with `httpx`.
- **Reason:** V1 needs two read endpoints.
- **Rejected:** a full GitHub client library.

## 11. Fetch into a bare repository, never check out

- **Decision:** fetch one pinned commit with git into a temporary bare
  repository and read files from git objects into memory.
- **Reason:** without a working tree no hook, filter, LFS download or
  submodule update can run, and git verifies content against the commit
  hash.
- **Rejected:** a normal clone (runs checkout machinery on the host),
  GitHub's tarball download (no verification against the commit hash).

## 12. Public repositories only, token optional

- **Decision:** V1 reads public repositories. A token is optional, used
  only against `api.github.com`, and never given to git.
- **Reason:** no credential ever reaches git or a sandbox.
- **Rejected:** private repository support in V1.

## 13. One attempt, no refusal fallback, caller-owned retries

- **Decision:** the baseline explores with read-only tools and then gives
  exactly one proposal with no test feedback. A declined request is recorded
  as `model_refused`; no other model is tried. The SDK's retries are off and
  PatchPilot retries a failed request at most once, budgeting each attempt.
- **Reason:** the Milestone 6 loop should differ from the baseline in one
  thing only, results must come from the model that was asked, and no request
  may be sent without being counted.
- **Rejected:** a no-tools baseline, server-side fallback, SDK retries.

## 14. Gemini as a second provider, free tier, for the pilot

- **Decision:** the owner chose not to buy model credits during development.
  Gemini 3.8 Flash (`gemini-3.8-flash`) on the free tier is a second
  provider behind the same interface, through the official `google-genai`
  SDK and its Interactions API. The Anthropic adapter stays as an optional
  provider. Each provider has one pinned model, selected by configuration
  (`PATCHPILOT_PROVIDER`), never by fallback.
- **Reason:** a real pilot at no model cost. The agent loop, tools, patch
  policy, sandbox, traces and grading are unchanged; only the adapter
  differs.
- **How it is kept free and honest:** requests are stateless (`store` off);
  SDK retries and automatic continuation are off, because both send
  uncounted requests; Vertex AI and enterprise mode are refused; a live run
  needs the owner's confirmation that the key's project has no billing
  account, which code cannot check; the ledger still reserves at list price
  so the caps bound the exposure. Results always name the provider and the
  model, and results from different models are never pooled.
- **Accepted cost:** on the free tier Google may use prompts and responses
  to improve its products, so only synthetic or approved public code is
  sent.
- **Rejected:** a multi-provider gateway library, automatic fallback to
  another model or provider, the legacy `generateContent` API (still
  supported, but no longer the documented path), server-side conversation
  state.
