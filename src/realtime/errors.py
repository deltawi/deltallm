from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import uuid4


def new_event_id() -> str:
    return f"event_{uuid4().hex}"


def safe_identifier(value: object, headers: Mapping[str, str]) -> str | None:
    """Keep bounded opaque IDs, excluding credentials and diagnostic text."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) is None:
        return None
    for credential in headers.values():
        secret = credential.removeprefix("Bearer ")
        if secret and secret in value:
            return None
    return value


def sanitize_error(
    error: object, headers: Mapping[str, str], *, default_type: str
) -> dict[str, object]:
    source = error if isinstance(error, dict) else {}
    kind = source.get("type")
    result: dict[str, object] = {
        "type": kind if kind in ("invalid_request_error", "server_error") else default_type,
        "code": "upstream_error",
        "message": "The upstream could not complete a Realtime operation",
    }
    event_id = safe_identifier(source.get("event_id"), headers)
    if event_id is not None:
        result["event_id"] = event_id
    return result


class RealtimeError(Exception):
    """Only gateway-owned, public messages may cross this boundary."""

    def __init__(self, code: str, message: str, *, close_code: int = 1008) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code
        self.event_id = new_event_id()
        self.client_event_id: str | None = None

    @classmethod
    def admission_denied(cls) -> RealtimeError:
        return cls("admission_denied", "Realtime admission denied")

    def event(self) -> dict[str, object]:
        error = {"type": "invalid_request_error", "code": self.code, "message": self.message}
        if self.client_event_id is not None:
            error["event_id"] = self.client_event_id
        return {"type": "error", "event_id": self.event_id, "error": error}
