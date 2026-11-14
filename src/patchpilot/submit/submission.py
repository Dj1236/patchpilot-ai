"""Opening the approved pull request, with read-only checks before every write.

The writer can send only the few requests this needs, to the one bound
repository, and nothing else: no merge, no update or deletion of a branch,
no force push, no write to the default branch. A request outside its list
is refused before it is sent.

Every submission reconciles first. It reads the repository, the base
branch, an existing branch of the same name and existing pull requests from
it, and continues from what is really there. A write whose outcome is
unknown (a timeout, a dropped connection) is never repeated in the same
process: the submission is marked uncertain and stops. Running it again
reconciles with reads before deciding anything.

Git objects are content-addressed, so creating the same blob or tree again
gives the same object and is harmless. A repeated commit is merely unused.
Only the branch and the pull request are one-off, and both are looked up
before they are created.
"""

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from patchpilot.sandbox.models import FrozenModel
from patchpilot.submit.approval import Approval, check_approval
from patchpilot.submit.proposal import PullRequestProposal, canonical, write_private

SUBMISSION_FILE = "submission.json"
API_HOST = "api.github.com"
MAX_RESPONSE_BYTES = 1_000_000
_USER_AGENT = "patchpilot-ai"

State = Literal["started", "commit_created", "branch_created", "pull_request_open", "uncertain"]


class SubmissionError(RuntimeError):
    """The submission stopped. ``uncertain`` means a write may or may not have happened."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class Submission(FrozenModel):
    schema_version: Literal[1] = 1
    approval_id: str
    proposal_sha256: str
    repository: str
    branch: str
    state: State
    commit_sha: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    detail: str = ""
    events: tuple[str, ...] = ()


class _Response(FrozenModel):
    status: int
    body: Any


class GitHubWriter:
    """The only requests PatchPilot ever sends with a write credential.

    Bound to one repository. A fine-grained token is required: a classic
    token, which GitHub reports with scope headers, is refused because its
    scopes cannot be limited to one repository.
    """

    def __init__(
        self,
        repository: str,
        token: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not token.get_secret_value().strip():
            raise SubmissionError("no GitHub write token is configured")
        self._slug = repository
        self._token = token
        self.writes = 0
        self.requests: list[str] = []
        prefix = re.escape(f"/repos/{repository}")
        self._allowed = {
            "GET": re.compile(
                rf"{prefix}(|/branches/[^/]+|/git/commits/[0-9a-f]{{40}}"
                rf"|/git/ref/heads/[A-Za-z0-9._/-]+|/pulls)"
            ),
            "POST": re.compile(rf"{prefix}/(git/blobs|git/trees|git/commits|git/refs|pulls)"),
        }
        self._client = httpx.Client(
            base_url=f"https://{API_HOST}",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def send(
        self, method: str, path: str, *, params: dict[str, str] | None = None, body: Any = None
    ) -> _Response:
        pattern = self._allowed.get(method)
        if pattern is None or not pattern.fullmatch(path):
            raise SubmissionError(f"refusing a request outside the allowed list: {method} {path}")
        request = self._client.build_request(
            method,
            path,
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
        )
        if request.url.scheme != "https" or request.url.host != API_HOST:
            raise SubmissionError("refusing to send a request outside the GitHub API")
        self.requests.append(f"{method} {path}")
        self.writes += method != "GET"
        try:
            response = self._client.send(request)
        except httpx.HTTPError as error:
            # A write that may have arrived is uncertain; a read is just a failure.
            raise SubmissionError(
                f"{method} {path} failed: {type(error).__name__}", uncertain=method != "GET"
            ) from None
        if "x-oauth-scopes" in response.headers:
            raise SubmissionError(
                "a classic token was used; use a fine-grained token for one repository"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SubmissionError(f"the response to {method} {path} is too large")
        try:
            data = response.json() if response.content else None
        except ValueError:
            data = None
        return _Response(status=response.status_code, body=data)


def git_blob_sha(data: bytes) -> str:
    """The SHA-1 GitHub gives a blob of ``data``, computed locally to check its answer."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


_MEANINGS = {
    401: "the GitHub token was rejected",
    403: "the GitHub token may not do this",
    404: "not found, or not visible to this token",
}


def _ok(response: _Response, what: str, expected: tuple[int, ...] = (200,)) -> Any:
    if response.status not in expected:
        meaning = _MEANINGS.get(response.status, f"HTTP {response.status}")
        raise SubmissionError(f"{what}: {meaning}")
    return response.body if isinstance(response.body, (dict, list)) else {}


class _Run:
    """One submission attempt: the proposal, the writer and the record being kept."""

    def __init__(
        self,
        proposal: PullRequestProposal,
        approval: Approval,
        writer: GitHubWriter,
        directory: Path,
        previous: Submission | None,
    ) -> None:
        self.proposal, self.writer, self.directory = proposal, writer, directory
        self.record = previous or Submission(
            approval_id=approval.approval_id,
            proposal_sha256=approval.proposal_sha256,
            repository=proposal.repository,
            branch=proposal.branch,
            state="started",
        )
        self.repo = f"/repos/{proposal.repository}"

    def save(self, **changes: Any) -> None:
        event = changes.pop("event", None)
        if event:
            changes["events"] = (*self.record.events, event)
        self.record = self.record.model_copy(update=changes)
        write_private(self.directory / SUBMISSION_FILE, canonical(self.record).decode() + "\n")

    def get(self, path: str, **params: str) -> _Response:
        return self.writer.send("GET", f"{self.repo}{path}", params=params or None)

    def post(self, path: str, body: Any, what: str, uncertain_state: bool) -> Any:
        try:
            response = self.writer.send("POST", f"{self.repo}{path}", body=body)
        except SubmissionError as error:
            if error.uncertain and uncertain_state:
                self.save(state="uncertain", detail=str(error), event=f"uncertain: {what}")
                raise SubmissionError(
                    f"{what}: the outcome is unknown. Nothing was repeated. Run submit "
                    "again: it checks GitHub with read-only requests before any write.",
                    uncertain=True,
                ) from None
            raise
        return response


def _check_target(run: _Run) -> str:
    """Check the repository, the token's permission and that the base is not stale."""
    proposal = run.proposal
    data = _ok(run.get(""), "reading the repository")
    if data.get("id") != proposal.repository_id or str(data.get("full_name", "")).lower() != (
        proposal.repository
    ):
        raise SubmissionError("the repository is not the one the proposal is bound to")
    if not (data.get("permissions") or {}).get("push"):
        raise SubmissionError("the token cannot push to this repository")
    if proposal.branch in (data.get("default_branch"), proposal.base_branch):
        raise SubmissionError("refusing to write to the default or the base branch")
    branch = _ok(run.get(f"/branches/{proposal.base_branch}"), "reading the base branch")
    if (branch.get("commit") or {}).get("sha") != proposal.base_commit:
        raise SubmissionError(
            f"{proposal.base_branch} has moved since the proposal was made; the approval is "
            "stale. Make a new proposal from the current base and approve it again"
        )
    base = _ok(run.get(f"/git/commits/{proposal.base_commit}"), "reading the base commit")
    tree = (base.get("tree") or {}).get("sha")
    if not isinstance(tree, str):
        raise SubmissionError("the base commit has no tree")
    return tree


def _expected_tree(run: _Run, base_tree: str) -> str:
    """Create the blobs and the tree. Repeating this is harmless: objects are by content."""
    entries: list[dict[str, str]] = []
    for change in run.proposal.changes:
        data = change.content.encode()
        body = {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        blob = _ok(
            run.post("/git/blobs", body, "creating a blob", False), "creating a blob", (201,)
        )
        if blob.get("sha") != git_blob_sha(data):
            raise SubmissionError(f"GitHub stored different content for {change.path}")
        entries.append({"path": change.path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree_body: dict[str, Any] = {"base_tree": base_tree, "tree": entries}
    tree = _ok(
        run.post("/git/trees", tree_body, "creating a tree", False), "creating a tree", (201,)
    )
    if not isinstance(tree.get("sha"), str):
        raise SubmissionError("GitHub did not return a tree")
    return str(tree["sha"])


def _matches(run: _Run, commit_sha: str, tree_sha: str) -> bool:
    commit = _ok(run.get(f"/git/commits/{commit_sha}"), "reading a commit")
    parents = [parent.get("sha") for parent in commit.get("parents") or []]
    return (commit.get("tree") or {}).get("sha") == tree_sha and parents == [
        run.proposal.base_commit
    ]


def _existing_pull_request(run: _Run, commit_sha: str | None) -> dict[str, Any] | None:
    owner = run.proposal.repository.split("/")[0]
    found = _ok(
        run.get("/pulls", head=f"{owner}:{run.proposal.branch}", state="all"),
        "looking for an existing pull request",
    )
    for pull in found if isinstance(found, list) else []:
        head = pull.get("head") or {}
        if head.get("ref") == run.proposal.branch:
            if commit_sha is not None and head.get("sha") != commit_sha:
                raise SubmissionError("a pull request from this branch exists with other content")
            return dict(pull)
    return None


def submit_proposal(
    proposal: PullRequestProposal,
    approval: Approval,
    writer: GitHubWriter,
    directory: Path,
    *,
    now: datetime | None = None,
) -> Submission:
    """Open the approved draft pull request, or report why not. Never merges anything."""
    check_approval(approval, proposal, now=now)
    previous = load_submission(directory)
    if previous is not None and (
        previous.approval_id != approval.approval_id
        or previous.proposal_sha256 != approval.proposal_sha256
    ):
        raise SubmissionError(
            "this run was already submitted under another approval; it is not submitted again"
        )
    if previous is not None and previous.state == "pull_request_open":
        return previous
    run = _Run(proposal, approval, writer, directory, previous)
    run.save(event="submission started" if previous is None else f"resumed from {previous.state}")

    base_tree = _check_target(run)
    tree_sha = _expected_tree(run, base_tree)

    # The branch: reuse ours if it is already there, refuse anyone else's.
    ref = run.get(f"/git/ref/heads/{proposal.branch}")
    commit_sha: str | None = None
    if ref.status == 200:
        existing = str(((ref.body or {}).get("object") or {}).get("sha", ""))
        if not _matches(run, existing, tree_sha):
            raise SubmissionError(f"branch {proposal.branch} exists with other content")
        commit_sha = existing
        run.save(state="branch_created", commit_sha=commit_sha, event="branch already present")
    elif ref.status != 404:
        _ok(ref, "reading the branch")
    pull = _existing_pull_request(run, commit_sha)
    if pull is None and commit_sha is None:
        message = f"Proposed fix for #{proposal.issue_number} (PatchPilot run {proposal.run_id})"
        body = {"message": message, "tree": tree_sha, "parents": [proposal.base_commit]}
        made = run.post("/git/commits", body, "creating the commit", False)
        commit_sha = str(_ok(made, "creating the commit", (201,)).get("sha", ""))
        run.save(state="commit_created", commit_sha=commit_sha, event="commit created")
        body = {"ref": f"refs/heads/{proposal.branch}", "sha": commit_sha}
        created = run.post("/git/refs", body, "creating the branch", True)
        if created.status == 422:
            raise SubmissionError(
                "the branch appeared while submitting; run submit again to reconcile"
            )
        _ok(created, "creating the branch", (201,))
        check = _ok(run.get(f"/git/ref/heads/{proposal.branch}"), "checking the branch")
        if (check.get("object") or {}).get("sha") != commit_sha:
            raise SubmissionError("the new branch does not point at the approved commit")
        run.save(state="branch_created", event="branch created and checked")
    if pull is None:
        pull_body: dict[str, Any] = {
            "title": proposal.title,
            "body": proposal.body,
            "head": proposal.branch,
            "base": proposal.base_branch,
            "draft": True,
            "maintainer_can_modify": False,
        }
        opened = run.post("/pulls", pull_body, "opening the pull request", True)
        if opened.status == 422:
            raise SubmissionError("GitHub refused the pull request; run submit again to reconcile")
        pull = _ok(opened, "opening the pull request", (201,))
        if (pull.get("head") or {}).get("sha") != commit_sha or (pull.get("base") or {}).get(
            "ref"
        ) != proposal.base_branch:
            raise SubmissionError("the opened pull request does not match the approved patch")
        event = "draft pull request opened"
    else:
        event = "existing pull request found; none created"
    run.save(
        state="pull_request_open",
        commit_sha=commit_sha or (pull.get("head") or {}).get("sha"),
        pull_request_number=pull.get("number"),
        pull_request_url=pull.get("html_url"),
        detail="",
        event=event,
    )
    return run.record


def load_submission(directory: Path) -> Submission | None:
    path = directory / SUBMISSION_FILE
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 256 * 1024:
        raise SubmissionError("the stored submission record is not usable")
    try:
        return Submission.model_validate(json.loads(path.read_text()))
    except ValueError:
        raise SubmissionError("the stored submission record is not valid") from None
