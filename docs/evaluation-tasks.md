# Evaluation tasks

An evaluation task is one controlled bug: a starting repository state, an
issue report, and the hidden tests that decide whether a patch fixed it.

## Layout

```
evals/tasks/<task-id>/
    task.toml            full task definition (internal)
    repo/                agent-visible starting state, with passing tests
    internal/            never copied into an agent workspace
        hidden_tests/    regression tests, laid out as they overlay the repo
        reference_fix/   corrected files, laid out as they overlay the repo
```

## What an agent may see

`Task.agent_view()` returns an `AgentTask`, which has fields only for the
task ID, the repository reference, the issue, the source roots, the allowed
test commands and the budgets. It has no field for evaluation data, so that
data cannot be passed along by mistake. `LoadedTask.agent_files()` returns
only the contents of `repo/`.

Hidden tests and the reference fix are reachable only through evaluator
functions, which run after an agent has finished and feed nothing back.

## `task.toml`

| Field | Meaning |
|---|---|
| `schema_version` | Always `1` |
| `task_id` | Unique ID; must equal the directory name |
| `kind` | `injected` (bug we introduced) or `real` (bug from a public project) |
| `repository` | `fixture` (a path plus a SHA-256 content pin) or `git` (a GitHub URL plus a full 40-character commit SHA) |
| `issue` | Title, body, expected behavior, actual behavior, optional reproduction |
| `environment.source_roots` | Directories added to the import path |
| `commands` | Allowed, agent-visible test commands: a name and pytest targets only |
| `budgets` | Time, memory, CPU and process limits for every run of this task |
| `evaluation.hidden_tests_dir`, `evaluation.reference_fix_dir` | Locations inside `internal/` |
| `evaluation.command` | The pytest targets the evaluator runs |
| `evaluation.fail_to_pass` | Tests that fail on the starting state and must pass after a fix |
| `evaluation.pass_to_pass` | Tests that pass on the starting state and must keep passing |

Fixtures live inside this repository, so they are pinned by a digest of
their files instead of a commit SHA. The `git` form is validated by the
schema, but fetching it arrives with Milestone 4.

## Expected behavior of a valid task

`verify_fixture` checks all of this in the sandbox:

1. The visible commands pass on the starting state.
2. On the starting state, every fail-to-pass test fails.
3. On the starting state, every pass-to-pass test passes.
4. The starting state is not graded as resolved.
5. With the reference fix applied, the task is graded as resolved.

## Grading

A candidate tree is **resolved** when every fail-to-pass test passes and
every pass-to-pass test still passes. Beyond that:

- A listed test that is missing or skipped does not count as passing, so
  deleting a test cannot resolve a task.
- A candidate that contains a file at a hidden test's path is rejected.
- A run that timed out, ran out of memory or hit a sandbox error is marked
  untrustworthy. It is never resolved, and it is not evidence against the
  patch either.

Tests are identified by pytest node ID, rebuilt from the JUnit report. That
mapping is exact only for module-level test functions, so V1 tasks do not
put listed tests inside classes.

## Current tasks

| Task | Project | Bug | Visible tests | Hidden tests |
|---|---|---|---|---|
| `inj-001-cachekit` | LRU cache with time-to-live | Rewriting an existing key does not mark it as recently used, so a frequently updated entry is evicted | 13 pass | 3 fail-to-pass, 1 pass-to-pass |
| `inj-002-exprcalc` | Arithmetic expression evaluator | Subtraction and division are applied right to left, so `10 - 4 - 3` gives 9 | 24 pass | 6 fail-to-pass, 2 pass-to-pass |

## Adding a task

1. Create the layout above. Use only the standard library plus pytest.
2. Write `task.toml`. Compute the pin with
   `patchpilot.sandbox.tree.tree_digest(collect_tree(Path("repo")))`.
3. Add the task ID to the lists in `tests/unit/test_tasks.py` and
   `tests/integration/test_fixture_tasks.py`.
4. Run `uv run pytest`. The task is admitted only when `verify_fixture`
   passes.

Pre-commit hooks and Ruff are configured to leave `repo/` and `internal/`
untouched, because reformatting a fixture would change its pin.
