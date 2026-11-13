"""Free checks before the first paid request.

Preflight asks the provider two things that cost nothing: whether the model
exists for this credential, and how many input tokens the real first request
has. It never asks the model to generate anything.

A clean preflight shows that the credential works, the model is available
and the request is well formed enough to be counted. It does not guarantee
that the first paid request will succeed.
"""

import json
from typing import Any, Literal

from patchpilot.agent.baseline import RunLimits
from patchpilot.agent.prompts import (
    SYSTEM_PROMPT,
    BugDescription,
    build_task_message,
    prompt_hash,
    tool_specs,
)
from patchpilot.agent.proposal import PROPOSAL_SCHEMA
from patchpilot.agent.trace import redact, secrets_from_environment
from patchpilot.llm.anthropic_client import AnthropicModel
from patchpilot.llm.base import ModelError, ModelRequest
from patchpilot.llm.budget import prices_for, to_micro, worst_case_cost
from patchpilot.sandbox.models import FrozenModel, PytestCommand

PreflightFailure = Literal[
    "no_credentials",
    "authentication",
    "permission",
    "model_unavailable",
    "malformed_request",
    "rate_limited",
    "quota_exhausted",
    "not_configured",
    "unavailable",
    "connection",
    "provider_error",
]
_MAX_DETAIL = 300
_NO_CREDENTIAL = (
    "no credential is configured; set ANTHROPIC_API_KEY in the environment that starts PatchPilot"
)


class PreflightError(Exception):
    """A preflight check failed. The message never contains credentials or prompts."""

    def __init__(self, kind: PreflightFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class PreflightReport(FrozenModel):
    provider: str
    task_id: str
    model: str
    model_display_name: str | None
    effort: str
    prompt_sha256: str
    input_tokens_counted: int
    input_tokens_reserved: int
    max_output_tokens: int
    estimated_input_cost_micro_usd: int
    reservation_micro_usd: int
    run_cap_micro_usd: int
    total_cap_micro_usd: int
    ledger_committed_micro_usd: int
    fits_run_cap: bool
    fits_total_cap: bool


def first_request(
    bug: BugDescription,
    *,
    source_roots: tuple[str, ...],
    commands: tuple[PytestCommand, ...],
    limits: RunLimits,
) -> ModelRequest:
    """Build the request the baseline sends first for this task."""
    message = build_task_message(
        bug,
        source_roots=source_roots,
        test_targets=tuple(target for command in commands for target in command.targets),
    )
    return ModelRequest(
        model=limits.model,
        system=SYSTEM_PROMPT,
        messages=({"role": "user", "content": message},),
        tools=tool_specs(),
        output_schema=PROPOSAL_SCHEMA,
        max_tokens=limits.max_output_tokens,
        effort=limits.effort,
    )


def _failure(error: Exception, during: str) -> PreflightError:
    import anthropic

    detail = ""
    if isinstance(error, anthropic.APIStatusError):
        # The provider's explanation of a rejected request names the field at
        # fault. It is bounded and stripped of known secrets before use.
        body = error.body if isinstance(error.body, dict) else {}
        inner = body.get("error") if isinstance(body.get("error"), dict) else {}
        text = inner.get("message") if isinstance(inner, dict) else None
        if isinstance(text, str):
            detail = ": " + redact(text, secrets_from_environment())[:_MAX_DETAIL]
    kind: PreflightFailure
    if isinstance(error, anthropic.AuthenticationError):
        kind, summary = "authentication", "the provider rejected the credential"
    elif isinstance(error, anthropic.PermissionDeniedError):
        kind, summary = "permission", "the credential may not use this model or endpoint"
    elif isinstance(error, anthropic.NotFoundError):
        kind, summary = "model_unavailable", "the model was not found for this credential"
    elif isinstance(error, anthropic.BadRequestError | anthropic.UnprocessableEntityError):
        kind, summary = "malformed_request", "the provider rejected the request as malformed"
    elif isinstance(error, anthropic.RateLimitError):
        kind, summary = "rate_limited", "the provider's rate limit was reached"
    elif isinstance(error, anthropic.APIStatusError):
        kind, summary = "provider_error", f"the provider returned HTTP {error.status_code}"
        detail = ""
    elif isinstance(error, anthropic.APIConnectionError):
        kind, summary, detail = "connection", "the provider could not be reached", ""
    else:
        kind, summary, detail = "provider_error", f"unexpected {type(error).__name__}", ""
    return PreflightError(kind, f"{during}: {summary}{detail}")


def connect(timeout_seconds: float = 30.0) -> Any:
    """Build the SDK client from the environment. The credential is never read here."""
    import anthropic

    try:
        return anthropic.Anthropic(max_retries=0, timeout=timeout_seconds)
    except anthropic.AnthropicError:
        raise PreflightError("no_credentials", _NO_CREDENTIAL) from None


def run_preflight(
    client: Any,
    request: ModelRequest,
    *,
    task_id: str,
    run_cap_usd: float,
    total_cap_usd: float,
    ledger_committed_micro_usd: int,
) -> PreflightReport:
    """Check the model and count the request. Only free endpoints are called."""
    import anthropic

    prices_for(request.model)  # a model with no pinned price is refused before any request
    try:
        model = client.models.retrieve(request.model)
    except anthropic.AnthropicError as error:
        raise _failure(error, "checking the model") from None
    except TypeError:
        # The SDK raises this before sending anything when it has no credential.
        raise PreflightError("no_credentials", _NO_CREDENTIAL) from None
    try:
        counted = client.messages.count_tokens(**AnthropicModel._arguments(request))
        tokens = int(counted.input_tokens)
    except anthropic.AnthropicError as error:
        raise _failure(error, "counting input tokens") from None
    except (AttributeError, TypeError, ValueError):
        raise PreflightError(
            "provider_error", "counting input tokens: the response had no token count"
        ) from None

    # The same padded count the paid path reserves with.
    reserved_tokens = AnthropicModel(_Counted(tokens)).count_input_tokens(request)
    return _report(
        "anthropic",
        request,
        tokens,
        reserved_tokens,
        getattr(model, "display_name", None),
        task_id=task_id,
        run_cap_usd=run_cap_usd,
        total_cap_usd=total_cap_usd,
        ledger_committed_micro_usd=ledger_committed_micro_usd,
    )


def _gemini_failure(error: Exception, during: str) -> PreflightError:
    from patchpilot.llm.gemini_client import describe_error, failure

    status, code, _ = describe_error(error)
    body = getattr(error, "details", None) or getattr(error, "body", None)
    kind: PreflightFailure
    if "api_key_invalid" in json.dumps(body, default=str).lower():
        # The provider reports a bad key as a malformed request.
        status = 401
    by_status: dict[int | None, tuple[PreflightFailure, str]] = {
        400: ("malformed_request", "the provider rejected the request as malformed"),
        401: ("authentication", "the provider rejected the credential"),
        403: ("permission", "the credential or its project may not use this model"),
        404: ("model_unavailable", "the model was not found for this credential"),
    }
    if not isinstance(error, ModelError) and status in by_status:
        kind, summary = by_status[status]
        return PreflightError(kind, f"{during}: {summary}" + (f" ({code})" if code else ""))
    model_error = failure(error)
    kinds: dict[str, PreflightFailure] = {
        "rate_limited": "rate_limited",
        "quota_exhausted": "quota_exhausted",
        "not_configured": "not_configured",
        "unavailable": "unavailable",
        "connection": "connection",
        "timeout": "connection",
    }
    return PreflightError(kinds.get(model_error.kind, "provider_error"), f"{during}: {model_error}")


def run_gemini_preflight(
    client: Any,
    request: ModelRequest,
    *,
    task_id: str,
    run_cap_usd: float,
    total_cap_usd: float,
    ledger_committed_micro_usd: int,
) -> PreflightReport:
    """Check the model and estimate the request. Only free endpoints are called.

    The counting endpoint takes plain content, so the request is counted as
    one text. This confirms the key and the model, and sizes the request; it
    does not prove the provider accepts the tools, the answer schema or the
    thinking setting. The first generation request is the first real check.
    """
    from patchpilot.llm.gemini_client import GeminiModel, padded

    prices_for(request.model)  # a model with no pinned price is refused before any request
    try:
        model = client.models.get(model=request.model)
    except Exception as error:
        raise _gemini_failure(error, "checking the model") from None
    try:
        counted = client.models.count_tokens(
            model=request.model, contents=GeminiModel.flattened(request)
        )
        tokens = int(counted.total_tokens)
    except Exception as error:
        raise _gemini_failure(error, "counting input tokens") from None
    return _report(
        "gemini",
        request,
        tokens,
        padded(tokens),
        getattr(model, "display_name", None),
        task_id=task_id,
        run_cap_usd=run_cap_usd,
        total_cap_usd=total_cap_usd,
        ledger_committed_micro_usd=ledger_committed_micro_usd,
    )


def _report(
    provider: str,
    request: ModelRequest,
    tokens: int,
    reserved_tokens: int,
    display: object,
    *,
    task_id: str,
    run_cap_usd: float,
    total_cap_usd: float,
    ledger_committed_micro_usd: int,
) -> PreflightReport:
    prices = prices_for(request.model)
    reservation = worst_case_cost(reserved_tokens, request.max_tokens, prices)
    run_cap, total_cap = to_micro(run_cap_usd), to_micro(total_cap_usd)
    return PreflightReport(
        provider=provider,
        task_id=task_id,
        model=request.model,
        model_display_name=display if isinstance(display, str) else None,
        effort=request.effort,
        prompt_sha256=prompt_hash(),
        input_tokens_counted=tokens,
        input_tokens_reserved=reserved_tokens,
        max_output_tokens=request.max_tokens,
        estimated_input_cost_micro_usd=tokens * prices.input,
        reservation_micro_usd=reservation,
        run_cap_micro_usd=run_cap,
        total_cap_micro_usd=total_cap,
        ledger_committed_micro_usd=ledger_committed_micro_usd,
        fits_run_cap=reservation <= run_cap,
        fits_total_cap=ledger_committed_micro_usd + reservation <= total_cap,
    )


class _Counted:
    """A stand-in that returns an already known count, to reuse the padding rule."""

    def __init__(self, tokens: int) -> None:
        self._tokens = tokens
        self.messages = self

    def count_tokens(self, **_: Any) -> Any:
        return type("Count", (), {"input_tokens": self._tokens})()
