"""Keep audit payloads small enough for the request-path acceptance budget.

Requests such as OCR, vision and audio carry multi-megabyte base64 data inline.
Audit acceptance ships the payload to PostgreSQL in one statement under a short
deadline, so inline binary data is replaced by a short placeholder that keeps
its media type, size and SHA-256. A payload that is still too large afterwards
is replaced as a whole.
"""

from __future__ import annotations

from hashlib import sha256
import re
from typing import Any

DEFAULT_AUDIT_PAYLOAD_MAX_BYTES = 1_048_576

# Shorter strings are kept as-is: they are cheap and may be meaningful text.
_INLINE_DATA_MIN_CHARS = 1024
_DATA_URL = re.compile(r"data:([\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[^;,]*)*;base64,", re.ASCII)
_BASE64 = re.compile(r"[A-Za-z0-9+/=_\-\r\n]+", re.ASCII)
# Rough JSON overhead per container entry and per scalar.
_ENTRY_OVERHEAD = 4


def compact_audit_content(content: Any, *, max_bytes: int) -> tuple[Any, bool]:
    """Return (content, changed) with inline binary data and oversize payloads omitted."""

    compacted, size, changed = _compact(content)
    if size <= max_bytes:
        return compacted, changed
    return {"omitted": "payload_too_large", "approx_size_bytes": size, "max_bytes": max_bytes}, True


def _compact(value: Any) -> tuple[Any, int, bool]:
    if isinstance(value, str):
        placeholder = _inline_data_placeholder(value)
        if placeholder is not None:
            return placeholder, len(placeholder) + 2, True
        return value, len(value) + 2, False
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        size = 2
        changed = False
        for key, item in value.items():
            compacted, item_size, item_changed = _compact(item)
            result[key] = compacted
            size += len(str(key)) + item_size + _ENTRY_OVERHEAD
            changed = changed or item_changed
        return (result if changed else value), size, changed
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        size = 2
        changed = False
        for item in value:
            compacted, item_size, item_changed = _compact(item)
            items.append(compacted)
            size += item_size + 1
            changed = changed or item_changed
        return (items if changed else value), size, changed
    return value, _ENTRY_OVERHEAD, False


def _inline_data_placeholder(value: str) -> str | None:
    if len(value) < _INLINE_DATA_MIN_CHARS:
        return None
    match = _DATA_URL.match(value)
    if match is not None:
        media_type = match.group(1) or "application/octet-stream"
        if _BASE64.fullmatch(value, match.end()) is None:
            return None
    elif _BASE64.fullmatch(value) is not None:
        media_type = "base64"
    else:
        return None
    digest = sha256(value.encode()).hexdigest()
    return f"[omitted inline data: {media_type}, {len(value)} chars, sha256={digest}]"
