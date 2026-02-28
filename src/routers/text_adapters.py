from __future__ import annotations

import json
from typing import Any

from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest, CompletionsRequest, ResponsesRequest


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

    return ChatCompletionRequest(
        model=payload.model,
        messages=[{"role": "user", "content": prompt_text}],
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        top_p=payload.top_p,
        n=payload.n,
        stream=payload.stream,
        stop=payload.stop,
        presence_penalty=payload.presence_penalty,
        frequency_penalty=payload.frequency_penalty,
        user=payload.user,
        metadata=payload.metadata,
    )


def responses_to_chat_request(payload: ResponsesRequest) -> ChatCompletionRequest:
    messages: list[dict[str, Any]] = []
    if payload.instructions:
        messages.append({"role": "system", "content": payload.instructions})

    if isinstance(payload.input, str):
        messages.append({"role": "user", "content": payload.input})
    elif isinstance(payload.input, list):
        text_parts: list[str] = []
        for item in payload.input:
            if isinstance(item, str):
                text_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in {"input_text", "text"} and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
        if text_parts:
            messages.append({"role": "user", "content": "\n".join(text_parts)})
    if not messages:
        raise InvalidRequestError(message="Responses `input` could not be translated into chat messages")

    return ChatCompletionRequest(
        model=payload.model,
        messages=messages,
        temperature=payload.temperature,
        max_tokens=payload.max_output_tokens,
        top_p=payload.top_p,
        stream=payload.stream,
        tools=payload.tools,
        tool_choice=payload.tool_choice,
        user=payload.user,
        metadata=payload.metadata,
    )


def chat_response_to_completions_response(chat_payload: dict[str, Any]) -> dict[str, Any]:
    choices_out: list[dict[str, Any]] = []
    for choice in chat_payload.get("choices", []):
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
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
        "usage": chat_payload.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def chat_response_to_responses_response(chat_payload: dict[str, Any]) -> dict[str, Any]:
    first_choice = ((chat_payload.get("choices") or [{}])[0]) or {}
    message = first_choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    else:
        text = str(content or "")

    response_id = str(chat_payload.get("id") or "")
    return {
        "id": response_id,
        "object": "response",
        "created_at": chat_payload.get("created"),
        "model": chat_payload.get("model"),
        "output": [
            {
                "id": f"msg_{response_id}" if response_id else "msg_0",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": chat_payload.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "status": "completed",
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


def stream_chat_to_responses_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return line
    payload = line[len("data:") :].strip()
    if payload == "[DONE]":
        return "data: [DONE]"
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return line

    first_choice = ((chunk.get("choices") or [{}])[0]) or {}
    delta = first_choice.get("delta") or {}
    text = str(delta.get("content") or "")
    out = {
        "id": chunk.get("id"),
        "object": "response.output_text.delta",
        "created_at": chunk.get("created"),
        "model": chunk.get("model"),
        "delta": text,
    }
    return f"data: {json.dumps(out, separators=(',', ':'))}"
