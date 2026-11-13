import pytest

from patchpilot.llm.base import ModelError, ModelRequest, Usage
from patchpilot.llm.fake import ScriptedModel, ScriptExhaustedError, final_turn, tool_turn

REQUEST = ModelRequest(
    model="m", system="s", messages=({"role": "user", "content": "hi"},), max_tokens=100
)


def test_turns_are_returned_in_order_and_requests_recorded() -> None:
    model = ScriptedModel([tool_turn(("list_files", {})), final_turn({"ok": True})])

    first = model.complete(REQUEST)
    second = model.complete(REQUEST)

    assert first.stop_reason == "tool_use" and first.tool_calls[0].name == "list_files"
    assert first.content[0]["id"] == first.tool_calls[0].id
    assert second.stop_reason == "end_turn" and second.text == '{"ok": true}'
    assert len(model.requests) == 2 and "hi" in model.sent_text()


def test_a_scripted_error_is_raised_and_a_callable_sees_the_request() -> None:
    model = ScriptedModel(
        [ModelError("timeout", "slow"), lambda request: final_turn(request.model)]
    )

    with pytest.raises(ModelError) as caught:
        model.complete(REQUEST)

    assert caught.value.kind == "timeout"
    assert model.complete(REQUEST).text == "m"


def test_asking_for_more_turns_than_scripted_fails_loudly() -> None:
    model = ScriptedModel([final_turn("x")])
    model.complete(REQUEST)

    with pytest.raises(ScriptExhaustedError):
        model.complete(REQUEST)


def test_usage_adds_up() -> None:
    total = Usage(input_tokens=1, output_tokens=2).plus(
        Usage(input_tokens=10, cache_read_input_tokens=5)
    )

    assert (total.input_tokens, total.output_tokens, total.cache_read_input_tokens) == (11, 2, 5)
