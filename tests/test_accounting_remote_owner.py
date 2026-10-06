"""The parent lifecycle owns remote startup and exactly one ordered close."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.bootstrap.accounting_remote import RemoteAccountingOwner
from src.bootstrap.runtime_services import RuntimeServicesRuntime, shutdown_runtime_services
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from tests.test_accounting_native_config import native


def owner():
    return RemoteAccountingOwner(
        native(), ProcessLifecycle(LifecycleSettings()), role="api", owner_id="remote-test"
    )


async def test_runtime_closes_local_graph_before_transport_once():
    value = owner()
    order = []

    async def local_close(*, expires_at):
        assert expires_at > asyncio.get_running_loop().time()
        order.append("local")

    original_close = value.transport.close

    async def transport_close():
        order.append("transport")
        await original_close()

    value.runtime.close = local_close
    value.transport.close = transport_close
    service = AsyncMock()
    runtime = RuntimeServicesRuntime(
        accounting_remote_owner=value, accounting_protocol_service=service
    )
    await shutdown_runtime_services(runtime)
    await shutdown_runtime_services(runtime)
    assert order == ["local", "transport"]
    service.close.assert_not_awaited()


async def test_failed_local_start_is_owned_and_transport_still_closes():
    value = owner()
    value.runtime.start = AsyncMock(side_effect=RuntimeError("dependency unavailable"))
    value.runtime.close = AsyncMock(side_effect=RuntimeError("drain incomplete"))
    original_close = value.transport.close
    value.transport.close = AsyncMock(wraps=original_close)
    runtime = RuntimeServicesRuntime(accounting_remote_owner=value)
    with pytest.raises(RuntimeError, match="dependency unavailable"):
        await value.start(expires_at=asyncio.get_running_loop().time() + 1)
    with pytest.raises(RuntimeError, match="drain incomplete"):
        await shutdown_runtime_services(runtime)
    value.runtime.close.assert_awaited_once()
    value.transport.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match="restart"):
        await value.start(expires_at=asyncio.get_running_loop().time() + 1)


@pytest.mark.parametrize("role", ["accountingWorker", "accountingRequest"])
def test_minimal_roles_cannot_open_an_api_rpc_pool(role):
    with pytest.raises(ValueError):
        RemoteAccountingOwner(
            native(accounting_projection_worker_enabled=role == "accountingWorker"),
            ProcessLifecycle(LifecycleSettings()),
            role=role,
            owner_id="remote-test",
        )
