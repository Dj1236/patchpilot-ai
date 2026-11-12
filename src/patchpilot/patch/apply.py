"""Apply a proposal to an in-memory copy of a snapshot, and diff the result.

An edit names the exact text it replaces. That text must occur exactly once
in the file as it stands when the edit is applied; zero or several matches
reject the whole proposal. Nothing is guessed, and the original mapping is
never modified, so a proposal applies completely or not at all.

The diff is computed from the before and after trees. Text the model wrote
is never copied into it as a diff.
"""

import difflib
from collections.abc import Mapping
from typing import Literal

from patchpilot.agent.proposal import PatchProposal

PatchErrorCode = Literal[
    "file_missing", "file_exists", "not_text", "target_missing", "target_ambiguous", "no_change"
]
_NO_NEWLINE = "\\ No newline at end of file\n"


class PatchError(ValueError):
    """The proposal cannot be applied. Carries a machine-readable code."""

    def __init__(self, code: PatchErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _decode(path: str, data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchError("not_text", f"{path} is not a text file") from None
    if "\0" in text:
        raise PatchError("not_text", f"{path} is not a text file")
    return text


def apply_proposal(files: Mapping[str, bytes], proposal: PatchProposal) -> dict[str, bytes]:
    """Return a new tree with the proposal applied, or raise ``PatchError``."""
    patched = dict(files)
    for number, edit in enumerate(proposal.edits, start=1):
        if edit.path not in patched:
            raise PatchError("file_missing", f"edit {number}: {edit.path} does not exist")
        text = _decode(edit.path, patched[edit.path])
        matches = text.count(edit.expected_original)
        if matches == 0:
            raise PatchError(
                "target_missing", f"edit {number}: the expected text is not in {edit.path}"
            )
        if matches > 1:
            raise PatchError(
                "target_ambiguous",
                f"edit {number}: the expected text occurs {matches} times in {edit.path}",
            )
        if edit.expected_original == edit.replacement:
            raise PatchError("no_change", f"edit {number}: the replacement changes nothing")
        patched[edit.path] = text.replace(edit.expected_original, edit.replacement).encode("utf-8")

    for test in proposal.regression_tests:
        if test.path in patched:
            raise PatchError("file_exists", f"{test.path} already exists")
        if "\0" in test.content:
            raise PatchError("not_text", f"{test.path} is not a text file")
        patched[test.path] = test.content.encode("utf-8")
    return patched


def _file_diff(path: str, before: str | None, after: str | None) -> list[str]:
    old = [] if before is None else before.splitlines(keepends=True)
    new = [] if after is None else after.splitlines(keepends=True)
    header = [f"diff --git a/{path} b/{path}\n"]
    if before is None:
        header.append("new file mode 100644\n")
    if after is None:
        header.append("deleted file mode 100644\n")
    body = difflib.unified_diff(
        old,
        new,
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile="/dev/null" if after is None else f"b/{path}",
    )
    lines: list[str] = []
    for line in body:
        if line.endswith("\n"):
            lines.append(line)
        else:
            # The last line of a file without a trailing newline.
            lines.extend((line + "\n", _NO_NEWLINE))
    return header + lines if lines else []


def unified_diff(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> str:
    """Return a git-style unified diff turning ``before`` into ``after``."""
    parts: list[str] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        parts.extend(
            _file_diff(
                path,
                None if old is None else _decode(path, old),
                None if new is None else _decode(path, new),
            )
        )
    return "".join(parts)


def changed_line_count(diff: str) -> int:
    """Count added and removed lines in a unified diff."""
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
