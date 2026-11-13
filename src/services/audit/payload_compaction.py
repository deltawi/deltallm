"""Keep audit payloads small enough for the request-path acceptance budget.

Requests such as OCR, vision and audio carry multi-megabyte base64 data inline.
Audit acceptance ships the payload to PostgreSQL in one statement under a short
deadline, so inline binary data is replaced by a short placeholder that keeps
its media type, size and SHA-256. A payload that is still too large afterwards
is replaced as a whole.
"""

from __future__ import annotations

from hashlib import sha256
from json.encoder import encode_basestring_ascii
import re
from typing import Any

DEFAULT_AUDIT_PAYLOAD_MAX_BYTES = 1_048_576

# Shorter strings are kept as-is: they are cheap and may be meaningful text.
_INLINE_DATA_MIN_CHARS = 1024
_DATA_URL = re.compile(r"data:([\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[^;,]*)*;base64,", re.ASCII)
_BASE64 = re.compile(r"[A-Za-z0-9+/=_\-\r\n]+", re.ASCII)
# A data URL's header (media type and parameters, up to ";base64,") must end within
# this many characters. The header regex then never scans a long string.
_DATA_URL_MAX_HEADER_CHARS = 1024
# Long strings are scanned and hashed in slices of this many characters. Each slice
# is one C call that holds the GIL, so compaction in a worker thread never stops the
# serving event loop for long (a whole 24 MiB string took about 200 ms).
_SCAN_CHUNK_CHARS = 256 * 1024
# The ASCII characters that ``json.dumps`` escapes.
_JSON_ESCAPED_ASCII = re.compile(r'["\\\x00-\x1f\x7f]')
# Larger integers are replaced: their decimal text has no bound and costs time
# quadratic in its length (a 4,096-digit integer takes about 0.15 ms to print).
_MAX_INTEGER_BITS = 1024
# Rough JSON overhead per container entry and per scalar.
ENTRY_OVERHEAD = 4
# The smallest size this module gives a value: an empty string, list or dict.
MIN_VALUE_SIZE = 2
# The longest JSON text of a finite float, such as -1.7976931348623157e+308.
MAX_FLOAT_CHARS = 24
# The shortest inline-data placeholder: the shortest media type ("a/b"), the
# shortest length and a SHA-256. A string can be no smaller than this or its own
# length, so a string with less room than that is over the limit without a scan.
_MIN_PLACEHOLDER_CHARS = len(
    f"[omitted inline data: a/b, {_INLINE_DATA_MIN_CHARS} chars, sha256={'0' * 64}]"
)


def compact_audit_content(content: Any, *, max_bytes: int) -> tuple[Any, bool]:
    """Return (content, changed) with inline binary data and oversize payloads omitted."""

    compacted, size, changed = _compact(content, stop=max_bytes)
    if size <= max_bytes:
        return compacted, changed
    return oversize_placeholder(size, max_bytes=max_bytes), True


def oversize_placeholder(size: int, *, max_bytes: int) -> dict[str, Any]:
    """Return what replaces a payload whose compacted size is over ``max_bytes``.

    ``size`` is counted only until it is over ``max_bytes``, so it is a lower bound.
    """

    return {"omitted": "payload_too_large", "approx_size_bytes": size, "max_bytes": max_bytes}


def _compact(value: Any, *, stop: int) -> tuple[Any, int, bool]:
    """Return (value, size, changed), and stop counting once the size is over ``stop``.

    ``stop`` is what is left of the payload limit for this value: each child gets what
    its container has not used yet. So the whole traversal ends as soon as the payload
    is known to be over its limit, and no later string is scanned, hashed or measured.
    The caller then replaces the payload, so the value returned with a size over
    ``stop`` is not used, and that size is only a lower bound. A size is over ``stop``
    exactly when the full count would be.
    """

    if isinstance(value, str):
        # A lower bound of its size, kept or replaced: a string that cannot fit is
        # never scanned or hashed.
        floor = min(len(value), _MIN_PLACEHOLDER_CHARS) + 2
        if floor > stop:
            return value, floor, False
        placeholder = _inline_data_placeholder(value)
        if placeholder is not None:
            return placeholder, len(placeholder) + 2, True
        return value, _text_size(value, stop=stop), False
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        size = 2
        changed = False
        for key, item in value.items():
            if isinstance(key, str):
                size += _text_size(key, stop=stop - size) - 2
            else:
                size += len(str(key))
            size += ENTRY_OVERHEAD
            if size > stop:
                return value, size, changed
            compacted, item_size, item_changed = _compact(item, stop=stop - size)
            size += item_size
            changed = changed or item_changed
            if size > stop:
                return value, size, changed
            result[key] = compacted
        return (result if changed else value), size, changed
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        size = 2
        changed = False
        for item in value:
            size += 1
            if size > stop:
                return value, size, changed
            compacted, item_size, item_changed = _compact(item, stop=stop - size)
            size += item_size
            changed = changed or item_changed
            if size > stop:
                return value, size, changed
            items.append(compacted)
        return (items if changed else value), size, changed
    if type(value) is int:
        bits = value.bit_length()
        if bits > _MAX_INTEGER_BITS:
            placeholder = f"[omitted large integer: {bits} bits]"
            return placeholder, len(placeholder) + 2, True
        # An upper bound of its digits and sign.
        return value, max(ENTRY_OVERHEAD, (bits * 31) // 100 + 3), False
    if type(value) is float:
        return value, MAX_FLOAT_CHARS, False
    return value, ENTRY_OVERHEAD, False


def _inline_data_placeholder(value: str) -> str | None:
    if len(value) < _INLINE_DATA_MIN_CHARS:
        return None
    match = _DATA_URL.match(value, 0, _DATA_URL_MAX_HEADER_CHARS)
    if match is not None:
        media_type = match.group(1) or "application/octet-stream"
        if not _is_base64(value, match.end()):
            return None
    elif _is_base64(value, 0):
        media_type = "base64"
    else:
        return None
    return f"[omitted inline data: {media_type}, {len(value)} chars, sha256={_sha256(value)}]"


def json_text_chars(value: str, *, stop: int | None = None) -> int:
    """Return the exact length of ``value`` as JSON text (``json.dumps``), without quotes.

    Escapes count: a quote, a backslash or a control character is two or six
    characters, and a character outside ASCII is six (twelve outside the BMP). A caller
    that cuts text to the payload limit thus cuts it as the exact size check of the
    record measures it. With ``stop``, the work is bounded by ``stop``: text longer than
    ``stop`` is not scanned, and the scan ends at the first slice that takes the size
    over ``stop``. Then the result is only known to be more than ``stop``.
    """

    limit = len(value) * 12 if stop is None else max(0, stop)
    if len(value) > limit:
        # Escapes only make the text longer, so the length is a lower bound.
        return len(value)
    if value.isascii() and _JSON_ESCAPED_ASCII.search(value) is None:
        return len(value)
    return _encoded_size(value, stop=limit + 2) - 2


def _text_size(value: str, *, stop: int) -> int:
    """Return the length of ``value`` as JSON text, as ``json.dumps`` writes it.

    ``json.dumps`` writes each non-ASCII character as a ``\\uXXXX`` escape (six
    characters, twelve outside the BMP), so such a string is measured by encoding it
    slice by slice, and only until its size is over ``stop``. ASCII text is counted by
    its length: its escapes (quotes, backslashes, control characters) are few, and the
    exact size check of each record covers them.
    """

    if value.isascii() or len(value) + 2 > stop:
        # Escapes only make the text longer, so the length is a lower bound.
        return len(value) + 2
    return _encoded_size(value, stop=stop)


def _encoded_size(value: str, *, stop: int) -> int:
    """Encode ``value`` slice by slice and return its JSON size, or a size over ``stop``."""

    size = 2
    for position in range(0, len(value), _SCAN_CHUNK_CHARS):
        size += len(encode_basestring_ascii(value[position : position + _SCAN_CHUNK_CHARS])) - 2
        if size > stop:
            break
    return size


def _is_base64(value: str, start: int) -> bool:
    """Return True if ``value[start:]`` is one or more base64 characters, slice by slice."""

    end = len(value)
    for position in range(start, end, _SCAN_CHUNK_CHARS):
        if _BASE64.fullmatch(value, position, min(position + _SCAN_CHUNK_CHARS, end)) is None:
            return False
    return start < end


def _sha256(value: str) -> str:
    """Return the SHA-256 of the UTF-8 text, encoded slice by slice (the same digest)."""

    digest = sha256()
    for position in range(0, len(value), _SCAN_CHUNK_CHARS):
        digest.update(value[position : position + _SCAN_CHUNK_CHARS].encode())
    return digest.hexdigest()
