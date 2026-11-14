"""A person's approval of one exact pull request proposal.

An approval is created only by ``patchpilot approve``, which shows the
proposal and asks the person to type the start of its patch digest. No code
path of the agent can create one. It is bound to the digest of the whole
proposal and repeats the fields that matter, so a different patch, target,
base commit, branch or operation is not approved by it.
"""

import getpass
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from patchpilot.sandbox.models import FrozenModel
from patchpilot.submit.proposal import (
    PullRequestProposal,
    canonical,
    proposal_digest,
    write_private,
)

APPROVAL_FILE = "approval.json"
CONFIRM_CHARS = 12
DEFAULT_VALIDITY = timedelta(hours=24)


class ApprovalError(PermissionError):
    """There is no valid approval for what is about to be done."""


class Approval(FrozenModel):
    schema_version: Literal[1] = 1
    approval_id: str
    proposal_sha256: str
    repository: str
    repository_id: int = Field(ge=1)
    issue_number: int
    base_commit: str
    patch_sha256: str
    record_sha256: str
    branch: str
    operation: Literal["create_draft_pull_request"] = "create_draft_pull_request"
    approved_by: str
    approved_at: datetime
    expires_at: datetime


def approve(
    proposal: PullRequestProposal,
    confirmation: str,
    *,
    now: datetime | None = None,
    validity: timedelta = DEFAULT_VALIDITY,
) -> Approval:
    """Approve ``proposal`` if ``confirmation`` is the start of its patch digest."""
    if confirmation.strip().lower() != proposal.patch_sha256[:CONFIRM_CHARS]:
        raise ApprovalError("the confirmation does not match the patch digest; nothing approved")
    moment = now or datetime.now(UTC)
    return Approval(
        approval_id=uuid.uuid4().hex,
        proposal_sha256=proposal_digest(proposal),
        repository=proposal.repository,
        repository_id=proposal.repository_id,
        issue_number=proposal.issue_number,
        base_commit=proposal.base_commit,
        patch_sha256=proposal.patch_sha256,
        record_sha256=proposal.record_sha256,
        branch=proposal.branch,
        approved_by=getpass.getuser(),
        approved_at=moment,
        expires_at=moment + validity,
    )


def check_approval(
    approval: Approval, proposal: PullRequestProposal, *, now: datetime | None = None
) -> None:
    """Raise ``ApprovalError`` unless ``approval`` covers exactly ``proposal`` right now."""
    bound = {
        "proposal_sha256": proposal_digest(proposal),
        "repository": proposal.repository,
        "repository_id": proposal.repository_id,
        "issue_number": proposal.issue_number,
        "base_commit": proposal.base_commit,
        "patch_sha256": proposal.patch_sha256,
        "record_sha256": proposal.record_sha256,
        "branch": proposal.branch,
        "operation": proposal.operation,
    }
    for name, value in bound.items():
        if getattr(approval, name) != value:
            raise ApprovalError(f"the approval does not cover this proposal: {name} differs")
    moment = now or datetime.now(UTC)
    if not approval.approved_at <= moment < approval.expires_at:
        raise ApprovalError("the approval has expired; review and approve the proposal again")


def save_approval(directory: Path, approval: Approval) -> None:
    write_private(directory / APPROVAL_FILE, canonical(approval).decode() + "\n")


def load_approval(directory: Path) -> Approval:
    path = directory / APPROVAL_FILE
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ApprovalError("there is no approval for this proposal; run patchpilot approve")
    try:
        return Approval.model_validate_json(path.read_bytes())
    except ValueError:
        raise ApprovalError("the stored approval is not valid") from None
