"""The stateful repair loop.

    propose -> evaluate -> verified                  -> done
                        -> not accepted -> feedback  -> propose (next proposal)
                                                     -> done (proposals or a budget used up)

It is the baseline plus one thing: after a proposal that was not accepted,
the model is told why and may propose again. Everything else is shared with
the baseline: the same read-only tools, patch policy, safe edit application
and sandbox checks, called through the same functions.

What the model is told comes only from the agent's own checks. Hidden tests
are not part of a run at all: the evaluator uses them after this function
has returned, and nothing it finds comes back here.

Every proposal is applied to the original files, never on top of an earlier
one, so a run cannot drift away from the snapshot. The agent's answer is its
last proposal.

The run's whole state is plain JSON data (``RepairState``), and each step is
a function from state to state. That keeps the steps testable on their own
and lets the state be stored after every transition.
"""

import hashlib
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from patchpilot.agent.baseline import (
    Attempt,
    RepairLimits,
    RunLimits,
    RunRecord,
    RunStatus,
    _explore_and_propose,
    _Stop,
    build_record,
    evaluate_proposal,
    new_totals,
)
from patchpilot.agent.prompts import (
    REPAIR_SYSTEM_PROMPT,
    BugDescription,
    build_feedback_message,
    build_task_message,
    prompt_hash,
)
from patchpilot.agent.proposal import PatchProposal
from patchpilot.agent.trace import TraceWriter
from patchpilot.llm.base import ModelClient, Usage
from patchpilot.llm.budget import RunBudget
from patchpilot.repo.tools import RepositoryTools, ToolBudget
from patchpilot.sandbox.models import PytestCommand, SandboxLimits, SandboxRunner
from patchpilot.sandbox.tree import tree_digest

Phase = Literal["propose", "evaluate", "feedback", "done"]


class RepairState(BaseModel):
    """Everything a repair run knows, as plain JSON data."""

    model_config = ConfigDict(extra="forbid")

    phase: Phase = "propose"
    # The proposal being worked on, counted from one.
    attempt: int = 1
    messages: list[dict[str, Any]]
    # A proposal that was given and is waiting to be evaluated.
    proposal: PatchProposal | None = None
    attempts: list[Attempt] = []
    status: RunStatus | None = None
    detail: str = ""
    model_turns: int = 0
    generation_attempts: int = 0
    retries: int = 0
    token_count_requests: int = 0
    usage: Usage = Usage()
    models_reported: list[str] = []
    tool_calls: int = 0
    cost_micro_usd: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class _Context:
    """What the steps need besides the state. None of it is stored."""

    files: Mapping[str, bytes]
    tools: RepositoryTools
    tool_budget: ToolBudget
    model: ModelClient
    sandbox: SandboxRunner
    budget: RunBudget
    trace: TraceWriter
    limits: RunLimits
    repair: RepairLimits
    commands: Sequence[PytestCommand]
    sandbox_limits: SandboxLimits
    source_roots: Sequence[str]
    totals: dict[str, Any]
    clock: Callable[[], float]
    started: float
    # The tree of the last evaluated proposal, if it applied and broke no rule.
    patched: dict[str, bytes] | None = None


def _check_budgets(context: _Context) -> None:
    """End the run if a cumulative budget is used up.

    Cost is checked by the ledger before every request, and model turns and
    tool calls by their own limits; these are the remaining three.
    """
    usage: Usage = context.totals["usage"]
    tokens = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens
    )
    repair = context.repair
    if tokens >= repair.max_total_tokens:
        raise _Stop(
            RunStatus.BUDGET_EXCEEDED, f"the token budget of {repair.max_total_tokens} is used up"
        )
    if context.totals["attempts"] >= repair.max_generation_attempts:
        raise _Stop(
            RunStatus.BUDGET_EXCEEDED,
            f"the limit of {repair.max_generation_attempts} generation attempts is used up",
        )
    if context.clock() - context.started >= repair.max_seconds:
        raise _Stop(
            RunStatus.BUDGET_EXCEEDED, f"the time budget of {repair.max_seconds:.0f} s is used up"
        )


def _finish(state: RepairState, status: RunStatus, detail: str) -> str:
    state.phase, state.status, state.detail = "done", status, detail
    return f"{status.value}: {detail}"


def _propose(context: _Context, state: RepairState) -> str:
    """Let the model read the snapshot and give its next proposal."""
    try:
        state.proposal = _explore_and_propose(
            context.model,
            context.tools,
            state.messages,
            context.limits,
            context.budget,
            context.trace,
            context.totals,
            system=REPAIR_SYSTEM_PROMPT,
            guard=lambda: _check_budgets(context),
        )
    except _Stop as stop:
        if stop.status is not RunStatus.INVALID_PROPOSAL:
            return _finish(state, stop.status, stop.detail)
        # An answer that is not a proposal uses up one proposal.
        state.attempts.append(Attempt(status=stop.status, detail=stop.detail))
        context.patched = None
        state.phase = "feedback"
        return "the answer was not a usable proposal"
    state.phase = "evaluate"
    return "the model gave a proposal"


def _evaluate(context: _Context, state: RepairState) -> str:
    """Apply the proposal to the original files, check the rules and verify it."""
    if state.proposal is None:
        return _finish(state, RunStatus.FAILED_INFRASTRUCTURE, "no proposal to evaluate")
    attempt, context.patched = evaluate_proposal(
        context.files,
        state.proposal,
        limits=context.limits,
        commands=context.commands,
        sandbox_limits=context.sandbox_limits,
        source_roots=context.source_roots,
        sandbox=context.sandbox,
    )
    state.proposal = None
    state.attempts.append(attempt)
    if attempt.status in (RunStatus.CANDIDATE_VERIFIED, RunStatus.FAILED_INFRASTRUCTURE):
        return _finish(state, attempt.status, attempt.detail)
    state.phase = "feedback"
    return f"proposal {state.attempt} was not accepted: {attempt.status.value}"


def _feedback(context: _Context, state: RepairState) -> str:
    """Decide whether another proposal is allowed and, if so, say what went wrong."""
    last = state.attempts[-1]
    if state.attempt >= context.repair.max_attempts:
        proposals = "proposal" if state.attempt == 1 else "proposals"
        return _finish(
            state,
            last.status,
            f"no verified patch after {state.attempt} {proposals}; the last one: {last.detail}",
        )
    try:
        _check_budgets(context)
    except _Stop as stop:
        return _finish(state, stop.status, stop.detail)
    if not state.messages or state.messages[-1]["role"] != "assistant":
        # A cut-off or empty answer leaves nothing to reply to.
        return _finish(state, last.status, last.detail)
    state.messages.append(
        {
            "role": "user",
            "content": build_feedback_message(
                attempt=state.attempt,
                remaining=context.repair.max_attempts - state.attempt,
                status=last.status.value,
                detail=last.detail,
                verification=last.verification,
            ),
        }
    )
    state.attempt += 1
    state.phase = "propose"
    return "the model was told why and may propose again"


_STEPS: dict[str, Callable[[_Context, RepairState], str]] = {
    "propose": _propose,
    "evaluate": _evaluate,
    "feedback": _feedback,
}


def _sync(context: _Context, state: RepairState) -> None:
    """Copy the run's counters into the state, so the state alone describes the run."""
    totals = context.totals
    state.model_turns = totals["turns"]
    state.generation_attempts = totals["attempts"]
    state.retries = totals["retries"]
    state.token_count_requests = totals["token_counts"]
    state.usage = totals["usage"]
    state.models_reported = sorted(totals["models"])
    state.tool_calls = context.tool_budget.calls
    state.cost_micro_usd = context.budget.spent
    state.elapsed_seconds = context.clock() - context.started


def config_digest(limits: RunLimits, repair: RepairLimits) -> str:
    material = limits.model_dump_json() + repair.model_dump_json()
    return hashlib.sha256(material.encode()).hexdigest()


def run_repair(
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
    repair: RepairLimits | None = None,
    run_cap_usd: float = 0.75,
    repository_commit: str | None = None,
    run_id: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_transition: Callable[[RepairState], None] | None = None,
) -> tuple[RunRecord, dict[str, bytes] | None]:
    """Run one bounded repair. Returns the record and the last proposal's tree, if any.

    The tree is for the evaluator, which grades it after this function has
    finished. ``on_transition`` receives the state after every step.
    """
    limits = limits or RunLimits()
    repair = repair or RepairLimits()
    run_id = run_id or uuid.uuid4().hex[:16]
    started_at = datetime.now(UTC)
    tool_budget = ToolBudget(
        max_calls=limits.max_tool_calls,
        max_output_bytes=limits.max_tool_output_bytes,
        max_call_output_bytes=limits.max_tool_call_output_bytes,
    )
    context = _Context(
        files=files,
        tools=RepositoryTools(files, tool_budget),
        tool_budget=tool_budget,
        model=model,
        sandbox=sandbox,
        budget=budget,
        trace=trace,
        limits=limits,
        repair=repair,
        commands=commands,
        sandbox_limits=sandbox_limits,
        source_roots=source_roots,
        totals=new_totals(),
        clock=clock,
        started=clock(),
    )
    tree_sha = tree_digest(files)
    prompt_sha = prompt_hash(REPAIR_SYSTEM_PROMPT)
    config_sha = config_digest(limits, repair)
    trace.start(
        {
            "run_id": run_id,
            "task_id": task_id,
            "mode": "repair",
            "repository_tree_sha256": tree_sha,
            "prompt_sha256": prompt_sha,
            "config_sha256": config_sha,
        }
    )
    state = RepairState(
        messages=[
            {
                "role": "user",
                "content": build_task_message(
                    bug,
                    source_roots=tuple(source_roots),
                    test_targets=tuple(
                        target for command in commands for target in command.targets
                    ),
                    proposals=repair.max_attempts,
                ),
            }
        ]
    )
    while state.phase != "done":
        source, attempt = state.phase, state.attempt
        reason = _STEPS[source](context, state)
        _sync(context, state)
        trace.transition(source, state.phase, attempt, reason)
        if on_transition is not None:
            on_transition(state)

    status = state.status or RunStatus.FAILED_INFRASTRUCTURE
    last = state.attempts[-1] if state.attempts else None
    trace.finish(status.value, state.detail, last.diff if last else "")
    record = build_record(
        run_id=run_id,
        task_id=task_id,
        mode="repair",
        status=status,
        detail=state.detail,
        started_at=started_at,
        duration_seconds=state.elapsed_seconds,
        totals=context.totals,
        tool_calls=tool_budget.calls,
        cost_micro_usd=budget.spent,
        last=last,
        attempts=state.attempts,
        limits=limits,
        prompt_sha256=prompt_sha,
        config_sha256=config_sha,
        repository_tree_sha256=tree_sha,
        repository_commit=repository_commit,
        run_cap_usd=run_cap_usd,
        repair=repair,
    )
    return record, context.patched
