"""Build the tree that hidden tests are graded on.

A candidate patch controls source code. It must not control how that code is
judged. The evaluation tree is therefore assembled from three sources:

- source files: taken from the candidate;
- tests and runner configuration: always taken from the pinned starting
  state, whatever the candidate did to them;
- hidden tests: added last, from outside the candidate.

Anything in the candidate that tries to change the judging is recorded as
tampering, and a tampered candidate is never graded as resolved. This mirrors
the V1 patch rule that existing tests and test configuration are off limits.
"""

from collections.abc import Mapping
from dataclasses import dataclass

# Files that change how Python or pytest runs, wherever they are placed.
RUNNER_CONFIG_NAMES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "tox.ini",
        "setup.cfg",
        "setup.py",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
TEST_DIRECTORY_NAMES = frozenset({"tests", "test", "testing"})


def is_runner_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in RUNNER_CONFIG_NAMES or name.endswith(".pth")


def is_test_path(path: str) -> bool:
    *directories, name = path.split("/")
    return (
        any(part in TEST_DIRECTORY_NAMES for part in directories)
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def is_protected(path: str) -> bool:
    """Return True for files a V1 patch may not modify or delete."""
    return is_runner_config(path) or is_test_path(path) or path.startswith(".github/")


@dataclass(frozen=True)
class EvaluationTree:
    """Files to grade, and what was overridden to build them."""

    files: dict[str, bytes]
    tampered: tuple[str, ...]
    ignored: tuple[str, ...]


def build_evaluation_tree(
    original: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
    hidden_tests: Mapping[str, bytes],
) -> EvaluationTree:
    """Combine a candidate's source changes with pinned tests and hidden tests.

    ``tampered`` lists candidate paths that tried to affect grading: a changed
    or deleted protected file, a new runner configuration file, or a file at
    the path of a hidden test. ``ignored`` lists new test files, which are
    harmless but are not needed for grading and are left out.
    """
    files: dict[str, bytes] = {}
    tampered: list[str] = []
    ignored: list[str] = []

    for path, content in original.items():
        if is_protected(path):
            files[path] = content
            if candidate.get(path) != content:
                tampered.append(path)
        elif path in candidate:
            files[path] = candidate[path]

    for path, content in candidate.items():
        if path in original:
            continue
        if path in hidden_tests or is_runner_config(path):
            tampered.append(path)
        elif is_protected(path):
            ignored.append(path)
        else:
            files[path] = content

    for path, content in hidden_tests.items():
        files[path] = content

    return EvaluationTree(
        files=files, tampered=tuple(sorted(tampered)), ignored=tuple(sorted(ignored))
    )
