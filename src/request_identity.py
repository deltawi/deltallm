"""Bound request correlation IDs without using them as economic identities."""

from __future__ import annotations

from typing import TypeGuard
from uuid import uuid4


def valid_request_id(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and all("!" <= character <= "~" for character in value)
    )


def resolve_request_id(value: str | None) -> str:
    return value if valid_request_id(value) else uuid4().hex
