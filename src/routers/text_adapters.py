from __future__ import annotations

import json
from typing import Any

from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest, CompletionsRequest


def _copy_explicit_fields(
    source: CompletionsRequest, target: dict[str, Any], field_map: dict[str, str]
) -> None:
    fields_set = getattr(source, "model_fields_set", set())
    for source_field, target_field in field_map.items():
        if source_field in fields_set:
            target[target_field] = getattr(source, source_field)


def completions_to_chat_request(payload: CompletionsRequest) -> ChatCompletionRequest:
    if payload.echo:
        raise InvalidRequestError(message="`echo=true` is not supported on this gateway")
    if payload.best_of and payload.best_of > 1:
        raise InvalidRequestError(message="`best_of` is not supported on this gateway")
    if payload.logprobs is not None:
        raise InvalidRequestError(message="`logprobs` is not supported on this gateway")
    if payload.suffix:
        raise InvalidRequestError(message="`suffix` is not supported on this gateway")

    if isinstance(payload.prompt, list):
        prompt_text = "\n".join(str(item) for item in payload.prompt)
    else:
        prompt_text = payload.prompt

    data: dict[str, Any] = {
        "model": payload.model,
        "messages": [{"role": "user", "content": prompt_text}],
    }
    _copy_explicit_fields(
        payload,
        data,
        {
            "temperature": "temperature",
            "max_tokens": "max_tokens",
            "top_p": "top_p",
            "n": "n",
            "stream": "stream",
            "stop": "stop",
            "presence_penalty": "presence_penalty",
            "frequency_penalty": "frequency_penalty",
            "user": "user",
            "metadata": "metadata",
        },
    )
    return ChatCompletionRequest.model_validate(data)


def chat_response_to_completions_response(chat_payload: dict[str, Any]) -> dict[str, Any]:
    choices_out: list[dict[str, Any]] = []
    for choice in chat_payload.get("choices", []):
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = str(content or "")
        choices_out.append(
            {
                "text": text,
                "index": int(choice.get("index", 0) or 0),
                "logprobs": None,
                "finish_reason": choice.get("finish_reason"),
            }
        )
    return {
        "id": chat_payload.get("id"),
        "object": "text_completion",
        "created": chat_payload.get("created"),
        "model": chat_payload.get("model"),
        "choices": choices_out,
        "usage": chat_payload.get("usage")
        or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_chat_to_completions_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return line
    payload = line[len("data:") :].strip()
    if payload == "[DONE]":
        return "data: [DONE]"
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return line

    choices = chunk.get("choices") or []
    out_choices: list[dict[str, Any]] = []
    for choice in choices:
        delta = choice.get("delta") or {}
        out_choices.append(
            {
                "text": delta.get("content", ""),
                "index": int(choice.get("index", 0) or 0),
                "logprobs": None,
                "finish_reason": choice.get("finish_reason"),
            }
        )

    out = {
        "id": chunk.get("id"),
        "object": "text_completion",
        "created": chunk.get("created"),
        "model": chunk.get("model"),
        "choices": out_choices,
    }
    return f"data: {json.dumps(out, separators=(',', ':'))}"
