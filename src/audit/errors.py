from __future__ import annotations

import re


_CAMEL_TO_SNAKE_FIRST = re.compile("(.)([A-Z][a-z]+)")
_CAMEL_TO_SNAKE_SECOND = re.compile("([a-z0-9])([A-Z])")


def _camel_to_snake(value: str) -> str:
    first = _CAMEL_TO_SNAKE_FIRST.sub(r"\1_\2", value)
    return _CAMEL_TO_SNAKE_SECOND.sub(r"\1_\2", first).lower()


def derive_audit_error_code(error: Exception | None) -> str | None:
    if error is None:
        return None

    for attr_name in ("code", "error_code", "status_code"):
        value = getattr(error, attr_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        text = str(status_code).strip()
        if text:
            return text

    error_type = getattr(error, "error_type", None)
    if error_type is not None:
        text = str(error_type).strip()
        if text:
            return text

    return _camel_to_snake(error.__class__.__name__) or None
