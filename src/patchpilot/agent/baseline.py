"""The single-attempt baseline.

    explore (read-only tools, within budgets)
      -> one structured proposal
      -> patch policy
      -> apply to a copy of the snapshot
      -> verify in the sandbox
      -> record

The model sees no test result and gets no second proposal. Tool calls while
exploring are reading, not retrying. Every ending has one status.
"""

import hashlib
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from patchpilot import __version__
from patchpilot.agent.prompts import (
    SYSTEM_PROMPT,
    BugDescription,
    build_task_message,
    prompt_hash,
    render_tool_result,
    tool_specs,
)
from patchpilot.agent.proposal import PROPOSAL_SCHEMA, PatchProposal, ProposalError, parse_proposal
from patchpilot.agent.trace import TraceWriter
from patchpilot.agent.verify import SandboxRunner, Verification, verify_patch
from patchpilot.llm.base import ModelClient, ModelError, ModelRequest, ModelTurn, Usage
from patchpilot.llm.budget import BudgetExceededError, RunBudget, UnknownModelPriceError
from patchpilot.patch.apply import PatchError, apply_proposal, unified_diff
from patchpilot.patch.policy import PolicyLimits, check_policy
from patchpilot.repo.tools import BudgetExceededError as ToolBudgetExceededError
from patchpilot.repo.tools import RepositoryTools, ToolBudget
from patchpilot.sandbox.models import FrozenModel, PytestCommand, SandboxLimits
from patchpilot.sandbox.tree import tree_digest

_RETRYABLE = ("timeout", "rate_limited", "connection")


class RunStatus(StrEnum):
    CANDIDATE_VERIFIED = "candidate_verified"
    UNVERIFIED = "unverified"
    INVALID_PROPOSAL = "invalid_proposal"
    REJECTED_POLICY = "rejected_policy"
    MODEL_REFUSED = "model_refused"
    BUDGET_EXCEEDED = "budget_exceeded"
    FAILED_INFRASTRUCTURE = "failed_infrastructure"


class RunLimits(FrozenModel):
    """Bounds on one run. Changing any of them changes the configuration hash."""

    model: str = "claude-sonnet-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_turns: int = Field(default=20, ge=1, le=100)
    max_output_tokens: int = Field(default=16_000, ge=256, le=64_000)
    max_request_attempts: int = Field(default=2, ge=1, le=3)
    max_tool_calls: int = Field(default=60, ge=1, le=500)
    max_tool_output_bytes: int = Field(default=600_000, ge=1000)
    max_tool_call_output_bytes: int = Field(default=32_000, ge=500)
    policy: PolicyLimits = PolicyLimits()

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Manifest(FrozenModel):
    """What is needed to identify and reproduce a run."""

    runner_version: str
    model_requested: str
    models_reported: tuple[str, ...]
    effort: str
    prompt_sha256: str
    config_sha256: str
    repository_tree_sha256: str
    repository_commit: str | None
    sandbox_image_id: str | None
    run_cap_usd: float
    limits: RunLimits


class RunRecord(FrozenModel):
    run_id: str
    task_id: str
    mode: str = "baseline"
    status: RunStatus
    detail: str
    started_at: datetime
    duration_seconds: float
    model_turns: int
    tool_calls: int
    usage: Usage
    cost_micro_usd: int
    proposal: PatchProposal | None = None
    diff: str = ""
    changed_files: tuple[str, ...] = ()
    policy_violations: tuple[str, ...] = ()
    verification: Verification | None = None
    manifest: Manifest


class _Stop(Exception):
    def __init__(self, status: RunStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _send(
    model: ModelClient,
    request: ModelRequest,
    budget: RunBudget,
    trace: TraceWriter,
    index: int,
    attempts: int,
) -> ModelTurn:
    """Reserve, send and settle one request, with a bounded number of attempts.

    Each attempt has its own reservation. A failed attempt is charged in
    full, because a request that errored may still have been billed.
    """
    trace.request(index, request)
    last: ModelError | None = None
    for _ in range(attempts):
        try:
            reservation = budget.reserve(
                request.model, model.count_input_tokens(request), request.max_tokens
            )
        except BudgetExceededError as error:
            raise _Stop(RunStatus.BUDGET_EXCEEDED, str(error)) from None
        except UnknownModelPriceError as error:
            raise _Stop(RunStatus.FAILED_INFRASTRUCTURE, str(error)) from None
        except ModelError as error:
            raise _Stop(
                RunStatus.FAILED_INFRASTRUCTURE, f"could not size the request: {error.kind}"
            ) from None
        try:
            turn = model.complete(request)
        except ModelError as error:
            budget.settle_failed(reservation, error.kind)
            trace.error(index, error)
            last = error
            if error.kind in _RETRYABLE:
                continue
            break
        budget.settle(reservation, request.model, turn.usage)
        trace.response(index, turn)
        return turn
    kind = last.kind if last else "unknown"
    raise _Stop(RunStatus.FAILED_INFRASTRUCTURE, f"the model request failed ({kind})")


def _explore_and_propose(
    model: ModelClient,
    tools: RepositoryTools,
    first_message: str,
    limits: RunLimits,
    budget: RunBudget,
    trace: TraceWriter,
    totals: dict[str, Any],
) -> PatchProposal:
    messages: list[dict[str, Any]] = [{"role": "user", "content": first_message}]
    for index in range(limits.max_turns):
        request = ModelRequest(
            model=limits.model,
            system=SYSTEM_PROMPT,
            messages=tuple(messages),
            tools=tool_specs(),
            output_schema=PROPOSAL_SCHEMA,
            max_tokens=limits.max_output_tokens,
            effort=limits.effort,
        )
        turn = _send(model, request, budget, trace, index, limits.max_request_attempts)
        totals["turns"] += 1
        totals["usage"] = totals["usage"].plus(turn.usage)
        if turn.model:
            totals["models"].add(turn.model)

        if turn.stop_reason == "refusal":
            raise _Stop(RunStatus.MODEL_REFUSED, "the model declined the request")
        if turn.stop_reason == "max_tokens":
            raise _Stop(RunStatus.INVALID_PROPOSAL, "the model's answer was cut off")
        if turn.stop_reason == "tool_use" and turn.tool_calls:
            # The assistant turn goes back exactly as received, and every
            # result for it goes back together in one message.
            messages.append({"role": "assistant", "content": list(turn.content)})
            results: list[dict[str, Any]] = []
            for call in turn.tool_calls:
                try:
                    result = tools.call(call.name, call.arguments)
                except ToolBudgetExceededError as error:
                    raise _Stop(RunStatus.BUDGET_EXCEEDED, str(error)) from None
                text, is_error = render_tool_result(result)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
            continue
        if turn.stop_reason == "end_turn":
            try:
                return parse_proposal(turn.text)
            except ProposalError as error:
                raise _Stop(RunStatus.INVALID_PROPOSAL, str(error)) from None
        raise _Stop(RunStatus.INVALID_PROPOSAL, "the model stopped without a proposal")
    raise _Stop(
        RunStatus.BUDGET_EXCEEDED, f"no proposal after the limit of {limits.max_turns} model turns"
    )


def run_baseline(
    *,
    task_id: str,
    bug: BugDescription,
    files: Mapping[str, bytes],
    source_roots: Sequence[str],
    commands: Sequence[PytestCommand],
    sandbox_limits: SandboxLimits,
    model: ModelClient,
    sandbox: SandboxRunner,
    budget: RunBudget,
    trace: TraceWriter,
    limits: RunLimits | None = None,
    run_cap_usd: float = 0.75,
    repository_commit: str | None = None,
    run_id: str | None = None,
) -> tuple[RunRecord, dict[str, bytes] | None]:
    """Run one single-attempt repair. Returns the record and the patched tree, if any.

    The patched tree is returned for the evaluator, which grades it after
    this function has finished. Nothing the evaluator finds comes back here.
    """
    limits = limits or RunLimits()
    run_id = run_id or uuid.uuid4().hex[:16]
    started_at = datetime.now(UTC)
    started = time.monotonic()
    tool_budget = ToolBudget(
        max_calls=limits.max_tool_calls,
        max_output_bytes=limits.max_tool_output_bytes,
        max_call_output_bytes=limits.max_tool_call_output_bytes,
    )
    tools = RepositoryTools(files, tool_budget)
    totals: dict[str, Any] = {"turns": 0, "usage": Usage(), "models": set()}
    tree_sha = tree_digest(files)
    trace.start(
        {
            "run_id": run_id,
            "task_id": task_id,
            "mode": "baseline",
            "repository_tree_sha256": tree_sha,
            "prompt_sha256": prompt_hash(),
            "config_sha256": limits.digest(),
        }
    )

    proposal: PatchProposal | None = None
    patched: dict[str, bytes] | None = None
    diff = ""
    violations: tuple[str, ...] = ()
    verification: Verification | None = None
    try:
        message = build_task_message(
            bug,
            source_roots=tuple(source_roots),
            test_targets=tuple(target for command in commands for target in command.targets),
        )
        proposal = _explore_and_propose(model, tools, message, limits, budget, trace, totals)
        try:
            candidate = apply_proposal(files, proposal)
        except PatchError as error:
            raise _Stop(RunStatus.INVALID_PROPOSAL, f"{error.code}: {error}") from None
        diff = unified_diff(files, candidate)
        violations = check_policy(files, proposal, candidate, limits.policy)
        if violations:
            raise _Stop(RunStatus.REJECTED_POLICY, "; ".join(violations))
        patched = candidate
        verification = verify_patch(
            files,
            patched,
            proposal,
            commands=commands,
            limits=sandbox_limits,
            source_roots=source_roots,
            sandbox=sandbox,
        )
        if verification.infrastructure_failure:
            raise _Stop(RunStatus.FAILED_INFRASTRUCTURE, verification.reason)
        status = RunStatus.CANDIDATE_VERIFIED if verification.verified else RunStatus.UNVERIFIED
        detail = verification.reason
    except _Stop as stop:
        status, detail = stop.status, stop.detail

    trace.finish(status.value, detail)
    changed = (
        tuple(sorted(path for path in patched if patched[path] != files.get(path)))
        if patched is not None
        else ()
    )
    record = RunRecord(
        run_id=run_id,
        task_id=task_id,
        status=status,
        detail=detail,
        started_at=started_at,
        duration_seconds=time.monotonic() - started,
        model_turns=totals["turns"],
        tool_calls=tool_budget.calls,
        usage=totals["usage"],
        cost_micro_usd=budget.spent,
        proposal=proposal,
        diff=diff,
        changed_files=changed,
        policy_violations=violations,
        verification=verification,
        manifest=Manifest(
            runner_version=__version__,
            model_requested=limits.model,
            models_reported=tuple(sorted(totals["models"])),
            effort=limits.effort,
            prompt_sha256=prompt_hash(),
            config_sha256=limits.digest(),
            repository_tree_sha256=tree_sha,
            repository_commit=repository_commit,
            sandbox_image_id=verification.image_id if verification else None,
            run_cap_usd=run_cap_usd,
            limits=limits,
        ),
    )
    return record, patched
