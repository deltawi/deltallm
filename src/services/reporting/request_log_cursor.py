"""Opaque keyset cursor for request log pages ordered by start time and ID."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
import json
from typing import Any


class InvalidRequestLogCursor(ValueError):
    """The client sent a cursor that this module did not issue."""


def encode_request_log_cursor(start_time: Any, row_id: Any) -> str:
    if isinstance(start_time, datetime):
        timestamp = start_time.isoformat()
    else:
        timestamp = str(start_time or "").strip()
    identifier = str(row_id or "").strip()
    if not timestamp or not identifier:
        raise ValueError("Request log cursor rows require start_time and id")
    payload = json.dumps(
        {"start_time": timestamp, "id": identifier},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_request_log_cursor(value: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        timestamp = datetime.fromisoformat(str(payload["start_time"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        identifier = str(payload["id"]).strip()
        if not identifier:
            raise ValueError("empty cursor id")
        return timestamp.astimezone(UTC), identifier
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidRequestLogCursor("Invalid request log cursor") from exc


def encode_sorted_request_log_cursor(sort: str, value: Any, row_id: Any) -> str:
    """Cursor for pages ordered by a value (cost or duration) and then ID."""

    identifier = str(row_id or "").strip()
    if not identifier or isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Sorted request log cursor rows require a number and an id")
    payload = json.dumps(
        {"sort": sort, "value": value, "id": identifier},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_sorted_request_log_cursor(value: str, sort: str) -> tuple[float | int, str]:
    """Decode a cursor that ``encode_sorted_request_log_cursor`` issued for ``sort``."""

    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        if payload["sort"] != sort:
            raise ValueError("cursor is for another sort order")
        number = payload["value"]
        if isinstance(number, bool) or not isinstance(number, int | float):
            raise ValueError("cursor value is not a number")
        identifier = str(payload["id"]).strip()
        if not identifier:
            raise ValueError("empty cursor id")
        return number, identifier
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidRequestLogCursor("Invalid request log cursor") from exc
