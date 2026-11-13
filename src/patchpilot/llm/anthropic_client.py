"""Claude through the official Anthropic SDK.

Nothing here is used by the automated tests except through a stand-in client
object; a real request is only ever made when a run is started with the
explicit live flag.

Choices that are deliberate:

- The SDK's own retries are off. Retries are done by the caller, one budget
  reservation per attempt, so no request is ever sent without being counted.
- No refusal fallback is requested. A declined request is reported as a
  refusal instead of being answered by a different model.
- Thinking is adaptive, which is the only mode this model accepts, and no
  sampling parameters are sent because the model rejects non-default values.
- Content blocks are returned as plain dictionaries exactly as received,
  thinking blocks included, so the caller can send them back unchanged.
"""

from typing import Any

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn, StopReason, ToolCall, Usage

_KNOWN_STOPS: tuple[StopReason, ...] = ("end_turn", "tool_use", "max_tokens", "refusal")
_COUNT_MARGIN = 1.10
_COUNT_PADDING = 256


class AnthropicModel:
    """A ``ModelClient`` backed by the Anthropic Messages API."""

    def __init__(self, client: Any | None = None, *, timeout_seconds: float = 300.0) -> None:
        if client is None:
            import anthropic

            # Credentials come from the environment or an SDK profile and are
            # used by the SDK. They are never stored or logged by PatchPilot,
            # which reads them only to redact them from traces and reports.
            client = anthropic.Anthropic(max_retries=0, timeout=timeout_seconds)
        self._client = client

    @staticmethod
    def _arguments(request: ModelRequest) -> dict[str, Any]:
        output_config: dict[str, Any] = {"effort": request.effort}
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        arguments: dict[str, Any] = {
            "model": request.model,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [dict(message) for message in request.messages],
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
        }
        if request.tools:
            arguments["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        return arguments

    @staticmethod
    def _failure(error: Exception) -> ModelError:
        import anthropic

        # Only the error class and status are reported. Messages can echo
        # request details.
        if isinstance(error, anthropic.APITimeoutError):
            return ModelError("timeout", "the model request timed out")
        if isinstance(error, anthropic.RateLimitError):
            return ModelError("rate_limited", "the model API rate limit was reached")
        if isinstance(error, anthropic.APIStatusError):
            detail = f"the model API returned HTTP {error.status_code}"
            # Server-side failures are worth one more attempt; client errors are not.
            return ModelError("connection" if error.status_code >= 500 else "api_error", detail)
        if isinstance(error, anthropic.APIConnectionError):
            return ModelError("connection", "the model API could not be reached")
        return ModelError("api_error", f"unexpected model client error: {type(error).__name__}")

    def count_input_tokens(self, request: ModelRequest) -> int:
        """Ask the API how many input tokens the request has, plus a safety margin."""
        import anthropic

        try:
            counted = self._client.messages.count_tokens(**self._arguments(request))
        except anthropic.AnthropicError as error:
            raise self._failure(error) from None
        except TypeError:
            # Raised by the SDK, before anything is sent, when no credential is set.
            raise ModelError("api_error", "no model credential is configured") from None
        return int(counted.input_tokens * _COUNT_MARGIN) + _COUNT_PADDING

    def complete(self, request: ModelRequest) -> ModelTurn:
        import anthropic

        try:
            response = self._client.messages.create(
                max_tokens=request.max_tokens, **self._arguments(request)
            )
        except anthropic.AnthropicError as error:
            raise self._failure(error) from None

        content = tuple(
            block.model_dump(mode="json", exclude_none=True) for block in response.content
        )
        tool_calls = tuple(
            ToolCall(id=block["id"], name=block["name"], arguments=dict(block.get("input") or {}))
            for block in content
            if block.get("type") == "tool_use"
        )
        text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
        usage = response.usage
        stop: StopReason = response.stop_reason if response.stop_reason in _KNOWN_STOPS else "other"
        return ModelTurn(
            stop_reason=stop,
            content=content,
            text=text,
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
            model=response.model,
            request_id=getattr(response, "_request_id", None),
        )
