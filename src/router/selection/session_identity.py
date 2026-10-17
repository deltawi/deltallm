from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json

from src.models.requests import ChatCompletionRequest
from src.router.selection.session import SelectorSessionIdentity, SelectorSessionSource

MAX_SESSION_ID_LENGTH = 255
MAX_AGENT_METADATA_BYTES = 4_096
SESSION_AFFINITY_HEADER = "x-deltallm-session-affinity"

# First match wins. An explicit header always wins over agent headers and the fallbacks.
_SESSION_HEADERS: tuple[tuple[str, SelectorSessionSource], ...] = (
    ("x-deltallm-session-id", SelectorSessionSource.HEADER),
    ("x-session-id", SelectorSessionSource.HEADER),
    ("x-claude-code-session-id", SelectorSessionSource.AGENT),
)
_CODEX_TURN_HEADER = "x-codex-turn-metadata"


def selector_session_identity(
    *,
    headers: Mapping[str, str],
    payload: ChatCompletionRequest,
    api_key: str,
) -> SelectorSessionIdentity | None:
    """Find the session a request belongs to, scoped to the calling API key.

    An oversized or blank explicit ID gives no session instead of a weaker fallback,
    so a client error never joins unrelated conversations.
    """
    if headers.get(SESSION_AFFINITY_HEADER, "").strip().lower() == "off":
        return None
    for name, source in _SESSION_HEADERS:
        if name in headers:
            return _identity(api_key, source, headers[name])
    codex = headers.get(_CODEX_TURN_HEADER)
    if codex is not None:
        return _identity(api_key, SelectorSessionSource.AGENT, _codex_session(codex))
    metadata = payload.metadata or {}
    if "session_id" in metadata:
        value = metadata["session_id"]
        return _identity(
            api_key, SelectorSessionSource.METADATA, value if isinstance(value, str) else None
        )
    return _fingerprint(api_key, payload)


def _identity(
    api_key: str, source: SelectorSessionSource, value: str | None
) -> SelectorSessionIdentity | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > MAX_SESSION_ID_LENGTH:
        return None
    return SelectorSessionIdentity(source=source, digest=_digest(api_key, source, value))


def _codex_session(raw: str) -> str | None:
    if len(raw.encode("utf-8")) > MAX_AGENT_METADATA_BYTES:
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    value = document.get("session_id") if isinstance(document, dict) else None
    return value if isinstance(value, str) else None


def _fingerprint(api_key: str, payload: ChatCompletionRequest) -> SelectorSessionIdentity | None:
    """The opening of a conversation stays the same on every later request.

    The first answer is part of it, because many conversations of one app can share
    a system prompt and a first user message. A sampled first answer, or the random
    IDs of its tool calls, keeps those apart. A first turn has no answer yet, so it
    has no fingerprint session. The OpenAI ``user`` field keeps end users apart.
    """
    first_user = next((m for m in payload.messages if m.role == "user"), None)
    first_answer = next((m for m in payload.messages if m.role == "assistant"), None)
    if first_user is None or first_answer is None:
        return None
    system = next((m for m in payload.messages if m.role == "system"), None)
    material = json.dumps(
        [
            payload.user,
            system.content if system is not None else None,
            first_user.content,
            first_answer.content,
            first_answer.tool_calls,
        ],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return SelectorSessionIdentity(
        source=SelectorSessionSource.FINGERPRINT,
        digest=_digest(api_key, SelectorSessionSource.FINGERPRINT, material),
    )


def _digest(api_key: str, source: SelectorSessionSource, value: str) -> str:
    material = f"deltallm-selector-session-v1\0{api_key}\0{source.value}\0{value}"
    return sha256(material.encode("utf-8")).hexdigest()
