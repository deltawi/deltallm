from __future__ import annotations


class MCPError(Exception):
    pass


class MCPTransportError(MCPError):
    pass


class MCPInvalidResponseError(MCPError):
    pass


class MCPToolNotFoundError(MCPError):
    pass


class MCPAuthError(MCPError):
    pass


class MCPAccessDeniedError(MCPError):
    pass


class MCPPolicyDeniedError(MCPError):
    pass


class MCPApprovalRequiredError(MCPError):
    def __init__(self, message: str, *, approval_request_id: str | None = None) -> None:
        self.approval_request_id = approval_request_id
        super().__init__(message)


class MCPRateLimitError(MCPError):
    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class MCPToolTimeoutError(MCPError):
    def __init__(self, message: str, *, timeout_ms: int | None = None) -> None:
        self.timeout_ms = timeout_ms
        super().__init__(message)
