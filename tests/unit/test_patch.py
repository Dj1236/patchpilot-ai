import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from patchpilot.agent.proposal import PROPOSAL_SCHEMA, PatchProposal, ProposalError, parse_proposal
from patchpilot.patch.apply import PatchError, apply_proposal, changed_line_count, unified_diff
from patchpilot.patch.policy import PolicyLimits, check_policy

FILES: dict[str, bytes] = {
    "src/pkg/core.py": b"def total(items):\n    return None\n\n\ndef other():\n    return None\n",
    "src/pkg/util.py": b"VALUE = 1",  # no trailing newline
    "tests/test_core.py": b"def test_other():\n    assert True\n",
    "tests/conftest.py": b"# fixtures\n",
    "pyproject.toml": b"[project]\n",
}


def proposal(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "Return 0 for an empty list.",
        "root_cause": "total() returned None.",
        "relevant_files": ["src/pkg/core.py"],
        "edits": [
            {
                "path": "src/pkg/core.py",
                "expected_original": "def total(items):\n    return None\n",
                "replacement": "def total(items):\n    return sum(items)\n",
            }
        ],
        "regression_tests": [
            {"path": "tests/test_total.py", "content": "def test_total():\n    assert True\n"}
        ],
    }
    return {**base, **changes}


def parsed(**changes: Any) -> PatchProposal:
    return parse_proposal(json.dumps(proposal(**changes)))


def edit(path: str, old: str, new: str) -> dict[str, str]:
    return {"path": path, "expected_original": old, "replacement": new}


# -- proposal format -------------------------------------------------------------


def test_a_valid_proposal_parses() -> None:
    result = parsed()

    assert result.edits[0].path == "src/pkg/core.py"
    assert result.regression_tests[0].path == "tests/test_total.py"


def test_the_schema_given_to_the_model_matches_the_validated_fields() -> None:
    assert set(PROPOSAL_SCHEMA["properties"]) == set(PatchProposal.model_fields)
    assert PROPOSAL_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "Here is my patch: {}",
        "[]",
        '{"summary": "x"}',
        json.dumps(proposal(edits=[])),
        json.dumps(proposal(regression_tests=[])),
        json.dumps(proposal(extra_field=1)),
        json.dumps(proposal(edits=[edit("../outside.py", "a", "b")])),
        json.dumps(proposal(edits=[edit("/etc/passwd", "a", "b")])),
        json.dumps(proposal(edits=[edit("src/pkg/core.py", "", "b")])),
        json.dumps(proposal(edits=[edit("src/pkg/core.py", "a", "b")] * 21)),
        json.dumps(proposal(regression_tests=[{"path": "tests/../x.py", "content": "x"}])),
        json.dumps(proposal(summary="x" * 5000)),
        "[" * 100_000,
        json.dumps(proposal(root_cause="x" * 300_000)),
    ],
)
def test_unusable_answers_are_rejected(text: str) -> None:
    with pytest.raises(ProposalError):
        parse_proposal(text)


def test_a_rejection_never_echoes_the_model_output() -> None:
    with pytest.raises(ProposalError) as caught:
        parse_proposal(json.dumps(proposal(edits=[edit("../SECRET-MARKER", "a", "b")])))

    assert "SECRET-MARKER" not in str(caught.value)


# -- applying edits ---------------------------------------------------------------


def test_edits_and_new_tests_are_applied_to_a_copy() -> None:
    patched = apply_proposal(FILES, parsed())

    assert b"return sum(items)" in patched["src/pkg/core.py"]
    assert b"def other():\n    return None" in patched["src/pkg/core.py"]
    assert patched["tests/test_total.py"].startswith(b"def test_total")
    assert b"return sum(items)" not in FILES["src/pkg/core.py"]
    assert "tests/test_total.py" not in FILES


@pytest.mark.parametrize(
    "edits,code",
    [
        ([edit("src/pkg/missing.py", "a", "b")], "file_missing"),
        ([edit("src/pkg/core.py", "return 42", "x")], "target_missing"),
        ([edit("src/pkg/core.py", "    return None\n", "    return 0\n")], "target_ambiguous"),
        ([edit("src/pkg/core.py", "def total", "def total")], "no_change"),
        ([edit("src/pkg/core.py", "DEF TOTAL", "x")], "target_missing"),
    ],
)
def test_missing_ambiguous_and_empty_edits_reject_the_proposal(
    edits: list[dict[str, str]], code: str
) -> None:
    with pytest.raises(PatchError) as caught:
        apply_proposal(FILES, parsed(edits=edits))

    assert caught.value.code == code


def test_one_bad_edit_means_nothing_is_applied() -> None:
    good = edit(
        "src/pkg/core.py", "def total(items):\n    return None\n", "def total(i):\n    return 0\n"
    )
    bad = edit("src/pkg/util.py", "NOT THERE", "x")
    before = dict(FILES)

    with pytest.raises(PatchError):
        apply_proposal(FILES, parsed(edits=[good, bad]))

    assert before == FILES


def test_a_later_edit_sees_the_result_of_an_earlier_one() -> None:
    edits = [
        edit("src/pkg/util.py", "VALUE = 1", "VALUE = 2\nEXTRA = 3\n"),
        edit("src/pkg/util.py", "EXTRA = 3", "EXTRA = 4"),
    ]

    assert (
        apply_proposal(FILES, parsed(edits=edits))["src/pkg/util.py"] == b"VALUE = 2\nEXTRA = 4\n"
    )


def test_a_new_test_cannot_replace_an_existing_file_and_binary_files_are_refused() -> None:
    existing = [{"path": "tests/test_core.py", "content": "def test_x():\n    pass\n"}]
    with pytest.raises(PatchError) as caught:
        apply_proposal(FILES, parsed(regression_tests=existing))
    assert caught.value.code == "file_exists"

    with pytest.raises(PatchError) as binary:
        apply_proposal({**FILES, "src/pkg/core.py": b"\xff\x00"}, parsed())
    assert binary.value.code == "not_text"


# -- diffs --------------------------------------------------------------------------


def test_the_diff_comes_from_the_trees_and_counts_changed_lines() -> None:
    diff = unified_diff(FILES, apply_proposal(FILES, parsed()))

    assert "diff --git a/src/pkg/core.py b/src/pkg/core.py" in diff
    assert "-    return None" in diff and "+    return sum(items)" in diff
    assert "new file mode 100644" in diff and "+++ b/tests/test_total.py" in diff
    assert changed_line_count(diff) == 4
    assert unified_diff(FILES, FILES) == ""


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize(
    "edits",
    [
        [
            edit(
                "src/pkg/core.py",
                "def total(items):\n    return None\n",
                "def total(items):\n    return sum(items)\n",
            )
        ],
        [edit("src/pkg/util.py", "VALUE = 1", "VALUE = 2")],
        [edit("src/pkg/util.py", "VALUE = 1", "VALUE = 2\n")],
        [edit("src/pkg/core.py", "\n\ndef other():\n    return None\n", "")],
        [
            edit("src/pkg/core.py", "def total(items):", "def total(items, start=0):"),
            edit("src/pkg/util.py", "VALUE = 1", "VALUE = 1\nNAME = 'x'"),
        ],
    ],
)
def test_generated_diffs_apply_with_git_and_reproduce_the_patched_tree(
    tmp_path: Path, edits: list[dict[str, str]]
) -> None:
    patched = apply_proposal(FILES, parsed(edits=edits))
    diff = unified_diff(FILES, patched)
    for path, data in FILES.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (tmp_path / "change.patch").write_text(diff)
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    for arguments in (["--check"], []):
        subprocess.run(  # noqa: S603
            ["git", "apply", *arguments, "change.patch"],  # noqa: S607
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env=env,
        )

    for path, data in patched.items():
        assert (tmp_path / path).read_bytes() == data, path


# -- policy ---------------------------------------------------------------------------


def violations(**changes: Any) -> tuple[str, ...]:
    candidate = parsed(**changes)
    return check_policy(FILES, candidate, apply_proposal(FILES, candidate))


def test_a_source_fix_with_a_new_test_breaks_no_rule() -> None:
    assert violations() == ()


@pytest.mark.parametrize(
    "path,old",
    [
        ("tests/test_core.py", "assert True"),
        ("tests/conftest.py", "# fixtures"),
        ("pyproject.toml", "[project]"),
    ],
)
def test_existing_tests_and_configuration_may_not_be_edited(path: str, old: str) -> None:
    found = violations(edits=[edit(path, old, "# weakened")])

    assert len(found) == 1 and path in found[0]


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "src/sitecustomize.py",
        "src/pkg/helper.py",
        "tests/helper.py",
        "tests/data.json",
        "notes.md",
        ".github/workflows/ci.yml",
    ],
)
def test_only_new_test_files_may_be_added(path: str) -> None:
    found = violations(regression_tests=[{"path": path, "content": "x = 1\n"}])

    assert found and path in found[0]


def test_size_limits_apply() -> None:
    big = "\n".join(f"line_{index} = {index}" for index in range(400)) + "\n"
    candidate = parsed(edits=[edit("src/pkg/util.py", "VALUE = 1", big)])
    patched = apply_proposal(FILES, candidate)

    assert any("lines changed" in item for item in check_policy(FILES, candidate, patched))
    tight = PolicyLimits(max_files_changed=1)
    assert any("files changed" in item for item in check_policy(FILES, candidate, patched, tight))
