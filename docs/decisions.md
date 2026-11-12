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
- **Rejected:** model-written diffs, whole-file rewrites (large and
  error-prone).

## 6. ripgrep and `ast` parsing for code search

- **Decision:** text search with ripgrep and symbol outlines from Python's
  `ast` module. Files are parsed, never imported.
- **Reason:** approved repositories are small.
- **Rejected:** embeddings and a vector database (cost with no measured
  benefit yet).

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
