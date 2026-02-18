from __future__ import annotations

from datetime import UTC, datetime

from src.callbacks.payload import build_standard_logging_payload


def test_payload_builder_respects_turn_off_message_logging() -> None:
    payload = build_standard_logging_payload(
        call_type="completion",
        request_id="req-1",
        model="gpt-4o-mini",
        deployment_model="openai/gpt-4o-mini",
        request_payload={
            "messages": [{"role": "user", "content": "secret"}],
            "metadata": {"tags": ["a", "b"]},
            "stream": False,
        },
        response_obj={"usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
        user_api_key_dict={"api_key": "hashed", "user_id": "u1", "team_id": "team-1"},
        start_time=datetime.now(tz=UTC),
        end_time=datetime.now(tz=UTC),
        api_base="https://api.openai.com/v1",
        turn_off_message_logging=True,
    )

    assert payload.messages is None
    assert payload.tags == ["a", "b"]
    assert payload.usage.total_tokens == 5
