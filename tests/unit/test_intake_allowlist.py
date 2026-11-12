from pathlib import Path

import pytest
from pydantic import ValidationError

from patchpilot.intake.allowlist import (
    Allowlist,
    AllowlistError,
    ApprovedRepository,
    RepositoryNotApprovedError,
    load_allowlist,
)
from patchpilot.intake.urls import parse_issue_url

SHA = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
VALID = f"""
schema_version = 1

[[repositories]]
owner = "OctoCat"
name = "Hello-World"
repository_id = 1296269
commit_sha = "{SHA}"

[repositories.environment]
source_roots = ["src"]

[[repositories.commands]]
name = "tests"
targets = ["tests"]

[[repositories]]
owner = "example"
name = "other"
repository_id = 42
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "approved.toml"
    path.write_text(text)
    return path


def test_loads_entries_and_lower_cases_names(tmp_path: Path) -> None:
    allowlist = load_allowlist(_write(tmp_path, VALID))

    first, second = allowlist.repositories
    assert first.slug == "octocat/hello-world"
    assert first.url == "https://github.com/octocat/hello-world"
    assert first.repository_id == 1296269
    assert first.commit_sha == SHA
    assert first.environment.source_roots == ("src",)
    assert first.commands[0].targets == ("tests",)
    assert second.commit_sha is None


def test_lookup_ignores_case_and_refuses_unknown_repositories(tmp_path: Path) -> None:
    allowlist = load_allowlist(_write(tmp_path, VALID))
    approved = parse_issue_url("https://github.com/OCTOCAT/hello-WORLD/issues/3")
    unknown = parse_issue_url("https://github.com/octocat/Spoon-Knife/issues/3")

    assert allowlist.require(approved).repository_id == 1296269
    with pytest.raises(RepositoryNotApprovedError, match="octocat/spoon-knife"):
        allowlist.require(unknown)


def test_no_configured_file_approves_nothing() -> None:
    allowlist = load_allowlist(None)

    assert allowlist.repositories == ()
    with pytest.raises(RepositoryNotApprovedError):
        allowlist.require(parse_issue_url("https://github.com/octocat/Hello-World/issues/1"))


def test_similar_names_are_not_approved(tmp_path: Path) -> None:
    allowlist = load_allowlist(_write(tmp_path, VALID))

    for owner, name in [
        ("octocat", "hello-world2"),
        ("octocat", "hello"),
        ("octocat2", "hello-world"),
        ("octo", "cat/hello-world"),
        ("example", "other.git"),
    ]:
        assert allowlist.find(owner, name) is None


def test_a_configured_but_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="not a regular file"):
        load_allowlist(tmp_path / "missing.toml")


def test_a_symlinked_allowlist_is_refused(tmp_path: Path) -> None:
    real = _write(tmp_path, VALID)
    link = tmp_path / "link.toml"
    link.symlink_to(real)

    with pytest.raises(AllowlistError, match="not a regular file"):
        load_allowlist(link)


def test_invalid_toml_and_oversized_files_are_errors(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="not valid TOML"):
        load_allowlist(_write(tmp_path, "repositories = ["))
    with pytest.raises(AllowlistError, match="too large"):
        load_allowlist(_write(tmp_path, "# padding\n" * 40_000))


@pytest.mark.parametrize(
    "entry",
    [
        {"owner": "../evil", "name": "repo", "repository_id": 1},
        {"owner": "octocat", "name": "re po", "repository_id": 1},
        {"owner": "octocat", "name": "repo.git", "repository_id": 1},
        {"owner": "octocat", "name": "..", "repository_id": 1},
        {"owner": "octocat", "name": "repo", "repository_id": 0},
        {"owner": "octocat", "name": "repo"},
        {"owner": "octocat", "name": "repo", "repository_id": 1, "commit_sha": "main"},
        {"owner": "octocat", "name": "repo", "repository_id": 1, "commit_sha": "abc1234"},
        {"owner": "octocat", "name": "repo", "repository_id": 1, "url": "https://evil.example"},
        {"owner": "octocat", "name": "repo", "repository_id": 1, "token": "x"},
    ],
)
def test_invalid_entries_are_rejected(entry: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ApprovedRepository.model_validate(entry)


def test_duplicate_entries_are_rejected() -> None:
    entry = {"owner": "octocat", "name": "repo", "repository_id": 1}

    with pytest.raises(ValidationError, match="more than once"):
        Allowlist.model_validate({"repositories": [entry, {**entry, "owner": "OctoCat"}]})
    with pytest.raises(ValidationError, match="ID is listed more than once"):
        Allowlist.model_validate({"repositories": [entry, {**entry, "name": "other"}]})


def test_unknown_schema_versions_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="not a valid allowlist"):
        load_allowlist(_write(tmp_path, "schema_version = 2\n"))
