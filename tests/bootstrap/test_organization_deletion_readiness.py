from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.bootstrap.organization_deletion import require_organization_deletion_readiness


@pytest.mark.asyncio
async def test_disabled_requests_do_not_run_expensive_readiness_scan(monkeypatch) -> None:  # noqa: ANN001
    verify = AsyncMock()
    monkeypatch.setattr(
        "src.bootstrap.organization_deletion.verify_readiness",
        verify,
    )

    await require_organization_deletion_readiness(object(), requests_enabled=False)

    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_requests_fail_closed_until_database_is_ready(monkeypatch) -> None:  # noqa: ANN001
    verify = AsyncMock(return_value={"ready": False})
    monkeypatch.setattr(
        "src.bootstrap.organization_deletion.verify_readiness",
        verify,
    )

    with pytest.raises(RuntimeError, match="database readiness is incomplete"):
        await require_organization_deletion_readiness(object(), requests_enabled=True)

    verify.assert_awaited_once()
