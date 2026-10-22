"""Apply a deployment's request shaping to one chat attempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
from typing import Any

from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.chat_upstream import ChatUpstream
from src.providers.request_shaping import (
    RequestShaping,
    RequestShapingError,
    apply_body_shaping,
    parse_request_shaping,
    shaped_headers,
    upstream_session_id,
)
from src.router.selection.session import SelectorSessionSource
from src.router.selection.session_identity import selector_session_identity

_UNPARSED = object()


def deployment_request_shaping(deployment: Any) -> RequestShaping | None:
    shaping = getattr(deployment, "request_shaping", _UNPARSED)
    if shaping is _UNPARSED:
        try:
            shaping = parse_request_shaping(getattr(deployment, "deltallm_params", None) or {})
        except RequestShapingError as exc:
            shaping = exc
    if isinstance(shaping, RequestShapingError):
        # Fail closed: a broken config may name a proxy that egress policy requires.
        raise ServiceUnavailableError(
            message="Model deployment request shaping is misconfigured",
            code="request_shaping_invalid",
        )
    return shaping


def shape_chat_attempt(
    request: Any,
    payload: ChatCompletionRequest,
    deployment: Any,
    upstream: ChatUpstream,
    upstream_payload: dict[str, Any],
) -> ChatUpstream:
    """Shape the upstream body in place and return the upstream with its added headers."""
    shaping = deployment_request_shaping(deployment)
    if shaping is None:
        return upstream
    apply_body_shaping(upstream_payload, shaping)
    if shaping.outbound_proxy is not None:
        configured = getattr(getattr(request, "app", None), "state", None)
        names = getattr(configured, "upstream_proxy_names", None)
        if names is not None and shaping.outbound_proxy not in names:
            raise ServiceUnavailableError(
                message="Model deployment names an outbound proxy that is not configured",
                code="outbound_proxy_unavailable",
            )
    if not shaping.shapes_headers:
        return upstream
    client_headers: Mapping[str, str] = getattr(request, "headers", None) or {}
    added = shaped_headers(
        shaping,
        client_headers=client_headers,
        session_id=lambda: _conversation_session_id(request, client_headers, payload),
        reserved=upstream.headers,
    )
    if not added:
        return upstream
    return replace(upstream, headers={**upstream.headers, **added})


def _conversation_session_id(
    request: Any, headers: Mapping[str, str], payload: ChatCompletionRequest
) -> str | None:
    """The same conversation gets the same ID on every turn, scoped to the API key.

    Explicit session headers and metadata win. Otherwise the opening of the conversation
    (system prompt and first user message) identifies it, so the first turn already has
    the ID that later turns send.
    """
    auth = getattr(getattr(request, "state", None), "user_api_key", None)
    api_key = str(getattr(auth, "api_key", "") or "")
    if not api_key:
        return None
    identity = selector_session_identity(headers=headers, payload=payload, api_key=api_key)
    if identity is not None and identity.source is not SelectorSessionSource.FINGERPRINT:
        return upstream_session_id(identity.digest)
    first_user = next((m for m in payload.messages if m.role == "user"), None)
    if first_user is None:
        return None
    system = next((m for m in payload.messages if m.role == "system"), None)
    material = json.dumps(
        [payload.user, system.content if system is not None else None, first_user.content],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return upstream_session_id(f"{api_key}\0opening\0{material}")
