"""Tests for custom exceptions."""

import pytest
from deltallm.exceptions import (
    ProxyLLMError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    RateLimitError,
    BadRequestError,
    ContextLengthExceededError,
    ContentPolicyViolationError,
    APIConnectionError,
    APITimeoutError,
    ServiceUnavailableError,
    APIError,
    ModelNotSupportedError,
    BudgetExceededError,
    RouterError,
    ValidationError,
    map_http_status_to_error,
)


class TestProxyLLMError:
    """Test base exception class."""

    def test_basic_error(self):
        """Test basic error creation."""
        error = ProxyLLMError("Test error")
        assert str(error) == "deltallm_error: Test error"
        assert error.message == "Test error"

    def test_error_with_type(self):
        """Test error with type."""
        error = ProxyLLMError("Test error", type="custom_error")
        assert error.type == "custom_error"
        assert "custom_error" in str(error)

    def test_error_with_code(self):
        """Test error with code."""
        error = ProxyLLMError("Test error", code="500")
        assert error.code == "500"
        assert "[500]" in str(error)

    def test_error_with_body(self):
        """Test error with body."""
        body = {"key": "value", "details": "test"}
        error = ProxyLLMError("Test error", body=body)
        assert error.body == body


class TestAuthenticationError:
    """Test authentication error."""

    def test_auth_error(self):
        """Test authentication error."""
        error = AuthenticationError("Invalid API key")
        assert error.code == "401"
        assert error.type == "authentication_error"
        assert "Invalid API key" in str(error)

    def test_default_message(self):
        """Test default error message."""
        error = AuthenticationError()
        assert "Authentication failed" in str(error)


class TestPermissionDeniedError:
    """Test permission denied error."""

    def test_permission_error(self):
        """Test permission denied error."""
        error = PermissionDeniedError("Access denied")
        assert error.code == "403"
        assert error.type == "permission_denied"


class TestNotFoundError:
    """Test not found error."""

    def test_not_found_error(self):
        """Test not found error."""
        error = NotFoundError("Model not found")
        assert error.code == "404"
        assert error.type == "not_found"


class TestRateLimitError:
    """Test rate limit error."""

    def test_rate_limit_error(self):
        """Test rate limit error."""
        error = RateLimitError("Rate limit exceeded")
        assert error.code == "429"
        assert error.type == "rate_limit_error"

    def test_rate_limit_with_retry_after(self):
        """Test rate limit error with retry_after."""
        error = RateLimitError("Rate limit exceeded", retry_after=60)
        assert error.retry_after == 60


class TestBadRequestError:
    """Test bad request error."""

    def test_bad_request_error(self):
        """Test bad request error."""
        error = BadRequestError("Invalid parameter")
        assert error.code == "400"
        assert error.type == "invalid_request_error"


class TestContextLengthExceededError:
    """Test context length exceeded error."""

    def test_context_length_error(self):
        """Test context length exceeded error."""
        error = ContextLengthExceededError("Too many tokens")
        assert isinstance(error, BadRequestError)
        assert error.type == "context_length_exceeded"

    def test_inherits_bad_request(self):
        """Test that it inherits from BadRequestError."""
        error = ContextLengthExceededError("Too many tokens")
        assert error.code == "400"


class TestContentPolicyViolationError:
    """Test content policy violation error."""

    def test_content_policy_error(self):
        """Test content policy violation error."""
        error = ContentPolicyViolationError("Content flagged")
        assert isinstance(error, BadRequestError)
        assert error.type == "content_policy_violation"


class TestAPIConnectionError:
    """Test API connection error."""

    def test_connection_error(self):
        """Test API connection error."""
        error = APIConnectionError("Connection failed")
        assert error.type == "connection_error"


class TestAPITimeoutError:
    """Test API timeout error."""

    def test_timeout_error(self):
        """Test API timeout error."""
        error = APITimeoutError("Request timed out")
        assert isinstance(error, APIConnectionError)
        assert error.type == "timeout_error"


class TestServiceUnavailableError:
    """Test service unavailable error."""

    def test_service_unavailable_error(self):
        """Test service unavailable error."""
        error = ServiceUnavailableError("Service down")
        assert error.code == "503"
        assert error.type == "service_unavailable"


class TestAPIError:
    """Test generic API error."""

    def test_api_error(self):
        """Test generic API error."""
        error = APIError("API failed")
        assert error.code == "500"
        assert error.type == "api_error"


class TestModelNotSupportedError:
    """Test model not supported error."""

    def test_model_not_supported(self):
        """Test model not supported error."""
        error = ModelNotSupportedError("unknown-model")
        assert "unknown-model" in str(error)
        assert error.model == "unknown-model"
        assert error.provider is None

    def test_model_with_provider(self):
        """Test model not supported with provider."""
        error = ModelNotSupportedError("unknown-model", provider="openai")
        assert error.model == "unknown-model"
        assert error.provider == "openai"
        assert "openai" in str(error)


class TestBudgetExceededError:
    """Test budget exceeded error."""

    def test_budget_exceeded_error(self):
        """Test budget exceeded error."""
        error = BudgetExceededError("Budget exceeded")
        assert error.code == "429"
        assert error.type == "budget_exceeded"


class TestRouterError:
    """Test router error."""

    def test_router_error(self):
        """Test router error."""
        error = RouterError("No healthy deployments")
        assert error.type == "router_error"


class TestValidationError:
    """Test validation error."""

    def test_validation_error(self):
        """Test validation error."""
        error = ValidationError("Validation failed")
        assert error.code == "400"
        assert error.type == "validation_error"


class TestExceptionInheritance:
    """Test exception inheritance hierarchy."""

    def test_all_inherit_from_base(self):
        """Test that all exceptions inherit from ProxyLLMError."""
        exceptions = [
            AuthenticationError("test"),
            PermissionDeniedError("test"),
            NotFoundError("test"),
            RateLimitError("test"),
            BadRequestError("test"),
            ContextLengthExceededError("test"),
            ContentPolicyViolationError("test"),
            APIConnectionError("test"),
            APITimeoutError("test"),
            ServiceUnavailableError("test"),
            APIError("test"),
            ModelNotSupportedError("test"),
            BudgetExceededError("test"),
            RouterError("test"),
            ValidationError("test"),
        ]
        
        for exc in exceptions:
            assert isinstance(exc, ProxyLLMError)

    def test_catch_base_exception(self):
        """Test that all exceptions can be caught as ProxyLLMError."""
        try:
            raise AuthenticationError("test")
        except ProxyLLMError as e:
            assert isinstance(e, AuthenticationError)

        try:
            raise RateLimitError("test")
        except ProxyLLMError as e:
            assert isinstance(e, RateLimitError)

        try:
            raise ContextLengthExceededError("test")
        except BadRequestError as e:
            assert isinstance(e, ContextLengthExceededError)

        try:
            raise APITimeoutError("test")
        except APIConnectionError as e:
            assert isinstance(e, APITimeoutError)


class TestMapHTTPStatusToError:
    """Test HTTP status code mapping."""

    def test_map_400_to_bad_request(self):
        """Test mapping 400 to BadRequestError."""
        error = map_http_status_to_error(400, "Bad request")
        assert isinstance(error, BadRequestError)

    def test_map_401_to_auth_error(self):
        """Test mapping 401 to AuthenticationError."""
        error = map_http_status_to_error(401, "Unauthorized")
        assert isinstance(error, AuthenticationError)

    def test_map_403_to_permission_error(self):
        """Test mapping 403 to PermissionDeniedError."""
        error = map_http_status_to_error(403, "Forbidden")
        assert isinstance(error, PermissionDeniedError)

    def test_map_404_to_not_found(self):
        """Test mapping 404 to NotFoundError."""
        error = map_http_status_to_error(404, "Not found")
        assert isinstance(error, NotFoundError)

    def test_map_429_to_rate_limit(self):
        """Test mapping 429 to RateLimitError."""
        error = map_http_status_to_error(429, "Rate limit exceeded")
        assert isinstance(error, RateLimitError)

    def test_map_500_to_api_error(self):
        """Test mapping 500 to APIError."""
        error = map_http_status_to_error(500, "Internal error")
        assert isinstance(error, APIError)

    def test_map_502_to_api_error(self):
        """Test mapping 502 to APIError."""
        error = map_http_status_to_error(502, "Bad gateway")
        assert isinstance(error, APIError)

    def test_map_503_to_service_unavailable(self):
        """Test mapping 503 to ServiceUnavailableError."""
        error = map_http_status_to_error(503, "Service unavailable")
        assert isinstance(error, ServiceUnavailableError)

    def test_map_504_to_timeout(self):
        """Test mapping 504 to APITimeoutError."""
        error = map_http_status_to_error(504, "Gateway timeout")
        assert isinstance(error, APITimeoutError)

    def test_map_unknown_status_to_api_error(self):
        """Test mapping unknown status to APIError."""
        error = map_http_status_to_error(418, "I'm a teapot")
        assert isinstance(error, APIError)

    def test_map_context_length_error(self):
        """Test mapping context_length error in body."""
        body = {"error": {"type": "context_length_exceeded", "message": "Too long"}}
        error = map_http_status_to_error(400, "Bad request", body=body)
        assert isinstance(error, ContextLengthExceededError)

    def test_map_content_policy_error(self):
        """Test mapping content_policy error in body."""
        body = {"error": {"type": "content_policy_violation", "message": "Flagged"}}
        error = map_http_status_to_error(400, "Bad request", body=body)
        assert isinstance(error, ContentPolicyViolationError)

    def test_map_context_length_by_code(self):
        """Test mapping context_length by code in body."""
        body = {"error": {"code": "context_length_exceeded", "message": "Too long"}}
        error = map_http_status_to_error(400, "Bad request", body=body)
        assert isinstance(error, ContextLengthExceededError)

    def test_map_with_none_body(self):
        """Test mapping with None body."""
        error = map_http_status_to_error(400, "Bad request", body=None)
        assert isinstance(error, BadRequestError)

    def test_map_with_empty_body(self):
        """Test mapping with empty body."""
        error = map_http_status_to_error(400, "Bad request", body={})
        assert isinstance(error, BadRequestError)
