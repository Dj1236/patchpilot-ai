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
from pathlib import Path
from typing import Any

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
from patchpilot.agent.checkpoint import Checkpoint, CheckpointError, RepairState, save_checkpoint
from patchpilot.agent.prompts import (
    REPAIR_SYSTEM_PROMPT,
    BugDescription,
    build_feedback_message,
    build_task_message,
    prompt_hash,
)
from patchpilot.agent.trace import TraceWriter
from patchpilot.llm.base import ModelClient, Usage
from patchpilot.llm.budget import RunBudget, SpendLedger
from patchpilot.patch.apply import PatchError, apply_proposal
from patchpilot.repo.tools import RepositoryTools, ToolBudget
from patchpilot.sandbox.models import PytestCommand, SandboxLimits, SandboxRunner
from patchpilot.sandbox.tree import tree_digest


class Interrupted(Exception):
    """The run was stopped on purpose after a checkpoint, as a process failure would."""


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
    # Stores the state. Called only at boundaries where the state is complete.
    save: Callable[[RepairState], None] = lambda state: None
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


def _before_request(context: _Context, state: RepairState) -> None:
    """Check the budgets, then store the state: the boundary before a model request.

    Everything earlier is complete here: the previous answer was received,
    its cost settled and its tool calls answered. If the process stops
    during the request that follows, this is what a resume starts from.
    """
    _check_budgets(context)
    _sync(context, state)
    context.save(state)


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
            guard=lambda: _before_request(context, state),
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
    state.tool_output_bytes = context.tool_budget.output_bytes
    state.cost_micro_usd = context.budget.spent
    state.elapsed_seconds = context.clock() - context.started


def config_digest(limits: RunLimits, repair: RepairLimits) -> str:
    material = limits.model_dump_json() + repair.model_dump_json()
    return hashlib.sha256(material.encode()).hexdigest()


def _saver(
    directory: Path,
    secrets: Sequence[str],
    header: Mapping[str, Any],
    first_sequence: int,
    interrupt_after: int | None,
) -> Callable[[RepairState], None]:
    """Return the function that stores the state as the run's next checkpoint."""
    written = 0

    def save(state: RepairState) -> None:
        nonlocal written
        written += 1
        save_checkpoint(
            directory,
            Checkpoint(**header, sequence=first_sequence + written - 1, state=state),
            secrets,
        )
        if interrupt_after is not None and written >= interrupt_after:
            raise Interrupted(f"stopped on purpose after {written} checkpoints")

    return save


def _drive(
    context: _Context,
    state: RepairState,
    *,
    header: Mapping[str, Any],
    on_transition: Callable[[RepairState], None] | None,
) -> tuple[RunRecord, dict[str, bytes] | None]:
    """Run the steps until the run is done, storing the state after each one."""
    while state.phase != "done":
        source, attempt = state.phase, state.attempt
        reason = _STEPS[source](context, state)
        _sync(context, state)
        context.trace.transition(source, state.phase, attempt, reason)
        context.save(state)
        if on_transition is not None:
            on_transition(state)

    status = state.status or RunStatus.FAILED_INFRASTRUCTURE
    last = state.attempts[-1] if state.attempts else None
    context.trace.finish(status.value, state.detail, last.diff if last else "")
    record = build_record(
        run_id=header["run_id"],
        task_id=header["task_id"],
        mode="repair",
        status=status,
        detail=state.detail,
        started_at=header["started_at"],
        duration_seconds=state.elapsed_seconds,
        totals=context.totals,
        tool_calls=context.tool_budget.calls,
        cost_micro_usd=context.budget.spent,
        last=last,
        attempts=state.attempts,
        limits=context.limits,
        prompt_sha256=header["prompt_sha256"],
        config_sha256=header["config_sha256"],
        repository_tree_sha256=header["repository_tree_sha256"],
        repository_commit=header["repository_commit"],
        run_cap_usd=header["run_cap_usd"],
        repair=context.repair,
    )
    return record, context.patched


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
    checkpoint_dir: Path | None = None,
    secrets: Sequence[str] = (),
    interrupt_after: int | None = None,
) -> tuple[RunRecord, dict[str, bytes] | None]:
    """Run one bounded repair. Returns the record and the last proposal's tree, if any.

    The tree is for the evaluator, which grades it after this function has
    finished. ``on_transition`` receives the state after every step. With
    ``checkpoint_dir`` the state is stored there at every safe boundary, so
    the run can be resumed. ``interrupt_after`` stops the run after that many
    checkpoints, to exercise a resume.
    """
    limits = limits or RunLimits()
    repair = repair or RepairLimits()
    tool_budget = ToolBudget(
        max_calls=limits.max_tool_calls,
        max_output_bytes=limits.max_tool_output_bytes,
        max_call_output_bytes=limits.max_tool_call_output_bytes,
    )
    header: dict[str, Any] = {
        "run_id": run_id or uuid.uuid4().hex[:16],
        "task_id": task_id,
        "started_at": datetime.now(UTC),
        "repository_tree_sha256": tree_digest(files),
        "repository_commit": repository_commit,
        "prompt_sha256": prompt_hash(REPAIR_SYSTEM_PROMPT),
        "config_sha256": config_digest(limits, repair),
        "run_cap_usd": run_cap_usd,
        "limits": limits,
        "repair": repair,
    }
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
    if checkpoint_dir is not None:
        context.save = _saver(checkpoint_dir, secrets, header, 1, interrupt_after)
    trace.start(
        {
            "run_id": header["run_id"],
            "task_id": task_id,
            "mode": "repair",
            "repository_tree_sha256": header["repository_tree_sha256"],
            "prompt_sha256": header["prompt_sha256"],
            "config_sha256": header["config_sha256"],
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
    return _drive(context, state, header=header, on_transition=on_transition)


def _check_resumable(
    checkpoint: Checkpoint, files: Mapping[str, bytes], ledger: SpendLedger
) -> None:
    """Refuse a checkpoint that does not describe this task, this code and this ledger."""
    state = checkpoint.state
    if tree_digest(files) != checkpoint.repository_tree_sha256:
        raise CheckpointError("the task's files are not the ones this run started from")
    if prompt_hash(REPAIR_SYSTEM_PROMPT) != checkpoint.prompt_sha256:
        raise CheckpointError("the prompts changed since this run started")
    if config_digest(checkpoint.limits, checkpoint.repair) != checkpoint.config_sha256:
        raise CheckpointError("the checkpoint's limits do not match its configuration hash")
    if state.attempt > checkpoint.repair.max_attempts or len(state.attempts) > state.attempt:
        raise CheckpointError("the checkpoint's proposal count is not possible for its limits")
    if (state.phase == "evaluate") != (state.proposal is not None):
        raise CheckpointError("the checkpoint's phase and its pending proposal disagree")
    if state.phase == "feedback" and not state.attempts:
        raise CheckpointError("the checkpoint waits to give feedback on a proposal it lacks")
    if (state.phase == "done") != (state.status is not None):
        raise CheckpointError("the checkpoint's phase and its final status disagree")

    # The ledger is written before and after every model request, so it shows
    # whether anything happened after this checkpoint was stored.
    reservations, still_open, committed = ledger.run_requests(checkpoint.run_id)
    if still_open:
        raise CheckpointError(
            "a model request was in flight when the run stopped, and whether it "
            "completed is unknown; the run is not resumed"
        )
    if reservations != state.generation_attempts or committed != state.cost_micro_usd:
        raise CheckpointError(
            "the ledger records model requests made after this checkpoint was written; "
            "the checkpoint is stale and the run is not resumed"
        )


def _last_tree(files: Mapping[str, bytes], state: RepairState) -> dict[str, bytes] | None:
    """Rebuild the tree of the last evaluated proposal. No sandbox is needed for that."""
    if not state.attempts:
        return None
    last = state.attempts[-1]
    usable = (RunStatus.UNVERIFIED, RunStatus.CANDIDATE_VERIFIED)
    if last.status not in usable or last.proposal is None:
        return None
    try:
        return apply_proposal(files, last.proposal)
    except PatchError:
        raise CheckpointError("the checkpoint's last proposal no longer applies") from None


def resume_repair(
    *,
    checkpoint: Checkpoint,
    checkpoint_dir: Path,
    files: Mapping[str, bytes],
    source_roots: Sequence[str],
    commands: Sequence[PytestCommand],
    sandbox_limits: SandboxLimits,
    model: ModelClient,
    sandbox: SandboxRunner,
    ledger: SpendLedger,
    trace: TraceWriter,
    secrets: Sequence[str] = (),
    clock: Callable[[], float] = time.monotonic,
    on_transition: Callable[[RepairState], None] | None = None,
    interrupt_after: int | None = None,
) -> tuple[RunRecord, dict[str, bytes] | None]:
    """Continue a repair run from its checkpoint, in a new process.

    The run keeps its identity, its limits and everything it has used: model
    turns, generation attempts, tokens, tool calls, cost and time. Nothing
    that was completed is done again. The limits come from the checkpoint,
    not from the current settings, so restarting cannot raise them.

    A checkpoint that the ledger shows to be out of date is refused: if a
    model request may have happened after it, the run is not continued.
    """
    _check_resumable(checkpoint, files, ledger)
    state = checkpoint.state.model_copy(deep=True)
    limits, repair = checkpoint.limits, checkpoint.repair
    tool_budget = ToolBudget(
        max_calls=limits.max_tool_calls,
        max_output_bytes=limits.max_tool_output_bytes,
        max_call_output_bytes=limits.max_tool_call_output_bytes,
    )
    tool_budget.calls, tool_budget.output_bytes = state.tool_calls, state.tool_output_bytes
    budget = RunBudget(ledger, checkpoint.run_id, checkpoint.run_cap_usd)
    budget.spent, budget.requests = state.cost_micro_usd, state.generation_attempts
    header = checkpoint.model_dump(exclude={"schema_version", "mode", "sequence", "state"})
    header["limits"], header["repair"] = limits, repair
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
        totals={
            "turns": state.model_turns,
            "attempts": state.generation_attempts,
            "retries": state.retries,
            "token_counts": state.token_count_requests,
            "usage": state.usage,
            "models": set(state.models_reported),
        },
        clock=clock,
        # Time already used counts; time while the process was down does not.
        started=clock() - state.elapsed_seconds,
        save=_saver(checkpoint_dir, secrets, header, checkpoint.sequence + 1, interrupt_after),
        patched=_last_tree(files, state),
    )
    trace.resumed(checkpoint.sequence, state.phase, state.attempt)
    return _drive(context, state, header=header, on_transition=on_transition)
