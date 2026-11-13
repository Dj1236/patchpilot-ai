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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

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
from patchpilot.agent.verify import Verification, verify_patch
from patchpilot.llm.base import (
    APPROVED_MODELS,
    PINNED_MODELS,
    ModelClient,
    ModelError,
    ModelRequest,
    ModelTurn,
    Provider,
    Usage,
)
from patchpilot.llm.budget import BudgetExceededError, RunBudget, UnknownModelPriceError
from patchpilot.patch.apply import PatchError, apply_proposal, unified_diff
from patchpilot.patch.policy import PolicyLimits, check_policy
from patchpilot.repo.tools import BudgetExceededError as ToolBudgetExceededError
from patchpilot.repo.tools import RepositoryTools, ToolBudget
from patchpilot.sandbox.models import FrozenModel, PytestCommand, SandboxLimits, SandboxRunner
from patchpilot.sandbox.tree import tree_digest

_RETRYABLE = ("timeout", "rate_limited", "unavailable", "connection")
_MAX_RETRY_WAIT_SECONDS = 60.0


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

    provider: Provider = "anthropic"
    model: str = "claude-sonnet-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_turns: int = Field(default=20, ge=1, le=100)
    max_output_tokens: int = Field(default=16_000, ge=256, le=64_000)
    max_request_attempts: int = Field(default=2, ge=1, le=3)
    max_tool_calls: int = Field(default=60, ge=1, le=500)
    max_tool_output_bytes: int = Field(default=600_000, ge=1000)
    max_tool_call_output_bytes: int = Field(default=32_000, ge=500)
    policy: PolicyLimits = PolicyLimits()

    @model_validator(mode="after")
    def _only_an_approved_model(self) -> "RunLimits":
        if self.model not in APPROVED_MODELS[self.provider]:
            raise ValueError(
                f"provider {self.provider!r} runs only {list(APPROVED_MODELS[self.provider])}, "
                f"not {self.model!r}"
            )
        return self

    @classmethod
    def for_provider(cls, provider: Provider, model: str | None = None) -> "RunLimits":
        """Return the limits for one provider and one of its approved models."""
        return cls(provider=provider, model=model or PINNED_MODELS[provider])

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RepairLimits(FrozenModel):
    """Bounds on a repair run, on top of ``RunLimits``. All are for the whole run."""

    # Proposals the model may give. The strict maximum is five.
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_total_tokens: int = Field(default=400_000, ge=1_000)
    max_generation_attempts: int = Field(default=30, ge=1, le=200)
    max_seconds: float = Field(default=1_200.0, gt=0)


class Manifest(FrozenModel):
    """What is needed to identify and reproduce a run."""

    runner_version: str
    provider: str
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
    repair: RepairLimits | None = None


class Attempt(FrozenModel):
    """What happened to one proposal. A repair run has several; the baseline has one."""

    status: RunStatus
    detail: str
    proposal: PatchProposal | None = None
    diff: str = ""
    changed_files: tuple[str, ...] = ()
    policy_violations: tuple[str, ...] = ()
    verification: Verification | None = None


class RunRecord(FrozenModel):
    run_id: str
    task_id: str
    mode: str = "baseline"
    status: RunStatus
    detail: str
    started_at: datetime
    duration_seconds: float
    model_turns: int
    # Requests sent to the generation endpoint, whether or not they succeeded.
    # A turn is an attempt that returned an answer; a retry is a repeat of a
    # request that failed. Token counting is a separate, free endpoint.
    generation_attempts: int = 0
    retries: int = 0
    token_count_requests: int = 0
    tool_calls: int
    usage: Usage
    cost_micro_usd: int
    proposal: PatchProposal | None = None
    diff: str = ""
    changed_files: tuple[str, ...] = ()
    policy_violations: tuple[str, ...] = ()
    verification: Verification | None = None
    # Every proposal of a repair run, in order. Empty for the baseline.
    attempts: tuple[Attempt, ...] = ()
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
    totals: dict[str, Any],
) -> ModelTurn:
    """Reserve, send and settle one request, with a bounded number of attempts.

    Each attempt has its own reservation. A failed attempt is charged in
    full, because a request that errored may still have been billed.
    """
    trace.request(index, request)
    last: ModelError | None = None
    for attempt in range(attempts):
        try:
            tokens = model.count_input_tokens(request)
        except ModelError as error:
            # A refusal to go on is not a request; any other failure was one.
            totals["token_counts"] += error.kind != "attempt_limit"
            raise _Stop(
                RunStatus.FAILED_INFRASTRUCTURE,
                f"could not size the request ({error.kind}): {error}",
            ) from None
        totals["token_counts"] += 1
        try:
            reservation = budget.reserve(request.model, tokens, request.max_tokens)
        except BudgetExceededError as error:
            raise _Stop(RunStatus.BUDGET_EXCEEDED, str(error)) from None
        except UnknownModelPriceError as error:
            raise _Stop(RunStatus.FAILED_INFRASTRUCTURE, str(error)) from None
        totals["attempts"] += 1
        totals["retries"] += attempt > 0
        try:
            turn = model.complete(request)
        except ModelError as error:
            budget.settle_failed(reservation, error.kind)
            trace.error(index, error)
            last = error
            if error.kind in _RETRYABLE:
                if attempt + 1 < attempts and error.retry_after_seconds > 0:
                    # A rate limit window has to pass before a retry can succeed.
                    time.sleep(min(error.retry_after_seconds, _MAX_RETRY_WAIT_SECONDS))
                continue
            break
        budget.settle(reservation, request.model, turn.usage)
        trace.response(index, turn)
        return turn
    # The message is built by the adapter from the status and the provider's
    # short error code only, so it is safe to show.
    kind = last.kind if last else "unknown"
    raise _Stop(RunStatus.FAILED_INFRASTRUCTURE, f"the model request failed ({kind}): {last}")


def _explore_and_propose(
    model: ModelClient,
    tools: RepositoryTools,
    messages: list[dict[str, Any]],
    limits: RunLimits,
    budget: RunBudget,
    trace: TraceWriter,
    totals: dict[str, Any],
    *,
    system: str = SYSTEM_PROMPT,
    guard: Callable[[], None] | None = None,
) -> PatchProposal:
    """Let the model read the snapshot until it answers with a proposal.

    ``messages`` is the conversation so far and is extended in place, so a
    caller can continue it. ``guard`` runs before every request and may end
    the run. Turns are counted across calls: the limit is for the whole run.
    """
    while totals["turns"] < limits.max_turns:
        index = totals["turns"]
        if guard is not None:
            guard()
        request = ModelRequest(
            model=limits.model,
            system=system,
            messages=tuple(messages),
            tools=tool_specs(),
            output_schema=PROPOSAL_SCHEMA,
            max_tokens=limits.max_output_tokens,
            effort=limits.effort,
        )
        turn = _send(model, request, budget, trace, index, limits.max_request_attempts, totals)
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
            if turn.content:
                messages.append({"role": "assistant", "content": list(turn.content)})
            try:
                return parse_proposal(turn.text)
            except ProposalError as error:
                raise _Stop(RunStatus.INVALID_PROPOSAL, str(error)) from None
        raise _Stop(RunStatus.INVALID_PROPOSAL, "the model stopped without a proposal")
    raise _Stop(
        RunStatus.BUDGET_EXCEEDED, f"no proposal after the limit of {limits.max_turns} model turns"
    )


def new_totals() -> dict[str, Any]:
    """Return the counters one run keeps while it talks to the model."""
    return {
        "turns": 0,
        "attempts": 0,
        "retries": 0,
        "token_counts": 0,
        "usage": Usage(),
        "models": set(),
    }


def evaluate_proposal(
    files: Mapping[str, bytes],
    proposal: PatchProposal,
    *,
    limits: RunLimits,
    commands: Sequence[PytestCommand],
    sandbox_limits: SandboxLimits,
    source_roots: Sequence[str],
    sandbox: SandboxRunner,
) -> tuple[Attempt, dict[str, bytes] | None]:
    """Apply one proposal to the original files, check the rules and verify it.

    Returns what happened and the patched tree. The tree is ``None`` unless
    the proposal applied and broke no patch rule.
    """
    try:
        candidate = apply_proposal(files, proposal)
    except PatchError as error:
        return (
            Attempt(
                status=RunStatus.INVALID_PROPOSAL,
                detail=f"{error.code}: {error}",
                proposal=proposal,
            ),
            None,
        )
    diff = unified_diff(files, candidate)
    violations = check_policy(files, proposal, candidate, limits.policy)
    if violations:
        return (
            Attempt(
                status=RunStatus.REJECTED_POLICY,
                detail="; ".join(violations),
                proposal=proposal,
                diff=diff,
                policy_violations=violations,
            ),
            None,
        )
    verification = verify_patch(
        files,
        candidate,
        proposal,
        commands=commands,
        limits=sandbox_limits,
        source_roots=source_roots,
        sandbox=sandbox,
    )
    if verification.infrastructure_failure:
        status = RunStatus.FAILED_INFRASTRUCTURE
    elif verification.verified:
        status = RunStatus.CANDIDATE_VERIFIED
    else:
        status = RunStatus.UNVERIFIED
    attempt = Attempt(
        status=status,
        detail=verification.reason,
        proposal=proposal,
        diff=diff,
        changed_files=tuple(
            sorted(path for path in candidate if candidate[path] != files.get(path))
        ),
        verification=verification,
    )
    return attempt, candidate


def build_record(
    *,
    run_id: str,
    task_id: str,
    mode: str,
    status: RunStatus,
    detail: str,
    started_at: datetime,
    duration_seconds: float,
    totals: Mapping[str, Any],
    tool_calls: int,
    cost_micro_usd: int,
    last: Attempt | None,
    attempts: Sequence[Attempt],
    limits: RunLimits,
    prompt_sha256: str,
    config_sha256: str,
    repository_tree_sha256: str,
    repository_commit: str | None,
    run_cap_usd: float,
    repair: RepairLimits | None = None,
) -> RunRecord:
    """Assemble the final record of a run from its counters and its last proposal."""
    verification = last.verification if last else None
    return RunRecord(
        run_id=run_id,
        task_id=task_id,
        mode=mode,
        status=status,
        detail=detail,
        started_at=started_at,
        duration_seconds=duration_seconds,
        model_turns=totals["turns"],
        generation_attempts=totals["attempts"],
        retries=totals["retries"],
        token_count_requests=totals["token_counts"],
        tool_calls=tool_calls,
        usage=totals["usage"],
        cost_micro_usd=cost_micro_usd,
        proposal=last.proposal if last else None,
        diff=last.diff if last else "",
        changed_files=last.changed_files if last else (),
        policy_violations=last.policy_violations if last else (),
        verification=verification,
        attempts=tuple(attempts),
        manifest=Manifest(
            runner_version=__version__,
            provider=limits.provider,
            model_requested=limits.model,
            models_reported=tuple(sorted(totals["models"])),
            effort=limits.effort,
            prompt_sha256=prompt_sha256,
            config_sha256=config_sha256,
            repository_tree_sha256=repository_tree_sha256,
            repository_commit=repository_commit,
            sandbox_image_id=verification.image_id if verification else None,
            run_cap_usd=run_cap_usd,
            limits=limits,
            repair=repair,
        ),
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
    totals = new_totals()
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

    attempt: Attempt | None = None
    patched: dict[str, bytes] | None = None
    try:
        message = build_task_message(
            bug,
            source_roots=tuple(source_roots),
            test_targets=tuple(target for command in commands for target in command.targets),
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        proposal = _explore_and_propose(model, tools, messages, limits, budget, trace, totals)
        attempt, patched = evaluate_proposal(
            files,
            proposal,
            limits=limits,
            commands=commands,
            sandbox_limits=sandbox_limits,
            source_roots=source_roots,
            sandbox=sandbox,
        )
        status, detail = attempt.status, attempt.detail
    except _Stop as stop:
        status, detail = stop.status, stop.detail

    trace.finish(status.value, detail, attempt.diff if attempt else "")
    record = build_record(
        run_id=run_id,
        task_id=task_id,
        mode="baseline",
        status=status,
        detail=detail,
        started_at=started_at,
        duration_seconds=time.monotonic() - started,
        totals=totals,
        tool_calls=tool_budget.calls,
        cost_micro_usd=budget.spent,
        last=attempt,
        attempts=(),
        limits=limits,
        prompt_sha256=prompt_hash(),
        config_sha256=limits.digest(),
        repository_tree_sha256=tree_sha,
        repository_commit=repository_commit,
        run_cap_usd=run_cap_usd,
    )
    return record, patched
