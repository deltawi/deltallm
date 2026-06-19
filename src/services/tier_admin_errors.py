from __future__ import annotations


class TierAdminError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
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
