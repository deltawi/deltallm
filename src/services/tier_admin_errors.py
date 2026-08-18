from __future__ import annotations

from typing import Any


class TierAdminError(Exception):
    def __init__(self, detail: str | dict[str, Any]) -> None:
        message = detail.get("message") if isinstance(detail, dict) else detail
        super().__init__(message or str(detail))
        self.detail = detail


class TierAdminValidationError(TierAdminError):
    pass


class TierAdminConflictError(TierAdminError):
    pass


class TierAdminNotFoundError(TierAdminError):
    pass


class TierAdminUnavailableError(TierAdminError):
    pass


__all__ = [
    "TierAdminConflictError",
    "TierAdminError",
    "TierAdminNotFoundError",
    "TierAdminUnavailableError",
    "TierAdminValidationError",
]
