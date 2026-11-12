"""Validation and normalization of GitHub issue URLs.

An issue URL arrives from outside and is the first thing PatchPilot acts on,
so it is matched against one strict pattern instead of being parsed leniently.
Only the owner, repository and issue number are kept; everything PatchPilot
later sends to GitHub or git is rebuilt from those validated values.
"""

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints

from patchpilot.sandbox.models import FrozenModel

_MAX_URL_LENGTH = 300

# GitHub's own naming rules. Owners: letters, digits and single hyphens, not
# at either end, at most 39 characters. Repositories: letters, digits, '.',
# '_' and '-', at most 100 characters.
OWNER_PATTERN = r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}"
REPOSITORY_PATTERN = r"[A-Za-z0-9._-]{1,100}"

OwnerName = Annotated[str, StringConstraints(pattern=rf"^{OWNER_PATTERN}$")]
RepositoryName = Annotated[str, StringConstraints(pattern=rf"^{REPOSITORY_PATTERN}$")]

_ISSUE_URL = re.compile(
    rf"(?i:https)://(?i:(?:www\.)?github\.com)"
    rf"/(?P<owner>{OWNER_PATTERN})"
    rf"/(?P<repository>{REPOSITORY_PATTERN})"
    r"/issues/(?P<number>[1-9][0-9]{0,9})"
    r"/?"
    r"(?:\?[A-Za-z0-9._~&=+-]*)?"
    r"(?:#[A-Za-z0-9._~&=+-]*)?",
    re.ASCII,
)
_ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})


class IssueUrlError(ValueError):
    """The text is not an acceptable GitHub issue URL."""


def check_repository_name(name: str) -> str:
    """Return ``name`` if GitHub could have a repository of that name."""
    if not re.fullmatch(REPOSITORY_PATTERN, name, re.ASCII):
        raise IssueUrlError("the repository name contains unsupported characters")
    if name in (".", "..") or name.lower().endswith(".git"):
        raise IssueUrlError("the repository name is reserved")
    return name


class IssueRef(FrozenModel):
    """A GitHub issue identified by validated, lower-cased names."""

    owner: OwnerName
    repository: RepositoryName
    number: int = Field(ge=1, le=9_999_999_999)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}/issues/{self.number}"


def parse_issue_url(text: str) -> IssueRef:
    """Parse ``text`` into an :class:`IssueRef` or raise :class:`IssueUrlError`.

    Accepted: ``https://github.com/<owner>/<repository>/issues/<number>``,
    optionally with ``www.``, a trailing slash, a query or a fragment. Owner
    and repository are lower-cased, because GitHub treats them
    case-insensitively and the allowlist must compare one spelling.
    """
    if not isinstance(text, str):
        raise IssueUrlError("the issue URL must be text")
    if len(text) > _MAX_URL_LENGTH:
        raise IssueUrlError("the issue URL is too long")
    if not text.isascii() or not text.isprintable() or " " in text:
        raise IssueUrlError("the issue URL contains whitespace or non-ASCII characters")

    match = _ISSUE_URL.fullmatch(text)
    if match is None:
        raise IssueUrlError(
            "expected a URL of the form https://github.com/<owner>/<repository>/issues/<number>"
        )

    # Second opinion from the standard URL parser: the strict pattern and the
    # parser must agree on which host this is and that no credentials or port
    # are present.
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError as exc:
        raise IssueUrlError("the issue URL has an invalid port") from exc
    if (
        parts.scheme.lower() != "https"
        or parts.hostname not in _ALLOWED_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port is not None
    ):
        raise IssueUrlError("the issue URL does not point at github.com over HTTPS")

    repository = check_repository_name(match["repository"])
    return IssueRef(
        owner=match["owner"].lower(),
        repository=repository.lower(),
        number=int(match["number"]),
    )
