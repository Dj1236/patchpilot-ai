# Issue intake and repository tools

Intake turns a GitHub issue URL into a bug report and a pinned, read-only
snapshot of an approved repository, with tools to list, read and search it.
It makes no model call, runs no repository code and writes nothing to GitHub.

```
issue URL -> validate -> allowlist check -> GitHub API (GET only) -> bug report
                                                 |
                                 pin commit -> git fetch (bare, no checkout)
                                                 |
                       snapshot {path: bytes} + commit SHA + content digest
                                                 |
                     list / read / search / outline, with budgets
```

Try it:

```
uv run patchpilot intake https://github.com/<owner>/<repo>/issues/<n> --allowlist approved.toml
```

## Order of checks

1. The URL must match `https://github.com/<owner>/<repository>/issues/<number>`
   exactly. Only the owner, repository and number are kept.
2. The repository must be on the allowlist. **Until this point nothing is
   sent over the network.**
3. The repository's numeric ID, visibility and size are checked against the
   allowlist entry.
4. The issue is read. Pull requests, which GitHub's Issues API also returns,
   are refused as `not_an_issue`.
5. A commit is pinned: the allowlisted SHA, or the default branch resolved
   once to a full SHA.
6. That commit is fetched and snapshotted.

## Allowlist

```toml
schema_version = 1

[[repositories]]
owner = "octocat"
name = "Hello-World"
repository_id = 1296269          # GitHub's numeric ID; a name can change hands, the ID cannot
commit_sha = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"   # optional pin
max_size_kb = 51200              # optional

[repositories.environment]
source_roots = ["src"]

[[repositories.commands]]
name = "tests"
targets = ["tests"]
```

Set its path with `--allowlist` or `PATCHPILOT_ALLOWLIST_PATH`. With no file
configured, nothing is approved. A configured file that cannot be read is an
error, not an empty list.

## GitHub access

- Public repositories only. No token is needed.
- `PATCHPILOT_GITHUB_TOKEN` is optional and only raises rate limits. It is
  sent to `api.github.com` only, never to git and never into a sandbox, and
  never appears in errors or logs.
- A classic token that reports write scopes is refused. A fine-grained token
  does not report its permissions, so that check cannot cover it: create it
  with read-only access to public repositories.
- Only GET requests are sent. Redirects are not followed. Proxy and netrc
  settings from the environment are ignored. Responses are size-capped.

## Fetching

git is used to fetch exactly one commit into a temporary bare repository. No
working tree is created, so no hook, checkout filter, LFS download or
submodule update can run, and no repository file is written to the host by
name.

- The remote URL is built from the allowlist entry, never from input text.
- git gets a fresh environment and explicit configuration: system and user
  configuration, credential helpers, prompts and proxies are all off, and
  only HTTPS is allowed.
- **Pinned commit.** The commit is requested by its full SHA. If the server
  refuses that, the default branch is fetched to a bounded depth instead.
  Either way the pinned object must then be present and be a commit; if not,
  the fetch fails with `commit_unavailable`. The tip of a fetched branch is
  never used in its place.

### Storage limits, and what they are not

While a fetch runs, two mechanisms bound its temporary storage:

- the operating system's per-file size limit on the git process, which caps
  the pack file git writes;
- a watchdog that measures the temporary directory and kills git's process
  group when it grows past the limit.

**Neither is an absolute disk quota.** The per-file limit caps each file, not
their sum. The watchdog polls every 50 milliseconds, so the directory can
overshoot by whatever git writes within one interval. On a fast local disk a
small fetch can finish between two polls, in which case the per-file limit
and a final size check are what catch it. Both mechanisms were verified
separately on macOS; neither has run on Linux CI.

A wall-clock timeout, a stalled-transfer timeout, bounded output capture and
removal of the temporary directory apply as well.

## Snapshot

Sizes and types are read from the commit's file listing before any content
is loaded. Left out and reported in `skipped`:

| Entry | Reason |
|---|---|
| Symbolic link | Never followed or resolved |
| Submodule pointer | Never fetched |
| File over 1 MB | Not loaded |
| Name with characters outside letters, digits, `_`, `.`, `-` | Unsupported |

A commit with more than 2000 files or 16 MB in total is refused whole. LFS
pointer files are kept as the small text files they are.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_files` | `prefix`, `limit` | Paths and sizes |
| `read_file` | `path`, `start_line`, `max_lines` | A line range |
| `search_code` | `query`, `prefix`, `case_sensitive`, `max_matches` | Matching lines |
| `outline` | `path` | Classes, functions and methods of a Python file |

- The tools read the in-memory snapshot. They never touch the host
  filesystem, so a path can only select a file of the snapshot or nothing.
- Every call goes through one registry that validates the tool name and
  arguments. Errors never echo the request.
- Search is literal text, not regular expressions, so a pattern cannot make
  it run for a long time.
- `outline` parses Python; it never imports or runs it.
- Each run has a budget of calls and output bytes, and each call an output
  cap. Results say when they were cut. A used-up budget ends the run.

## Untrusted content

Issue titles, issue bodies and file contents are data from outside.

- They are wrapped in `UntrustedText`. Formatting one into a string or a log
  line gives a placeholder; code must call `reveal()` to get the content, and
  a test limits which modules do.
- Each issue text is kept twice: a bounded copy of the original for
  provenance, and a cleaned presentation form for display. The record holds
  the hash and length of the complete original and, separately, the hash of
  what was captured, plus whether the capture is complete.
- Nothing from an issue or a file is used to build a command, a path, a URL
  or a decision about what is approved.

**Cleaning does not prevent prompt injection.** Removing control characters,
invisible characters and HTML comments makes text readable and bounded for a
person. Text that survives can still contain instructions aimed at a language
model. The defense is structural: no shell, a fixed set of validated tools,
the allowlist, path and budget checks in code, and approval gates. Those are
in place now, before any model is connected, and are tested with injection
text that must change nothing.

## Unresolved limitations

- **git runs on the host** to fetch. It parses data from the remote, so a
  vulnerability in git itself is exposure. Only approved repositories on
  github.com are fetched. Running the fetch in its own container is future
  work.
- **Storage limits are not a quota,** as described above.
- **Fine-grained tokens cannot be checked** for write access.
- **File names are restricted,** so files with spaces or non-ASCII names are
  left out of snapshots.
- **Issue comments are not read.** Title and body only.
- **Private repositories are not supported.**
- **No proxy support,** because environment proxy settings are ignored.
- **Agent prompts do not exist yet.** The boundaries are built and tested,
  but their use with a model is Milestone 5 work.
- **Not run on Linux CI.** See [ci-status.md](ci-status.md).
