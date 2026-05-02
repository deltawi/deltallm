from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from src.batch.endpoints import (
    BATCH_ENDPOINT_CHAT_COMPLETIONS,
    BATCH_ENDPOINT_EMBEDDINGS,
    SUPPORTED_BATCH_ENDPOINT_SET,
    supported_batch_endpoints_display,
)
from src.models.requests import ChatCompletionRequest, EmbeddingRequest, MCPToolDefinition
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.model_visibility import CallableTargetPolicyMode, ensure_batch_model_allowed


@dataclass(frozen=True, slots=True)
class ParsedBatchInputLine:
    line_number: int
    custom_id: str
    request_body: dict[str, Any]
    model: str


def parse_batch_input_line(
    raw_line: str,
    *,
    line_number: int,
    endpoint: str,
    auth: UserAPIKeyAuth,
    seen_custom_ids: set[str],
    callable_target_grant_service: CallableTargetGrantService | None,
    callable_target_scope_policy_mode: CallableTargetPolicyMode | str,
) -> ParsedBatchInputLine | None:
    endpoint = str(endpoint or "").strip()
    if endpoint not in SUPPORTED_BATCH_ENDPOINT_SET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported batch endpoint '{endpoint}'. Supported endpoints: {supported_batch_endpoints_display()}",
        )

    line = raw_line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSONL at line {line_number}: {exc.msg}",
        ) from exc
    if not isinstance(obj, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} must be an object")

    method = str(obj.get("method") or "POST").strip().upper()
    if method != "POST":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} method must be POST")

    custom_id = str(obj.get("custom_id") or "").strip()
    if not custom_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} missing custom_id")
    if custom_id in seen_custom_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Duplicate custom_id at line {line_number}")
    seen_custom_ids.add(custom_id)

    url = str(obj.get("url") or endpoint)
    if url != endpoint:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} must target {endpoint}")

    body = obj.get("body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} missing body")

    request_body, model = _validate_batch_request_body(body, endpoint=endpoint, line_number=line_number)
    ensure_batch_model_allowed(
        auth,
        model,
        callable_target_grant_service=callable_target_grant_service,
        policy_mode=callable_target_scope_policy_mode,
    )
    return ParsedBatchInputLine(
        line_number=line_number,
        custom_id=custom_id,
        request_body=request_body,
        model=model,
    )


def _validate_batch_request_body(
    body: dict[str, Any],
    *,
    endpoint: str,
    line_number: int,
) -> tuple[dict[str, Any], str]:
    try:
        if endpoint == BATCH_ENDPOINT_EMBEDDINGS:
            validated = EmbeddingRequest.model_validate(body)
            return validated.model_dump(exclude_none=True), validated.model
        if endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS:
            validated = ChatCompletionRequest.model_validate(body)
            _validate_batch_chat_request(validated, line_number=line_number)
            return validated.model_dump(exclude_none=True), validated.model
    except ValidationError as exc:
        message = exc.errors()[0].get("msg") if exc.errors() else str(exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request body at line {line_number}: {message}",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported batch endpoint '{endpoint}'. Supported endpoints: {supported_batch_endpoints_display()}",
    )


def _validate_batch_chat_request(payload: ChatCompletionRequest, *, line_number: int) -> None:
    if payload.stream is True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Line {line_number} chat batch requests support non-streaming requests only; stream must be false",
        )
    if any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or []):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Line {line_number} MCP tools are not supported in batch chat yet",
        )
