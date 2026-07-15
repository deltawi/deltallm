from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def dump_request_for_preflight(payload: BaseModel) -> dict[str, Any]:
    """Serialize request data without losing explicit nulls or adding defaults."""
    return payload.model_dump(mode="python", exclude_unset=True)
