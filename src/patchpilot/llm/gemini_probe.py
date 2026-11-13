"""One minimal Gemini generation request, as a diagnostic.

The probe answers a single question: can this key get any answer from the
pinned model right now? It sends the simplest request the Interactions API
accepts, so a failure cannot be blamed on the agent's tools, answer schema,
thinking setting or history.

It is a real generation request. It uses one of the day's requests and is
entered in the spend ledger by the command that calls it. It is sent once:
no SDK retry, no automatic continuation, no retry here.

Only the HTTP status, a provider error code from a fixed list, timings and
token counts are reported. The provider's message text, the response body
and the model's answer are never returned.
"""

import time
from collections.abc import Callable
from typing import Any

from patchpilot.llm.base import Usage
from patchpilot.llm.gemini_client import GenerationLimiter, describe_error
from patchpilot.sandbox.models import FrozenModel

# Fixed, synthetic text. Nothing from a repository, an issue or a task is used.
PROMPT = "Reply with exactly OK."
MAX_OUTPUT_TOKENS = 64
TIMEOUT_SECONDS = 120.0
# A token is at least one character, so the prompt's length bounds its token
# count. The padding covers what the provider adds around it.
INPUT_TOKENS_RESERVED = len(PROMPT) + 256

# Provider error codes that may be shown, with what each means. Anything
# else is reported as unrecognized, never echoed.
REASONS: dict[str, str] = {
    "service_unavailable": "the provider is temporarily overloaded or down",
    "api_error": "the provider had an internal error",
    "deadline_exceeded": "the provider did not finish in time",
    "rate_limit_exceeded": "the per-minute limit was reached",
    "too_many_requests": "too many requests in a short time",
    "quota_exceeded": "the daily quota is used up",
    "authentication": "the provider rejected the credential",
    "permission_denied": "the credential or its project may not use this model",
    "payment_required": "the project's credit is used up",
    "failed_precondition": "a precondition is not met, such as billing or region",
    "model_not_found": "the model was not found for this credential",
    "not_found": "the endpoint or resource was not found",
    "invalid_request": "the provider rejected the request as malformed",
    "parameter_unknown": "the provider rejected an unknown parameter",
    "out_of_range": "a parameter is out of range",
    "unimplemented": "the feature is not supported",
}
_STATUSES = (
    "completed",
    "incomplete",
    "requires_action",
    "requires_continuation",
    "failed",
    "cancelled",
    "in_progress",
    "queued",
    "budget_exceeded",
)


class ProbeResult(FrozenModel):
    model: str
    response_received: bool
    http_status: int | None
    provider_code: str = ""
    reason: str = ""
    interaction_status: str = ""
    model_reported: str = ""
    answer_is_ok: bool = False
    usage: Usage = Usage()
    duration_seconds: float


def request_body(model: str) -> dict[str, Any]:
    """Return the whole request: no tools, no schema, no thinking setting, no history."""
    return {
        "model": model,
        "input": PROMPT,
        "generation_config": {"max_output_tokens": MAX_OUTPUT_TOKENS},
        "store": False,
    }


def run_probe(
    client: Any,
    model: str,
    *,
    limiter: GenerationLimiter | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """Send the one request and describe what came back. Never retries."""
    (limiter or GenerationLimiter()).acquire()
    started = clock()
    try:
        response = client.interactions.create(automatic_continuation=False, **request_body(model))
        status = str(response.status)
        usage = response.usage
        reported = str(response.model or "").removeprefix("models/")
        text = str(getattr(response, "output_text", "") or "")
    except Exception as error:
        http_status, code, daily = describe_error(error)
        if "Timeout" in type(error).__name__:
            reason = "no answer within the time limit"
        elif http_status is None:
            reason = "the provider could not be reached"
        elif daily:
            reason = REASONS["quota_exceeded"]
        else:
            reason = REASONS.get(code, "not classified")
        return ProbeResult(
            model=model,
            response_received=False,
            http_status=http_status,
            provider_code=code if code in REASONS else ("unrecognized" if code else ""),
            reason=reason,
            duration_seconds=clock() - started,
        )

    def count(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    return ProbeResult(
        model=model,
        response_received=True,
        http_status=200,
        interaction_status=status if status in _STATUSES else "unrecognized",
        model_reported=reported if reported.startswith("gemini-") else "unrecognized",
        answer_is_ok=text.strip() == "OK",
        # Upper bounds, as in the adapter.
        usage=Usage(
            input_tokens=count("total_input_tokens") + count("total_tool_use_tokens"),
            output_tokens=count("total_output_tokens") + count("total_thought_tokens"),
            cache_read_input_tokens=count("total_cached_tokens"),
        ),
        duration_seconds=clock() - started,
    )
