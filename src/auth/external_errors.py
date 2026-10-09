from __future__ import annotations

from src.models.errors import ProxyError


class ExternalAuthError(ProxyError):
    status_code = 403
    error_type = "external_auth_error"
    message = "External access is denied"

    def __init__(self, code: str = "external_access_denied", *, status_code: int = 403) -> None:
        self.status_code = status_code
        super().__init__(code=code)


class InvalidExternalAssertion(ExternalAuthError):
    def __init__(self) -> None:
        super().__init__("invalid_external_assertion", status_code=401)


class ExternalAuthUnavailable(ExternalAuthError):
    def __init__(self) -> None:
        super().__init__("external_auth_unavailable", status_code=503)
