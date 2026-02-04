"""Custom exceptions for ProxyLLM."""

from typing import Any, Optional


class ProxyLLMError(Exception):
    """Base exception for all ProxyLLM errors."""
    
    def __init__(
        self, 
        message: str, 
        *,
        type: Optional[str] = None,
        param: Optional[str] = None,
        code: Optional[str] = None,
        body: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.type = type or "deltallm_error"
        self.param = param
        self.code = code
        self.body = body or {}

    def __str__(self) -> str:
        msg = self.message
        if self.type:
            msg = f"{self.type}: {msg}"
        if self.code:
            msg = f"[{self.code}] {msg}"
        return msg


class AuthenticationError(ProxyLLMError):
    """Authentication failed (invalid API key, etc.)."""
    
    def __init__(self, message: str = "Authentication failed", **kwargs: Any) -> None:
        super().__init__(message, type="authentication_error", code="401", **kwargs)


class PermissionDeniedError(ProxyLLMError):
    """Permission denied for the requested resource."""
    
    def __init__(self, message: str = "Permission denied", **kwargs: Any) -> None:
        super().__init__(message, type="permission_denied", code="403", **kwargs)


class NotFoundError(ProxyLLMError):
    """Requested resource not found."""
    
    def __init__(self, message: str = "Resource not found", **kwargs: Any) -> None:
        super().__init__(message, type="not_found", code="404", **kwargs)


class RateLimitError(ProxyLLMError):
    """Rate limit exceeded."""
    
    def __init__(
        self, 
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(message, type="rate_limit_error", code="429", **kwargs)
        self.retry_after = retry_after


class BadRequestError(ProxyLLMError):
    """Invalid request (malformed, missing params, etc.)."""
    
    def __init__(self, message: str = "Bad request", **kwargs: Any) -> None:
        super().__init__(message, type="invalid_request_error", code="400", **kwargs)


class ContextLengthExceededError(BadRequestError):
    """Context length exceeded."""
    
    def __init__(self, message: str = "Context length exceeded", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.type = "context_length_exceeded"


class ContentPolicyViolationError(BadRequestError):
    """Content violates usage policies."""
    
    def __init__(self, message: str = "Content policy violation", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.type = "content_policy_violation"


class APIConnectionError(ProxyLLMError):
    """Failed to connect to the API."""
    
    def __init__(self, message: str = "Connection error", **kwargs: Any) -> None:
        super().__init__(message, type="connection_error", code="connection_error", **kwargs)


class APITimeoutError(APIConnectionError):
    """Request timed out."""
    
    def __init__(self, message: str = "Request timed out", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.type = "timeout_error"


class ServiceUnavailableError(ProxyLLMError):
    """Service temporarily unavailable."""
    
    def __init__(self, message: str = "Service unavailable", **kwargs: Any) -> None:
        super().__init__(message, type="service_unavailable", code="503", **kwargs)


class APIError(ProxyLLMError):
    """Generic API error from the provider."""
    
    def __init__(self, message: str = "API error", **kwargs: Any) -> None:
        super().__init__(message, type="api_error", code="500", **kwargs)


class ModelNotSupportedError(ProxyLLMError):
    """Model is not supported by the provider."""
    
    def __init__(self, model: str, provider: Optional[str] = None, **kwargs: Any) -> None:
        message = f"Model '{model}' is not supported"
        if provider:
            message += f" by provider '{provider}'"
        super().__init__(message, type="model_not_supported", **kwargs)
        self.model = model
        self.provider = provider


class BudgetExceededError(ProxyLLMError):
    """Budget limit exceeded."""
    
    def __init__(self, message: str = "Budget exceeded", **kwargs: Any) -> None:
        super().__init__(message, type="budget_exceeded", code="429", **kwargs)


class RouterError(ProxyLLMError):
    """Router-related error."""
    
    def __init__(self, message: str = "Router error", **kwargs: Any) -> None:
        super().__init__(message, type="router_error", **kwargs)


class ValidationError(ProxyLLMError):
    """Validation error."""
    
    def __init__(self, message: str = "Validation error", **kwargs: Any) -> None:
        super().__init__(message, type="validation_error", code="400", **kwargs)


def map_http_status_to_error(status_code: int, message: str, body: Optional[dict] = None) -> ProxyLLMError:
    """Map HTTP status code to appropriate exception."""
    error_map = {
        400: BadRequestError,
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        429: RateLimitError,
        500: APIError,
        502: APIError,
        503: ServiceUnavailableError,
        504: APITimeoutError,
    }
    
    error_class = error_map.get(status_code, APIError)
    
    # Check for specific error types in body
    if body and "error" in body:
        error_body = body["error"]
        error_type = error_body.get("type", "")
        error_code = error_body.get("code", "")
        
        if "context_length" in error_type or "context_length" in error_code:
            return ContextLengthExceededError(message, body=body)
        elif "content_policy" in error_type or "content_policy" in error_code:
            return ContentPolicyViolationError(message, body=body)
    
    return error_class(message, body=body)
