from __future__ import annotations

import json
from typing import Final

MAX_HEALED_JSON_CHARS: Final = 1_048_576
_OPENERS: Final = {"{": "}", "[": "]"}


def heal_json_text(text: str, *, require_object: bool) -> str | None:
    """Return a repaired JSON document, or None when a safe repair does not exist.

    The repair only removes text around the document and trailing commas. It never
    invents values or closes a truncated document, so a healed answer contains only
    what the model wrote.
    """

    if len(text) > MAX_HEALED_JSON_CHARS:
        return None
    candidate = _strip_code_fence(text.strip())
    start = _first_opener(candidate, require_object=require_object)
    if start < 0:
        return None
    document = _balanced_document(candidate, start)
    if document is None:
        return None
    try:
        parsed = json.loads(document)
    except (RecursionError, ValueError):
        return None
    if require_object and not isinstance(parsed, dict):
        return None
    return document


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    closing = text.rfind("```")
    if first_newline < 0 or closing <= first_newline:
        return text
    return text[first_newline + 1 : closing].strip()


def _first_opener(text: str, *, require_object: bool) -> int:
    brace = text.find("{")
    if require_object:
        return brace
    bracket = text.find("[")
    candidates = [index for index in (brace, bracket) if index >= 0]
    return min(candidates) if candidates else -1


def _balanced_document(text: str, start: int) -> str | None:
    """Copy one balanced value from start, dropping commas before a closing bracket."""

    out: list[str] = []
    expected: list[str] = []
    in_string = False
    escaped = False
    pending_comma = False
    pending_space: list[str] = []
    for char in text[start:]:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char in " \t\r\n":
            (pending_space if pending_comma else out).append(char)
            continue
        if char == ",":
            if pending_comma:
                return None
            pending_comma = True
            continue
        if char in _OPENERS:
            expected.append(_OPENERS[char])
        elif char in "}]":
            if not expected or expected.pop() != char:
                return None
            pending_comma = False
            pending_space.clear()
            out.append(char)
            if not expected:
                return "".join(out)
            continue
        if pending_comma:
            out.append(",")
            out.extend(pending_space)
            pending_space.clear()
            pending_comma = False
        if char == '"':
            in_string = True
        out.append(char)
    return None
