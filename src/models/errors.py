from __future__ import annotations


class ProxyError(Exception):
    status_code: int = 500
    error_type: str = "server_error"
    message: str = "Internal server error"

    def __init__(self, message: str | None = None, param: str | None = None, code: str | None = None):
        self.message = message or self.message
        self.param = param
        self.code = code
        super().__init__(self.message)


class AuthenticationError(ProxyError):
    status_code = 401
    error_type = "authentication_error"
    message = "Invalid API key"


class RateLimitError(ProxyError):
    status_code = 429
    error_type = "rate_limit_error"
    message = "Rate limit exceeded"

    def __init__(self, message: str | None = None, retry_after: int | None = None, **kwargs):
        super().__init__(message=message, **kwargs)
        self.retry_after = retry_after


class BudgetExceededError(ProxyError):
    status_code = 400
    error_type = "budget_exceeded"
    message = "Budget exceeded"


class ModelNotFoundError(ProxyError):
    status_code = 404
    error_type = "model_not_found"
    message = "Model not found"


class TimeoutError(ProxyError):
    status_code = 408
    error_type = "timeout_error"
    message = "Request timeout"


class InvalidRequestError(ProxyError):
    status_code = 400
    error_type = "invalid_request_error"
    message = "Invalid request"


class PermissionDeniedError(ProxyError):
    status_code = 403
    error_type = "permission_denied"
    message = "Permission denied"


class ServiceUnavailableError(ProxyError):
    status_code = 503
    error_type = "service_unavailable"
    message = "Service unavailable"
