from __future__ import annotations

from src.models.errors import ProxyError


class GuardrailViolationError(ProxyError):
    status_code = 400
    error_type = "guardrail_violation"
    message = "Guardrail violation"

    def __init__(
        self,
        guardrail_name: str,
        message: str,
        violation_type: str | None = None,
        status_code: int = 400,
        code: str = "content_policy_violation",
    ) -> None:
        self.guardrail_name = guardrail_name
        self.violation_type = violation_type
        self.status_code = status_code
        super().__init__(message=message, param=violation_type, code=code)
