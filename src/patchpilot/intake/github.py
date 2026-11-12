"""Minimal read-only client for the GitHub REST API.

The client only ever sends GET requests to ``api.github.com``, with paths
built from an allowlist entry. It does not follow redirects, ignores proxy
and netrc settings from the environment, bounds every response, and works
without a token. A token, when given, is sent to ``api.github.com`` only and
never appears in an error message.
"""

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from patchpilot.intake.allowlist import ApprovedRepository
from patchpilot.intake.text import CapturedText, capture
from patchpilot.intake.urls import IssueRef
from patchpilot.sandbox.models import FrozenModel

API_HOST = "api.github.com"
API_BASE = f"https://{API_HOST}"
MAX_RESPONSE_BYTES = 1_048_576
MAX_TITLE_CHARS = 512
MAX_BODY_CHARS = 65_536

_USER_AGENT = "patchpilot-ai"
_RETRY_STATUSES = frozenset({502, 503, 504})
_MAX_ATTEMPTS = 3
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


class IntakeStatus(StrEnum):
    """Why an intake request was refused or failed."""

    NOT_FOUND = "not_found"
    NOT_AN_ISSUE = "not_an_issue"
    REPOSITORY_MISMATCH = "repository_mismatch"
    REPOSITORY_NOT_PUBLIC = "repository_not_public"
    REPOSITORY_TOO_LARGE = "repository_too_large"
    RATE_LIMITED = "rate_limited"
    RESPONSE_TOO_LARGE = "response_too_large"
    REDIRECT_REFUSED = "redirect_refused"
    UNEXPECTED_RESPONSE = "unexpected_response"
    NETWORK_ERROR = "network_error"
    TOKEN_REJECTED = "token_rejected"  # noqa: S105 - a status name, not a credential
    TOKEN_HAS_WRITE_ACCESS = "token_has_write_access"  # noqa: S105


class GitHubError(Exception):
    """A GitHub request was refused or failed. Carries a machine-readable status."""

    def __init__(self, status: IntakeStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class RepositoryInfo(FrozenModel):
    repository_id: int
    default_branch: str
    size_kb: int
    archived: bool


class BugReport(FrozenModel):
    """A GitHub issue reduced to what PatchPilot uses. All text is untrusted."""

    source: Literal["github_issue"] = "github_issue"
    issue: IssueRef
    repository_id: int
    state: Literal["open", "closed"]
    title: CapturedText
    body: CapturedText
    fetched_at: datetime


def is_safe_branch_name(name: object) -> bool:
    """Return True for a branch name that is safe to pass to git as a ref."""
    return (
        isinstance(name, str)
        and _BRANCH_PATTERN.fullmatch(name) is not None
        and ".." not in name
        and "//" not in name
        and not name.endswith(("/", ".", ".lock"))
    )


def _write_scopes(header: str) -> list[str]:
    """Return the scopes of a classic token that allow more than reading."""
    scopes = [scope.strip() for scope in header.split(",") if scope.strip()]
    return sorted(
        scope for scope in scopes if not (scope.startswith("read:") or scope == "user:email")
    )


class GitHubClient:
    """Read-only access to the few GitHub endpoints PatchPilot needs."""

    def __init__(
        self,
        *,
        token: SecretStr | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._sleep = sleep
        self.request_count = 0
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
            # Never pick up proxies, certificates or netrc credentials from the
            # environment.
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    # -- transport ---------------------------------------------------------

    def _send(self, path: str) -> tuple[int, httpx.Headers, bytes]:
        headers = {}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        request = self._client.build_request("GET", path, headers=headers)
        if request.url.scheme != "https" or request.url.host != API_HOST:
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE,
                "refusing to send a request outside the GitHub API",
            )
        self.request_count += 1
        try:
            response = self._client.send(request, stream=True)
            try:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise GitHubError(
                            IntakeStatus.RESPONSE_TOO_LARGE,
                            f"the response to {path} exceeds {MAX_RESPONSE_BYTES} bytes",
                        )
            finally:
                response.close()
        except httpx.HTTPError as exc:
            # Only the error type is reported: messages may echo request details.
            raise GitHubError(
                IntakeStatus.NETWORK_ERROR, f"request to {path} failed: {type(exc).__name__}"
            ) from None
        return response.status_code, response.headers, bytes(body)

    def _check_token_scopes(self, headers: httpx.Headers) -> None:
        if self._token is None or "x-oauth-scopes" not in headers:
            return
        excessive = _write_scopes(headers["x-oauth-scopes"])
        if excessive:
            raise GitHubError(
                IntakeStatus.TOKEN_HAS_WRITE_ACCESS,
                f"the configured token has more than read access (scopes: {', '.join(excessive)}); "
                "use a read-only token or none",
            )

    def _get_json(self, path: str) -> dict[str, Any]:
        status, headers, body = 0, httpx.Headers(), b""
        for attempt in range(_MAX_ATTEMPTS):
            status, headers, body = self._send(path)
            if status not in _RETRY_STATUSES:
                break
            if attempt + 1 < _MAX_ATTEMPTS:
                self._sleep(0.5 * (attempt + 1))
        self._check_token_scopes(headers)

        if status in (301, 302, 303, 307, 308):
            raise GitHubError(
                IntakeStatus.REDIRECT_REFUSED,
                f"{path} redirects elsewhere (the repository may have moved); not followed",
            )
        if status == 401:
            raise GitHubError(IntakeStatus.TOKEN_REJECTED, "GitHub rejected the configured token")
        if status in (403, 429):
            if headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers:
                raise GitHubError(IntakeStatus.RATE_LIMITED, "the GitHub API rate limit is used up")
            raise GitHubError(IntakeStatus.NOT_FOUND, f"access to {path} is forbidden")
        if status in (404, 410, 451):
            raise GitHubError(IntakeStatus.NOT_FOUND, f"{path} does not exist")
        if status != 200:
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, f"{path} returned HTTP status {status}"
            )
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, f"{path} did not return valid JSON"
            ) from None
        if not isinstance(data, dict):
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, f"{path} did not return a JSON object"
            )
        return data

    # -- endpoints ---------------------------------------------------------

    def get_repository(self, repository: ApprovedRepository) -> RepositoryInfo:
        """Fetch repository facts and check them against the allowlist entry."""
        path = f"/repos/{repository.owner}/{repository.name}"
        data = self._get_json(path)
        if data.get("id") != repository.repository_id or isinstance(data.get("id"), bool):
            raise GitHubError(
                IntakeStatus.REPOSITORY_MISMATCH,
                f"{repository.slug} is not the repository that was approved (its ID differs)",
            )
        if data.get("private") is not False or data.get("visibility", "public") != "public":
            raise GitHubError(
                IntakeStatus.REPOSITORY_NOT_PUBLIC, f"{repository.slug} is not a public repository"
            )
        size = data.get("size")
        branch = data.get("default_branch")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise GitHubError(IntakeStatus.UNEXPECTED_RESPONSE, "the repository size is missing")
        if not is_safe_branch_name(branch):
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, "the default branch name is missing or unsafe"
            )
        if size > repository.max_size_kb:
            raise GitHubError(
                IntakeStatus.REPOSITORY_TOO_LARGE,
                f"{repository.slug} is {size} KB, above its limit of {repository.max_size_kb} KB",
            )
        assert isinstance(branch, str)  # noqa: S101 - narrowed by is_safe_branch_name
        return RepositoryInfo(
            repository_id=repository.repository_id,
            default_branch=branch,
            size_kb=size,
            archived=data.get("archived") is True,
        )

    def resolve_branch(self, repository: ApprovedRepository, branch: str) -> str:
        """Return the full commit SHA a branch points at right now."""
        if not is_safe_branch_name(branch):
            raise GitHubError(IntakeStatus.UNEXPECTED_RESPONSE, "the branch name is unsafe")
        path = f"/repos/{repository.owner}/{repository.name}/git/ref/heads/{branch}"
        data = self._get_json(path)
        target = data.get("object")
        sha = target.get("sha") if isinstance(target, dict) else None
        if (
            data.get("ref") != f"refs/heads/{branch}"
            or not isinstance(target, dict)
            or target.get("type") != "commit"
            or not isinstance(sha, str)
            or _SHA_PATTERN.fullmatch(sha) is None
        ):
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, f"branch {branch} did not resolve to a commit"
            )
        return sha

    def get_issue(self, repository: ApprovedRepository, number: int) -> BugReport:
        """Fetch one issue. Pull requests are refused."""
        path = f"/repos/{repository.owner}/{repository.name}/issues/{int(number)}"
        data = self._get_json(path)

        # GitHub's Issues API also returns pull requests, marked by this field.
        html_url = data.get("html_url")
        if "pull_request" in data or (isinstance(html_url, str) and "/pull/" in html_url):
            raise GitHubError(
                IntakeStatus.NOT_AN_ISSUE, f"{repository.slug}#{number} is a pull request"
            )
        if data.get("number") != number or isinstance(data.get("number"), bool):
            raise GitHubError(
                IntakeStatus.UNEXPECTED_RESPONSE, "the response is for a different issue number"
            )
        expected_repository = f"{API_BASE}/repos/{repository.slug}"
        repository_url = data.get("repository_url")
        if not isinstance(repository_url, str) or repository_url.lower() != expected_repository:
            raise GitHubError(
                IntakeStatus.REPOSITORY_MISMATCH,
                "the issue belongs to a different repository than the one requested",
            )
        title = data.get("title")
        body = data.get("body")
        state = data.get("state")
        if not isinstance(title, str) or not isinstance(body, str | None):
            raise GitHubError(IntakeStatus.UNEXPECTED_RESPONSE, "the issue has no usable text")
        if state not in ("open", "closed"):
            raise GitHubError(IntakeStatus.UNEXPECTED_RESPONSE, "the issue state is not recognized")
        return BugReport(
            issue=IssueRef(owner=repository.owner, repository=repository.name, number=number),
            repository_id=repository.repository_id,
            state=state,
            title=capture(title, max_chars=MAX_TITLE_CHARS),
            body=capture(body or "", max_chars=MAX_BODY_CHARS, remove_html_comments=True),
            fetched_at=datetime.now(UTC),
        )
