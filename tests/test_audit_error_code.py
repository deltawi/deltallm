from __future__ import annotations

from src.audit.errors import derive_audit_error_code
from src.email.models import EmailConfigurationError
from src.models.errors import ModelNotFoundError


def test_derive_audit_error_code_prefers_explicit_code() -> None:
    error = ModelNotFoundError(code="model_not_found")
    assert derive_audit_error_code(error) == "model_not_found"


def test_derive_audit_error_code_falls_back_to_class_name() -> None:
    error = EmailConfigurationError("email is disabled")
    assert derive_audit_error_code(error) == "email_configuration_error"

