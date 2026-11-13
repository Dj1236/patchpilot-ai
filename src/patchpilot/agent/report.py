"""The public face of a run: a sanitized report and a machine-readable record.

A report never contains prompts, thinking blocks or the raw trace. It does
contain text the model wrote (its summary and explanation) and a diff built
from repository content. Both are untrusted, so they are bounded, stripped of
control and invisible characters, and written so that they cannot break out
of their place in the document.
"""

import html
import json
import os
from collections.abc import Iterable
from pathlib import Path

from patchpilot.agent.baseline import RunRecord, RunStatus
from patchpilot.agent.trace import redact
from patchpilot.intake.text import clean_for_presentation
from patchpilot.llm.budget import usd

RECORD_FILE = "record.json"
REPORT_FILE = "report.md"
_MAX_QUOTED_CHARS = 1500
_MAX_DIFF_CHARS = 20_000
# On a free tier the ledger amount is what the run would cost at list price,
# not money spent.
_FREE_TIER_NOTE = {
    "gemini": (
        " (list-price upper bound; the free tier charges nothing unless the key's "
        "project has billing enabled)"
    )
}

_MODE_MEANING = {
    "baseline": "single attempt, no test feedback",
    "repair": "several proposals, with feedback from the agent's own checks",
}

_STATUS_MEANING = {
    RunStatus.CANDIDATE_VERIFIED: (
        "The regression test failed before the patch and all tests passed after it. "
        "This is the agent's own evidence, not an independent result."
    ),
    RunStatus.UNVERIFIED: "The patch was applied but its verification did not hold.",
    RunStatus.INVALID_PROPOSAL: "The model's answer could not be used as a patch.",
    RunStatus.REJECTED_POLICY: "The patch broke a patch rule and was not run.",
    RunStatus.MODEL_REFUSED: "The model declined the request.",
    RunStatus.BUDGET_EXCEEDED: "A spending, turn or tool limit ended the run.",
    RunStatus.FAILED_INFRASTRUCTURE: "The model API or the sandbox failed.",
}


def _quoted(text: str) -> str:
    """Render untrusted text as an inert block quote."""
    cleaned, _ = clean_for_presentation(text, max_chars=_MAX_QUOTED_CHARS)
    escaped = html.escape(cleaned, quote=False).replace("`", "&#96;").replace("@", "&#64;")
    return "\n".join(f"> {line}" for line in escaped.splitlines() or [""])


def _fenced(text: str, language: str) -> str:
    """Put text in a code fence longer than any run of backticks inside it."""
    cleaned, notes = clean_for_presentation(text, max_chars=_MAX_DIFF_CHARS)
    longest = 0
    run = 0
    for char in cleaned:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    tail = "\n(diff cut for length)" if notes.truncated else ""
    return f"{fence}{language}\n{cleaned.rstrip()}\n{fence}{tail}"


def public_report(record: RunRecord, resolved: bool | None = None) -> str:
    """Return the sanitized Markdown report for a run.

    ``resolved`` is the independent hidden-test result when the run was part
    of an evaluation, and ``None`` otherwise.
    """
    manifest = record.manifest
    lines = [
        f"# PatchPilot run {record.run_id}",
        "",
        f"- **Task:** `{record.task_id}`",
        f"- **Mode:** {record.mode} ({_MODE_MEANING.get(record.mode, '')})",
        f"- **Status:** `{record.status.value}`. {_STATUS_MEANING[record.status]}",
        f"- **Detail:** {html.escape(record.detail[:500], quote=False)}",
    ]
    if resolved is not None:
        outcome = "resolved" if resolved else "not resolved"
        lines.append(f"- **Independent grading (hidden tests):** {outcome}")
    lines += [
        f"- **Model turns:** {record.model_turns}, **tool calls:** {record.tool_calls}",
        f"- **Generation attempts:** {record.generation_attempts}, of which "
        f"{record.retries} were retries; **token-count requests:** "
        f"{record.token_count_requests}",
        f"- **Tokens (reported by the provider):** {record.usage.input_tokens} input, "
        f"{record.usage.output_tokens} output, "
        f"{record.usage.cache_read_input_tokens} cache read, "
        f"{record.usage.cache_creation_input_tokens} cache write",
        f"- **Cost:** ${usd(record.cost_micro_usd):.4f} of a ${manifest.run_cap_usd:.2f} run cap"
        + _FREE_TIER_NOTE.get(manifest.provider, ""),
        "- The cost is the internal ledger's figure, not a bill: a failed request is "
        "charged its full reservation, because it may have been billed.",
        f"- **Duration:** {record.duration_seconds:.1f} s",
        "",
    ]
    if record.attempts:
        limit = manifest.repair.max_attempts if manifest.repair else len(record.attempts)
        lines += [
            "## Proposals",
            "",
            f"{len(record.attempts)} of at most {limit}. Each was applied to the original "
            "code. The sections below describe the last one.",
            "",
            "| Proposal | Outcome | Detail |",
            "|---|---|---|",
        ]
        for number, attempt in enumerate(record.attempts, start=1):
            detail = html.escape(attempt.detail[:200], quote=False).replace("|", "\\|")
            lines.append(f"| {number} | `{attempt.status.value}` | {detail} |")
        lines.append("")
    if record.verification is not None:
        lines += [
            "## Verification evidence",
            "",
            "| Check | Expected | Result | Holds |",
            "|---|---|---|---|",
        ]
        for step in record.verification.steps:
            holds = "yes" if step.ok else "no"
            lines.append(f"| {step.name} | {step.expected} | {step.status.value} | {holds} |")
        lines.append("")
    if record.policy_violations:
        lines += ["## Patch rules broken", ""]
        lines += [f"- {html.escape(item[:300], quote=False)}" for item in record.policy_violations]
        lines.append("")
    if record.proposal is not None:
        lines += [
            "## Model's explanation",
            "",
            "Written by the model. Untrusted text, shown as data.",
            "",
            _quoted(record.proposal.summary),
            ">",
            _quoted(record.proposal.root_cause),
            "",
        ]
    if record.diff:
        lines += [
            "## Proposed diff",
            "",
            "Computed from the original and patched trees.",
            "",
            _fenced(record.diff, "diff"),
            "",
        ]
    lines += [
        "## Manifest",
        "",
        f"- Provider: `{manifest.provider}`. Model requested: `{manifest.model_requested}`, "
        f"reported: `{', '.join(manifest.models_reported) or 'none'}`, "
        f"effort `{manifest.effort}`",
        f"- Prompt hash: `{manifest.prompt_sha256}`",
        f"- Configuration hash: `{manifest.config_sha256}`",
        f"- Repository digest: `{manifest.repository_tree_sha256}`"
        + (f", commit `{manifest.repository_commit}`" if manifest.repository_commit else ""),
        f"- Sandbox image: `{manifest.sandbox_image_id or 'not used'}`",
        f"- Runner version: `{manifest.runner_version}`",
        "",
        "The sandbox reduces risk and is not guaranteed isolation. Test results come from a "
        "process that runs the code under test; see docs/grading-integrity.md.",
        "",
    ]
    return "\n".join(lines)


def write_run(
    record: RunRecord,
    directory: Path,
    resolved: bool | None = None,
    secrets: Iterable[str] = (),
) -> None:
    """Write ``record.json`` and ``report.md`` next to the run's trace.

    Known secret values are removed from both, as they are from the trace.
    """
    known = tuple(secrets)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"record": record.model_dump(mode="json"), "resolved": resolved}
    for name, text in (
        (RECORD_FILE, json.dumps(payload, indent=2)),
        (REPORT_FILE, public_report(record, resolved)),
    ):
        descriptor = os.open(directory / name, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(redact(text, known))
