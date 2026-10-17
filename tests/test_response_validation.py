from __future__ import annotations

from typing import Any

import pytest

from src.chat.json_healing import heal_json_text
from src.chat.response_validation import (
    ResponseValidationError,
    ResponseValidationReason,
    ResponseValidationScope,
    find_response_defect,
)
from src.models.errors import RoutingFailureAction, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.route_group_config import ResponseValidationSettings
from src.router.router import Deployment

SETTINGS = ResponseValidationSettings()
WEATHER_TOOL = {
    "type": "function",
    "function": {"name": "get_weather", "parameters": {"type": "object"}},
}


def _request(**extra: Any) -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], **extra}
    )


def _response(*messages: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": index,
                "message": {"role": "assistant", **message},
                "finish_reason": finish_reason,
            }
            for index, message in enumerate(messages)
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _tool_call(name: str = "get_weather", arguments: object = '{"city":"Rabat"}') -> dict[str, Any]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _deployment(deployment_id: str = "dep-a") -> Deployment:
    return Deployment(
        deployment_id=deployment_id,
        model_name="m",
        deltallm_params={"model": "openai/m"},
        model_info={},
    )


@pytest.mark.parametrize(
    "message",
    [{"content": ""}, {"content": "   \n"}, {"content": [{"type": "text", "text": " "}]}],
)
def test_blank_answer_is_empty_response(message: dict[str, Any]) -> None:
    assert find_response_defect(_response(message), _request(), SETTINGS) is (
        ResponseValidationReason.EMPTY_RESPONSE
    )


@pytest.mark.parametrize(
    ("message", "finish_reason"),
    [
        ({"content": "ok"}, "stop"),
        ({"content": ""}, "length"),
        ({"content": "", "reasoning_content": "thinking"}, "stop"),
        ({"content": [{"type": "image_url", "image_url": {"url": "x"}}]}, "stop"),
        ({"content": "", "refusal": "I cannot help with that."}, "stop"),
    ],
)
def test_answers_with_output_or_length_stop_are_not_empty(
    message: dict[str, Any], finish_reason: str
) -> None:
    response = _response(message, finish_reason=finish_reason)
    assert find_response_defect(response, _request(), SETTINGS) is None


def test_one_non_empty_choice_is_enough() -> None:
    response = _response({"content": ""}, {"content": "ok"})
    assert find_response_defect(response, _request(), SETTINGS) is None


@pytest.mark.parametrize("arguments", ['{"city": "Rabat"', "[1, 2]", "not json", '"text"'])
def test_tool_arguments_must_be_a_json_object(arguments: str) -> None:
    response = _response({"content": None, "tool_calls": [_tool_call(arguments=arguments)]})
    assert find_response_defect(response, _request(tools=[WEATHER_TOOL]), SETTINGS) is (
        ResponseValidationReason.INVALID_TOOL_ARGUMENTS
    )


@pytest.mark.parametrize("arguments", ['{"city":"Rabat"}', "{}", "", None])
def test_valid_or_empty_tool_arguments_pass(arguments: object) -> None:
    response = _response({"content": None, "tool_calls": [_tool_call(arguments=arguments)]})
    assert find_response_defect(response, _request(tools=[WEATHER_TOOL]), SETTINGS) is None


def test_tool_name_not_in_request_is_unknown() -> None:
    response = _response({"content": None, "tool_calls": [_tool_call(name="delete_user")]})
    assert find_response_defect(response, _request(tools=[WEATHER_TOOL]), SETTINGS) is (
        ResponseValidationReason.UNKNOWN_TOOL_NAME
    )


def test_tool_call_without_any_offered_tools_is_unknown() -> None:
    response = _response({"content": None, "tool_calls": [_tool_call()]})
    assert find_response_defect(response, _request(), SETTINGS) is (
        ResponseValidationReason.UNKNOWN_TOOL_NAME
    )


def test_unknown_tool_check_skips_requests_with_mcp_tools() -> None:
    response = _response({"content": None, "tool_calls": [_tool_call(name="docs.search")]})
    request = _request(tools=[WEATHER_TOOL, {"type": "mcp", "server": "docs"}])
    assert find_response_defect(response, request, SETTINGS) is None


@pytest.mark.parametrize("response_type", ["json_object", "json_schema"])
def test_json_mode_requires_json_content(response_type: str) -> None:
    request = _request(response_format={"type": response_type})
    assert find_response_defect(_response({"content": '{"a": 1}'}), request, SETTINGS) is None
    assert find_response_defect(_response({"content": "Sure! {"}), request, SETTINGS) is (
        ResponseValidationReason.INVALID_JSON_OUTPUT
    )


def test_json_mode_ignores_truncated_answers_and_text_mode() -> None:
    truncated = _response({"content": '{"a": '}, finish_reason="length")
    request = _request(response_format={"type": "json_object"})
    assert find_response_defect(truncated, request, SETTINGS) is None
    text_request = _request(response_format={"type": "text"})
    assert find_response_defect(_response({"content": "plain"}), text_request, SETTINGS) is None


def test_each_check_can_be_disabled() -> None:
    disabled = ResponseValidationSettings(
        empty_response=False,
        invalid_tool_arguments=False,
        unknown_tool_name=False,
        invalid_json_output=False,
    )
    request = _request(response_format={"type": "json_object"})
    assert find_response_defect(_response({"content": ""}), request, disabled) is None
    bad_tool = _response({"content": None, "tool_calls": [_tool_call(name="x", arguments="{")]})
    assert find_response_defect(bad_tool, request, disabled) is None
    off = ResponseValidationSettings(enabled=False)
    assert find_response_defect(_response({"content": ""}), request, off) is None


def test_settings_reject_unknown_keys() -> None:
    with pytest.raises(ValueError):
        ResponseValidationSettings.model_validate({"empty_responses": True})


def test_validation_error_retries_without_affecting_health() -> None:
    error = ResponseValidationError(ResponseValidationReason.EMPTY_RESPONSE)
    assert isinstance(error, ServiceUnavailableError)
    assert error.status_code == 503
    assert error.affects_deployment_health is False
    assert error.routing_failure_action is RoutingFailureAction.RETRY_OR_NEXT


async def _result(payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    return payload, 12.0


@pytest.mark.asyncio
async def test_scope_returns_rejected_answer_when_attempts_run_out() -> None:
    scope = ResponseValidationScope(_request(), SETTINGS)
    empty = _response({"content": ""})
    deployment = _deployment()

    async def failover() -> tuple[tuple[dict[str, Any], float], Deployment]:
        try:
            await scope.checked(deployment, _result(empty))
        except ResponseValidationError as exc:
            # A later attempt timing out still ends with the earlier answer.
            raise TimeoutError() from exc
        raise AssertionError("validation must reject the empty answer")

    (payload, latency), served = await scope.run(failover())

    assert payload is empty
    assert latency == 12.0
    assert served is deployment
    assert scope.response_headers() == {"x-deltallm-response-validation": "empty_response"}


@pytest.mark.asyncio
async def test_scope_raises_when_configured_to_error() -> None:
    scope = ResponseValidationScope(_request(), ResponseValidationSettings(on_exhausted="error"))

    async def failover() -> tuple[tuple[dict[str, Any], float], Deployment]:
        await scope.checked(_deployment(), _result(_response({"content": ""})))
        raise AssertionError("unreachable")

    with pytest.raises(ResponseValidationError):
        await scope.run(failover())
    assert scope.response_headers() == {}


@pytest.mark.asyncio
async def test_scope_keeps_errors_without_a_rejected_answer() -> None:
    scope = ResponseValidationScope(_request(), SETTINGS)

    async def failover() -> tuple[tuple[dict[str, Any], float], Deployment]:
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await scope.run(failover())


@pytest.mark.asyncio
async def test_scope_passes_valid_answers_through() -> None:
    scope = ResponseValidationScope(_request(), SETTINGS)
    ok = _response({"content": "ok"})
    assert await scope.checked(_deployment(), _result(ok)) == (ok, 12.0)
    assert scope.response_headers() == {}


@pytest.mark.parametrize(
    ("raw", "healed"),
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        (
            'Here is the JSON: {"a": [1, 2,], "b": "x}"} Hope this helps!',
            '{"a": [1, 2], "b": "x}"}',
        ),
        ('{"a": 1,\n}', '{"a": 1}'),
        ('Sure {"q": "say \\"hi\\""}', '{"q": "say \\"hi\\""}'),
    ],
)
def test_heal_json_text_repairs_wrapping_and_trailing_commas(raw: str, healed: str) -> None:
    assert heal_json_text(raw, require_object=True) == healed


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1', "no json here", '{"a": 1,, "b": 2}', '{"a": ]', "[1, 2]"],
)
def test_heal_json_text_refuses_unsafe_repairs(raw: str) -> None:
    assert heal_json_text(raw, require_object=True) is None


def test_heal_json_text_accepts_arrays_for_json_schema() -> None:
    assert heal_json_text("Result: [1, 2,]", require_object=False) == "[1, 2]"


@pytest.mark.asyncio
async def test_scope_heals_json_answers_when_enabled() -> None:
    settings = ResponseValidationSettings(json_healing=True)
    scope = ResponseValidationScope(_request(response_format={"type": "json_object"}), settings)
    fenced = _response({"content": '```json\n{"ok": true}\n```'})

    payload, _ = await scope.checked(_deployment(), _result(fenced))

    assert payload["choices"][0]["message"]["content"] == '{"ok": true}'
    assert scope.response_headers() == {"x-deltallm-response-healed": "json"}


@pytest.mark.asyncio
async def test_scope_does_not_heal_by_default() -> None:
    scope = ResponseValidationScope(_request(response_format={"type": "json_object"}), SETTINGS)
    with pytest.raises(ResponseValidationError):
        await scope.checked(_deployment(), _result(_response({"content": "```json\n{}\n```"})))


def test_heal_json_text_slices_large_answers_without_scanning() -> None:
    body = '{"items": [' + ",".join(['"x"'] * 20_000) + "]}"
    assert heal_json_text(f"```json\n{body}\n```", require_object=True) == body
    trailing = '{"items": [' + ",".join(['"x"'] * 20_000) + ",]}"
    assert heal_json_text(trailing, require_object=True) is None
