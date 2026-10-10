"""Signed persistence keeps local issue ownership and rejects changed proofs."""

import asyncio
from datetime import timedelta
from decimal import Decimal
import json
import time
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from src.api.internal_accounting import accounting_rpc_router
from src.billing.accounting.transport.accounting_auth import (
    ACCOUNTING_SIGNATURE_HEADER,
    ACCOUNTING_TIMESTAMP_HEADER,
    accounting_signature,
)
from src.billing.accounting.health.accounting_health import (
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
)
from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.billing.accounting.transport.accounting_local_wire import WireLocalGrant
from src.billing.accounting.transport.accounting_remote_leases import RemoteLocalLeasePersistence
from src.billing.accounting.transport.accounting_rpc_contracts import (
    LocalFundingReply,
    LocalFundingRequest,
    LocalReturnRequest,
    LocalTerminalRequest,
    WireLocalReturn,
    funding_reply,
    rpc_batch_bytes,
    rpc_request_bytes,
)
from src.billing.accounting.transport.accounting_rpc_service import AccountingRpcService
from src.billing.accounting.accounting_protocol import ReserveDecision
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.outbound.network_policy import OutboundNetworkPolicy
from src.telemetry.lifecycle import WorkerHealth, WorkerState
from tests.test_accounting_local_leases import (
    allocation,
    funding_row,
    grant,
    terminal,
)
from tests.test_accounting_health import snapshot


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


class Healthy:
    def __init__(self):
        self.state = WorkerState.READY

    @property
    def worker_health(self):
        return WorkerHealth(self.state)


class Backlog:
    async def snapshot(self, **kwargs):
        return snapshot()


class QueryClient:
    def __init__(self):
        self.calls = []
        self.error = None
        self.entered = asyncio.Event()
        self.resume = None

    async def query_raw(self, query, *parameters):
        self.calls.append((query, parameters))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        if "allocate_local_permit" in query:
            values = json.loads(parameters[3])
            results = []
            for raw in values:
                item = type(allocation()).model_validate_json(json.dumps(raw))
                row = funding_row(item)
                row["grantee_id"] = f"{parameters[1]}:{item.fence_token}"
                results.append(row)
            return results
        if "return_local_permit" in query:
            return [{**value, "returned_operations": 3} for value in json.loads(parameters[2])]
        if "append_terminal_journal" in query:
            return [
                {
                    "operation_id": value["operation_id"],
                    "journal_sequence": index + 1,
                    "outcome": value["outcome"],
                    "replayed": False,
                }
                for index, value in enumerate(json.loads(parameters[1]))
            ]
        raise AssertionError("unexpected database call")


async def rpc_state(client=None):
    client = client or QueryClient()
    probe = AccountingBacklogProbe(Backlog(), AccountingBacklogPolicy(generation=7))
    assert await probe.refresh(expires_at=deadline())
    projection = Healthy()
    service = AccountingRpcService(
        client, generation=7, probe=probe, projection_health=projection, dwell_seconds=0
    )
    service.start()
    app = FastAPI()
    app.include_router(accounting_rpc_router(service, signing_secret="rpc-test-secret"))
    return app, service, client, projection


def remote(app):
    async def resolve(hostname, port):
        return ("10.96.1.2",)

    transport = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="rpc-test-secret",
        transport=httpx.ASGITransport(app=app),
        network_policy=OutboundNetworkPolicy(
            allow_http=True,
            allowed_ports=(80,),
            allowed_private_cidrs=("10.96.0.0/12",),
            resolver=resolve,
        ),
    )
    return RemoteLocalLeasePersistence(transport, generation=7, owner_id="lease-test"), transport


async def test_remote_funding_return_and_terminal_each_use_one_bulk_native_call():
    app, service, client, _ = await rpc_state()
    persistence, transport = remote(app)
    try:
        assert await persistence.protocol_ready(7)
        item = allocation()
        funded = (await persistence.allocate_batch([item], expires_at=deadline()))[0]
        assert funded.fence_token == item.fence_token
        assert funded.allowance == item.reservation.allowance
        value = WireLocalReturn(
            grant=WireLocalGrant(**funded.model_dump(exclude={"observed_monotonic"})),
            first_unused_ordinal=1,
        ).restore(observed_monotonic=0)
        assert await persistence.return_batch([value], expires_at=deadline()) == (3,)
        written = terminal()
        receipts = await persistence.finalize_batch([written], expires_at=deadline())
        assert receipts[0].operation_id == written.finalization.operation_id
        assert len(client.calls) == 3
        assert await persistence.allocate_batch([], expires_at=deadline()) == ()
        assert await persistence.return_batch([], expires_at=deadline()) == ()
        assert await persistence.finalize_batch([], expires_at=deadline()) == ()
        assert len(client.calls) == 3
    finally:
        await service.close(timeout_seconds=1)
        await transport.close()


@pytest.mark.parametrize(
    "failure", ["fence", "owner", "generation", "allowance", "limit", "length"]
)
async def test_changed_funding_reply_cannot_enter_the_local_issuer(failure):
    item = allocation()
    funded = grant(item)
    raw = json.loads(rpc_batch_bytes([funding_reply(item, funded, now=funded.observed_monotonic)]))
    if failure == "fence":
        raw[0]["allocation_fence_token"] = str(uuid4())
    elif failure == "length":
        raw = []
    else:
        raw[0]["grant"][
            {
                "owner": "grantee_id",
                "generation": "protocol_generation",
                "allowance": "allowance",
                "limit": "operation_limit",
            }[failure]
        ] = {
            "owner": "other",
            "generation": 8,
            "allowance": "2",
            "limit": 1024,
        }[failure]

    class Transport:
        async def request(self, *args, **kwargs):
            return json.dumps(raw).encode()

    with pytest.raises(AccountingProtocolUnavailable):
        await RemoteLocalLeasePersistence(
            Transport(), generation=7, owner_id="lease-test"
        ).allocate_batch([item], expires_at=deadline())


@pytest.mark.parametrize("failure", ["signature", "old", "body", "path"])
async def test_bad_signature_is_unauthorized_and_never_queries(failure):
    app, service, client, _ = await rpc_state()
    path = "/internal/accounting/v1/allocate/local/batch"
    body = (
        LocalFundingRequest(
            generation=7, budget_ms=1000, owner_id="lease-test", values=(allocation(),)
        )
        .model_dump_json()
        .encode()
    )
    timestamp = str(int(time.time()) - (31 if failure == "old" else 0))
    signature = accounting_signature(
        "rpc-test-secret",
        timestamp=timestamp,
        path=path + ("changed" if failure == "path" else ""),
        body=body,
    )
    if failure == "signature":
        signature = "0" * 64
    if failure == "body":
        body += b" "
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://accounting.test"
        ) as http:
            response = await http.post(
                path,
                content=body,
                headers={
                    ACCOUNTING_TIMESTAMP_HEADER: timestamp,
                    ACCOUNTING_SIGNATURE_HEADER: signature,
                },
            )
        assert response.status_code == 401 and client.calls == []
        assert "lease-test" not in response.text
    finally:
        await service.close(timeout_seconds=1)


@pytest.mark.parametrize("generation", [8, True])
async def test_stale_or_boolean_generation_rejects_before_funding(generation):
    app, service, client, _ = await rpc_state()
    persistence, transport = remote(app)
    try:
        assert not await persistence.protocol_ready(generation)
        item = allocation().model_copy(
            update={
                "reservation": allocation().reservation.model_copy(
                    update={"protocol_generation": generation}
                )
            }
        )
        with pytest.raises(ValueError):
            await persistence.allocate_batch([item], expires_at=deadline())
        assert client.calls == []
    finally:
        await service.close(timeout_seconds=1)
        await transport.close()


@pytest.mark.parametrize("state", [WorkerState.FAILED, WorkerState.DISABLED, WorkerState.STOPPING])
async def test_projection_failure_stops_funding_but_keeps_returns_and_terminal_recovery(state):
    app, service, client, projection = await rpc_state()
    persistence, transport = remote(app)
    try:
        projection.state = state
        assert not service.worker_health.ready and not await persistence.protocol_ready(7)
        with pytest.raises(AccountingProtocolUnavailable):
            await persistence.allocate_batch([allocation()], expires_at=deadline())
        assert client.calls == []
        assert len(await persistence.finalize_batch([terminal()], expires_at=deadline())) == 1
        assert len(client.calls) == 1
    finally:
        await service.close(timeout_seconds=1)
        await transport.close()


async def test_terminal_queue_cannot_extend_the_transport_deadline():
    app, service, client, _ = await rpc_state()
    value = terminal()
    from src.billing.accounting.transport.accounting_local_wire import wire_local_terminals

    request = LocalTerminalRequest.model_validate_json(
        rpc_request_bytes(
            generation=7,
            budget_ms=5000,
            values=wire_local_terminals([value], generation=7),
        )
    )
    client.resume = asyncio.Event()
    end = deadline(0.03)
    try:
        with pytest.raises(TimeoutError):
            await service.finalize(request, expires_at=end)
        assert client.calls and not service.terminals.task.done()
        client.resume.set()
    finally:
        await service.close(timeout_seconds=1)
    assert service.terminals.retained_bytes == 0


@pytest.mark.parametrize(
    "body,headers",
    [
        (b"x" * 1_048_577, {}),
        (b"{}", {"content-encoding": "gzip"}),
        (b"{}", {"content-length": "unknown"}),
    ],
)
async def test_invalid_body_is_bounded_before_authentication_and_queries(body, headers):
    app, service, client, _ = await rpc_state()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://accounting.test"
        ) as http:
            response = await http.post(
                "/internal/accounting/v1/allocate/local/batch", content=body, headers=headers
            )
        assert response.status_code in {400, 413} and client.calls == []
    finally:
        await service.close(timeout_seconds=1)


async def test_funding_and_return_contracts_reject_another_owner_or_duplicate_fence():
    item = allocation()
    with pytest.raises(ValueError):
        LocalFundingRequest(
            generation=7, budget_ms=1000, owner_id="lease-test", values=(item, item)
        )
    funded = grant(item)
    wire = WireLocalReturn(
        grant=WireLocalGrant(**funded.model_dump(exclude={"observed_monotonic"})),
        first_unused_ordinal=1,
    )
    with pytest.raises(ValueError):
        LocalReturnRequest(generation=7, budget_ms=1000, owner_id="other", values=(wire,))
    with pytest.raises(ValueError):
        LocalFundingReply(allocation_fence_token=item.fence_token, decision=ReserveDecision.REPLAY)


@pytest.mark.parametrize("now", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_funding_clock_cannot_cross_the_wire(now):
    item = allocation()
    funded = grant(item)
    with pytest.raises(ValueError, match="invalid local clock"):
        funding_reply(item, funded, now=now)


async def test_rpc_wire_funding_uses_receiver_clock_and_accounts_for_server_elapsed_time():
    item = allocation()
    funded = grant(item)
    reply = funding_reply(item, funded, now=funded.observed_monotonic + 28)
    assert reply.grant.dispatch_expires_at - reply.grant.observed_at == timedelta(seconds=2)
    restored = reply.grant.restore(observed_monotonic=1000000)
    assert restored.dispatch_deadline == 1000002
    assert restored.allowance == Decimal(str(item.reservation.allowance))
    assert "observed_monotonic" not in reply.model_dump_json()
