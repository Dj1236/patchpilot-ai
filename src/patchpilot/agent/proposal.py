"""The one structured answer the model gives: a proposed patch.

Everything in a proposal is model output and therefore untrusted. It is
validated here, checked against the patch policy, applied to a copy of the
snapshot and executed only inside the sandbox.
"""

import json
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints, ValidationError

from patchpilot.sandbox.models import FrozenModel
from patchpilot.sandbox.tree import validate_relative_path

MAX_PROPOSAL_BYTES = 262_144

ProposalPath = Annotated[str, AfterValidator(validate_relative_path)]
Chunk = Annotated[str, StringConstraints(max_length=20_000)]


class ProposalError(ValueError):
    """The model's final answer is not a usable proposal."""


class Edit(FrozenModel):
    """Replace one exact, unique piece of text in an existing file."""

    path: ProposalPath
    expected_original: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    replacement: Chunk


class NewTestFile(FrozenModel):
    path: ProposalPath
    content: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class PatchProposal(FrozenModel):
    summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    root_cause: Annotated[str, StringConstraints(max_length=4000)]
    relevant_files: tuple[ProposalPath, ...] = Field(max_length=20)
    edits: tuple[Edit, ...] = Field(min_length=1, max_length=20)
    regression_tests: tuple[NewTestFile, ...] = Field(min_length=1, max_length=3)


def _text(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


# The schema given to the model. It carries structure only; lengths, counts
# and path rules are enforced by the Pydantic models above after parsing.
PROPOSAL_SCHEMA: dict[str, Any] = _object(
    {
        "summary": _text("One or two sentences describing the fix."),
        "root_cause": _text("Why the bug happens."),
        "relevant_files": {
            "type": "array",
            "items": _text("Repository-relative path of a file relevant to the bug."),
        },
        "edits": {
            "type": "array",
            "items": _object(
                {
                    "path": _text("Repository-relative path of an existing source file."),
                    "expected_original": _text(
                        "Exact text currently in the file. It must occur exactly once."
                    ),
                    "replacement": _text("Text that replaces expected_original."),
                }
            ),
        },
        "regression_tests": {
            "type": "array",
            "items": _object(
                {
                    "path": _text("Path of a NEW test file, for example tests/test_issue.py."),
                    "content": _text("Full content of the new pytest file."),
                }
            ),
        },
    }
)


def parse_proposal(text: str) -> PatchProposal:
    """Parse the model's final text into a proposal or raise ``ProposalError``."""
    if len(text.encode("utf-8", errors="replace")) > MAX_PROPOSAL_BYTES:
        raise ProposalError("the proposal is too large")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise ProposalError("the final answer is not valid JSON") from None
    try:
        return PatchProposal.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ProposalError(f"the proposal is invalid in: {', '.join(fields[:8])}") from None
