from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from src.batch.error_remediation import remediate_terminal_batch_item_errors
from src.batch.error_sanitization import sanitize_batch_artifact_error
from src.batch.public_errors import BatchPublicErrorCode, exception_public_error_code
from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.models.errors import ServiceUnavailableError
from src.router.execution import attach_failover_original_error


@pytest.mark.parametrize("code", [None, "", "provider-secret", "x" * 10000, 1, {}, []])
def test_unknown_error_codes_never_expand_public_fields(code):
    assert sanitize_batch_artifact_error(
        {"code": code, "message": "private", "retry_category": "service_unavailable"},
        cancelled=False,
    ) == {
        "message": "Provider unavailable",
        "type": "BatchItemError",
        "retry_category": "service_unavailable",
    }


def test_server_error_survives_wrapping_but_provider_cannot_forge_it():
    code = BatchPublicErrorCode.SELECTOR_CHECKPOINT_UNAVAILABLE
    forged = ServiceUnavailableError(message="private", code=code.value)
    assert exception_public_error_code(forged) is None
    wrapped = attach_failover_original_error(forged, BatchSelectorUnavailable())
    assert exception_public_error_code(wrapped) is code
    assert sanitize_batch_artifact_error({"code": code.value}, cancelled=True) == {
        "message": "Batch request cancelled",
        "type": "BatchItemCancelled",
    }


async def test_terminal_remediation_preserves_only_the_safe_checkpoint_meaning():
    code = BatchPublicErrorCode.SELECTOR_CHECKPOINT_UNAVAILABLE
    store = SimpleNamespace(
        list_terminal_error_rows=AsyncMock(
            return_value=[
                {
                    "item_id": "one",
                    "status": "failed",
                    "retry_category": "service_unavailable",
                    "error_code": code.value,
                    "has_error_body": True,
                    "has_last_error": True,
                    "last_error": "private",
                    "error_body": {"message": "private"},
                }
            ]
        ),
        update_terminal_error_rows=AsyncMock(return_value=1),
    )
    result = await remediate_terminal_batch_item_errors(
        store,
        after_item_id=None,
        page_size=10,
        max_pages=1,
        apply=True,
    )
    assert result.updated == 1
    (updates,) = store.update_terminal_error_rows.call_args.args
    assert updates[0]["last_error"] == code.message
    assert updates[0]["error_body"]["code"] == code.value
    assert "private" not in str(updates)
