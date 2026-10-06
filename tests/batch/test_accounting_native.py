import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.batch.accounting_checkpoint import BatchAccountingUnavailable
from src.batch.accounting_delivery import NativeBatchCompletionDelivery
from src.batch.accounting_native import NativeBatchBilling, decode_checkpoint
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.accounting_protocol import AccountingOutcome, DispatchPermit, ReserveDecision
from src.billing.accounting_service import AccountingProtocolService
from src.billing.accounting_snapshots import finalization_bytes
from src.models.requests import ChatCompletionRequest, EmbeddingRequest
from src.models.responses import UserAPIKeyAuth
from src.router.router import Deployment
from tests.test_accounting_local_service import state
from tests.test_accounting_request_path import _AccountingRepository
from tests.test_batch_completion_outbox import _build_record
from tests.test_batch_worker import _build_chat_batch_item, _build_chat_batch_job


class Checkpoints:
    def __init__(self):
        self.rows, self.calls = {}, []
        self.lose_terminal_reply = False
        self.lose_dispatch_reply = False

    async def write_many(self, writes, *, expires_at):
        assert expires_at > asyncio.get_running_loop().time()
        for write in writes:
            write.validate()
            current = self.rows.get(write.claim.item_id)
            assert (
                current == write.expected
                or (write.expected is not None and current == write.checkpoint)
                or (
                    write.checkpoint.terminal is not None
                    and current
                    in (write.checkpoint, write.checkpoint.model_copy(update={"terminal": None}))
                )
            )
        for write in writes:
            self.rows[write.claim.item_id] = write.checkpoint
        self.calls.append(tuple(writes))
        if self.lose_terminal_reply and any(
            write.checkpoint.terminal is not None for write in writes
        ):
            self.lose_terminal_reply = False
            raise TimeoutError()
        if self.lose_dispatch_reply and all(write.checkpoint.terminal is None for write in writes):
            self.lose_dispatch_reply = False
            raise TimeoutError()


@pytest.fixture(params=["assigned", "local"])
async def native_batch(request):
    if request.param == "assigned":
        service = AccountingProtocolService(_AccountingRepository(), generation=7, dwell_seconds=0)
    else:
        _, persistence, _, _, _, service = state(dwell_seconds=0)
        persistence.change = lambda replies: [
            reply.model_copy(update={"outcome": value.finalization.outcome})
            for reply, value in zip(replies, persistence.calls[-1], strict=True)
        ]
    service.start()
    checkpoints = Checkpoints()
    owner = NativeBatchBilling(service, checkpoints, tier_policy=None, max_provider_attempts=3)
    try:
        yield owner, checkpoints, service
    finally:
        await service.close()


def subjects(owner, *, count=1, embedding=False):
    job = _build_chat_batch_job()
    auth = UserAPIKeyAuth(
        api_key=job.created_by_api_key,
        user_id=job.created_by_user_id,
        team_id=job.created_by_team_id,
        organization_id=job.created_by_organization_id,
    )
    payload = (
        EmbeddingRequest(model="gpt-oss", input="hello")
        if embedding
        else ChatCompletionRequest(
            model="gpt-oss",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=8,
        )
    )
    executions = []
    for index in range(count):
        item = _build_chat_batch_item(f"native-{index + 1}", "hello")
        item.claim_epoch = 1
        executions.append(owner.bind(job, item, worker_id="w1", auth=auth))
    deployment = Deployment(
        deployment_id="native",
        model_name=payload.model,
        deltallm_params={"model": payload.model, "custom_llm_provider": "openai"},
        model_info={
            "max_input_tokens": 100,
            "max_output_tokens": 10,
            "input_cost_per_token": "0.01",
            "output_cost_per_token": "0.02",
            "batch_input_cost_per_token": "0.001",
            "batch_output_cost_per_token": "0.002",
        },
    )
    return executions, payload, auth, deployment


async def fund(owner, executions, payload, auth, deployment):
    await owner.prepare_group(
        executions,
        payloads=[payload] * len(executions),
        auths=[auth] * len(executions),
        deployment=deployment,
    )


@pytest.mark.parametrize("embedding", [False, True])
async def test_group_funding_is_shared_and_checkpoint_write_is_batched(native_batch, embedding):
    owner, checkpoints, service = native_batch
    executions, payload, auth, deployment = subjects(owner, count=4, embedding=embedding)
    service.reserve = AsyncMock(wraps=service.reserve)
    await fund(owner, executions, payload, auth, deployment)
    assert service.reserve.await_count == 4
    assert len(checkpoints.calls) == 1 and len(checkpoints.calls[0]) == 4
    assert all(execution.dispatched for execution in executions)
    assert all(
        decode_checkpoint(execution.item.accounting_checkpoint) == execution.checkpoint
        for execution in executions
    )
    assert all(
        execution.checkpoint.operation.reservation.allowance >= Decimal("0.3")
        for execution in executions
    )


async def test_completed_batch_prices_are_frozen_before_provider_and_exact(native_batch):
    owner, checkpoints, service = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    deployment.model_info["batch_input_cost_per_token"] = "99"
    execution = executions[0]
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "prompt_tokens_cached": 0}
    outbox = execution.completion_payload({"completed_at": datetime.now(UTC).isoformat()}, usage)
    terminal = execution.checkpoint.terminal
    assert terminal.exact_charge == Decimal("0.009")
    assert terminal.spend_payload["provider_cost_exact"] == "0.090000000000000000"
    assert outbox["billing_event_id"] == str(execution.checkpoint.operation_id)
    assert "cost_exact" not in deployment.model_info
    await owner.close_group(executions)
    assert checkpoints.calls[-1][0].checkpoint.terminal == terminal
    record = replace(
        _build_record(payload_overrides=outbox),
        completion_id=outbox["billing_event_id"],
        batch_id=execution.claim.batch_id,
        item_id=execution.claim.item_id,
    )
    repository = type(
        "DeliveryStore", (), {"mark_completion_outbox_sent": AsyncMock(return_value=True)}
    )()
    delivery = NativeBatchCompletionDelivery(service, repository)
    assert await delivery.deliver(record, worker_id="delivery")
    repository.mark_completion_outbox_sent.assert_awaited_once_with(
        record.completion_id,
        worker_id="delivery",
        attempt_count=record.attempt_count,
    )


async def test_cleanup_lost_reply_keeps_identical_terminal_bytes(native_batch):
    owner, checkpoints, _ = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    checkpoints.lose_terminal_reply = True
    with pytest.raises(TimeoutError):
        await owner.close_group(executions)
    frozen = finalization_bytes(executions[0].checkpoint.terminal)
    await owner.close_group(executions)
    assert finalization_bytes(checkpoints.calls[-1][0].checkpoint.terminal) == frozen


async def test_reclaimed_dispatch_can_only_recover_uncertainty(native_batch):
    owner, checkpoints, _ = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    old = executions[0]
    reclaimed = replace(old.item, claim_epoch=2, locked_by="w2")
    current = owner.bind(_build_chat_batch_job(), reclaimed, worker_id="w2", auth=auth)
    with pytest.raises(BatchAccountingUnavailable):
        await fund(owner, [current], payload, auth, deployment)
    assert checkpoints.rows[current.claim.item_id].terminal.outcome is AccountingOutcome.UNCERTAIN
    frozen = checkpoints.rows[current.claim.item_id].model_dump_json()
    await owner.close_group([current])
    assert checkpoints.rows[current.claim.item_id].model_dump_json() == frozen


async def test_dispatch_checkpoint_lost_reply_recovers_without_provider_dispatch(native_batch):
    owner, checkpoints, _ = native_batch
    executions, payload, auth, deployment = subjects(owner)
    checkpoints.lose_dispatch_reply = True
    with pytest.raises(TimeoutError):
        await fund(owner, executions, payload, auth, deployment)
    assert not executions[0].dispatched
    await owner.close_group(executions)
    assert (
        checkpoints.rows[executions[0].claim.item_id].terminal.outcome
        is AccountingOutcome.NOT_DISPATCHED
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "foreign-key"),
        ("team_id", "foreign-team"),
        ("organization_id", "foreign-org"),
        ("user_id", "foreign-user"),
        ("owner_account_id", "foreign-owner"),
    ],
)
async def test_failover_cannot_change_funded_tenant(native_batch, field, value):
    owner, _, service = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    service.reserve = AsyncMock(wraps=service.reserve)
    with pytest.raises(BatchAccountingUnavailable):
        await fund(owner, executions, payload, auth.model_copy(update={field: value}), deployment)
    assert service.reserve.await_count == 0
    assert len(executions[0].checkpoint.operation.attempts) == 1


async def test_failover_cannot_change_funded_request(native_batch):
    owner, _, _ = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    changed = payload.model_copy(update={"user": "foreign-end-user"})
    with pytest.raises(BatchAccountingUnavailable):
        await fund(owner, executions, changed, auth, deployment)
    assert len(executions[0].checkpoint.operation.attempts) == 1


async def test_failover_attempts_share_one_reservation_and_are_bounded(native_batch):
    owner, _, service = native_batch
    executions, payload, auth, deployment = subjects(owner)
    service.reserve = AsyncMock(wraps=service.reserve)
    for _ in range(3):
        await fund(owner, executions, payload, auth, deployment)
    assert service.reserve.await_count == 1
    assert len(executions[0].checkpoint.operation.attempts) == 3
    with pytest.raises(BatchAccountingUnavailable):
        await fund(owner, executions, payload, auth, deployment)
    assert executions[0].checkpoint.terminal.outcome is AccountingOutcome.UNCERTAIN


async def test_partial_group_funding_releases_unsent_proofs_before_error(native_batch):
    owner, checkpoints, service = native_batch
    executions, payload, auth, deployment = subjects(owner, count=2)
    original = service.reserve

    async def deny_second(reservation):
        if reservation.operation_id != batch_selector_operation_id(
            executions[0].claim.batch_id,
            executions[0].claim.item_id,
        ):
            return DispatchPermit(
                protocol_generation=reservation.protocol_generation,
                operation_id=reservation.operation_id,
                decision=ReserveDecision.BUDGET_EXHAUSTED,
            )
        return await original(reservation)

    service.reserve = deny_second
    with pytest.raises(Exception):
        await fund(owner, executions, payload, auth, deployment)
    assert len(checkpoints.rows) == 1
    assert (
        next(iter(checkpoints.rows.values())).terminal.outcome is AccountingOutcome.NOT_DISPATCHED
    )


async def test_native_delivery_lost_ack_retries_bytes_without_second_provider_call(native_batch):
    owner, _, service = native_batch
    executions, payload, auth, deployment = subjects(owner)
    await fund(owner, executions, payload, auth, deployment)
    execution = executions[0]
    outbox = execution.completion_payload(
        {"completed_at": datetime.now(UTC).isoformat()},
        {"prompt_tokens": 5, "completion_tokens": 2},
    )
    record = replace(
        _build_record(payload_overrides=outbox),
        completion_id=outbox["billing_event_id"],
        batch_id=execution.claim.batch_id,
        item_id=execution.claim.item_id,
    )
    calls, original = [], service.finalize_operation

    async def lose(handle, terminal):
        calls.append(finalization_bytes(terminal))
        receipt = await original(handle, terminal)
        if len(calls) == 1:
            raise TimeoutError()
        return receipt

    service.finalize_operation = lose
    repository = type(
        "DeliveryStore", (), {"mark_completion_outbox_sent": AsyncMock(return_value=True)}
    )()
    delivery = NativeBatchCompletionDelivery(service, repository)
    with pytest.raises(TimeoutError):
        await delivery.deliver(record, worker_id="delivery")
    assert await delivery.deliver(record, worker_id="delivery")
    assert calls[0] == calls[1]
    with pytest.raises(BatchAccountingUnavailable):
        await NativeBatchCompletionDelivery(None, repository).deliver(record, worker_id="delivery")
