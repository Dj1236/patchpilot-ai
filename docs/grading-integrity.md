# Grading integrity

This page states exactly what the evaluator trusts when it grades a candidate
patch, what it checks, and what it cannot detect.

**These measures do not make arbitrary hostile code trustworthy.** Tests run
in the same process as the code they test, so code that is determined to lie
about its own test results can. V1 grades approved repositories and patches
that have passed the patch policy; the measures here stop accidental and
low-effort manipulation and make the rest visible.

## Inputs

| Input | Controlled by | Trusted | How it is handled |
|---|---|---|---|
| Sandbox image, test launcher, pytest configuration | PatchPilot | Yes | Built from a pinned definition; root-owned and read-only inside the container |
| pytest command line | PatchPilot | Yes | Fixed; only validated targets and source roots vary |
| Task definition (`task.toml`) | Maintainer | Yes | Schema-validated; never shown to an agent in full |
| Hidden tests | Maintainer | Yes | Stored outside the agent workspace; added to the tree last |
| Existing tests and runner configuration of the starting state | Maintainer, pinned by digest | Yes | Always taken from the pin, never from the candidate |
| Source files of the candidate | Patch | **No** | Copied in as data; executed only inside the sandbox |
| New test files in the candidate | Patch | **No** | Left out of grading |
| New `conftest.py`, pytest configuration, `sitecustomize.py`, `.pth` files in the candidate | Patch | **No** | Left out and recorded as tampering |

## Outputs

| Output | Produced by | Trusted | How it is handled |
|---|---|---|---|
| Exit status of the test process | Process running candidate code | Partly | Must agree with the report |
| JUnit report | Process running candidate code | Partly | Size-capped, parsed defensively, checked for internal consistency and against the exit status |
| Standard output and error | Process running candidate code | No | Bounded; never used for grading |
| Timeout, memory and process events | Host timer and the kernel's control group | Yes | Read from outside the test process |
| Container removal | Docker engine | Yes | Confirmed after every run |

## What the evaluator does

1. **Builds its own tree.** Source files come from the candidate. Every test,
   `conftest.py` and configuration file comes from the pinned starting state.
   Hidden tests are added last. A candidate cannot replace, edit, delete or
   reconfigure any test it is judged by.
2. **Records tampering.** A changed or deleted protected file, a new runner
   configuration file, or a file at a hidden test's path is listed in
   `Grade.tampered`. A tampered candidate is never `resolved`.
3. **Starts pytest from the image.** The launcher imports pytest before any
   workspace directory is importable, and workspace directories are added
   after the standard library. A `pytest.py` in the workspace is never run as
   the runner. Configuration files in the workspace are not read.
4. **Skips exit handlers.** The launcher leaves the process without running
   exit handlers registered by the code under test.
5. **Cross-checks the report.** Exit status 0 needs a report with tests and
   no failures. Exit status 1 needs a report with at least one failure. A
   missing or malformed report, a test listed twice, or totals that disagree
   with the listed tests all make the run untrustworthy.
6. **Checks test identities.** Every expected test must appear in the report
   by its exact ID. A missing or skipped test never counts as passing. When a
   task is loaded, the hidden test files are parsed (not imported) and their
   test functions must match the task definition exactly.
7. **Never rewards an unusable run.** A timeout, memory kill, sandbox error
   or untrustworthy report is never `resolved`, and is not counted as
   evidence against the patch either.

## Manipulation attempts covered by tests

`tests/integration/test_grading_integrity.py` runs each of these against the
real sandbox, with the bug still in place:

| Attempt | Result |
|---|---|
| `conftest.py` that turns every test report into a pass | Dropped, flagged, not resolved |
| `pytest.ini`, `tox.ini`, `setup.cfg` or `pyproject.toml` that deselects tests | Dropped, flagged, not resolved |
| Configuration file in an ordinary (non-evaluation) run | Ignored by the runner |
| `pytest.py` or `_pytest/` package placed in the workspace | Never loaded; hidden tests still fail |
| Weakening or deleting an existing test | Original restored, flagged, not resolved |
| Exit handler that rewrites the report and forces exit status 0 | Never runs; hidden tests still fail |
| Exiting with status 0 before any test runs | Untrustworthy: no report |
| File placed at a hidden test's path | Replaced by the real hidden test, flagged |
| Compiled `.pyc` files planted next to tests | Left out |

## What is not detected

- **In-process forgery.** Code under test can patch pytest from inside the
  test process so that every test reports success with a matching exit
  status. `test_in_process_forgery_of_report_and_exit_status_is_detected` is
  a strict expected-failure test that demonstrates this; it will start
  failing loudly if a future change closes the gap. Closing it needs the
  tests to run in a separate privilege domain from the code under test.
- **Background threads or processes** that rewrite the report while pytest
  is still running, in step with the exit status.
- **Behavior that detects it is being tested** and acts differently.
- **Standard-library names.** A project module with the same name as a
  standard-library module is not importable in the sandbox, because
  workspace directories come after the standard library.
- **Repositories that need their own pytest configuration.** It is ignored.
  A task that needs it must wait for per-task configuration in the manifest.
- **Agent-visible runs are not protected this way.** When an agent runs the
  visible tests on its own workspace, its own `conftest.py` is honored. Only
  the evaluator's run is built from pinned tests.
- **Hidden tests live in this repository.** They are kept out of agent
  workspaces, but once the repository is public they are public too, and may
  end up in a model's training data.

## What the patch policy adds later

From Milestone 5, a patch is checked before it is ever graded: it may add
tests but not touch existing tests, configuration or protected files. The
evaluator repeats that check independently, so a policy bug alone cannot
turn into a false `resolved`.
