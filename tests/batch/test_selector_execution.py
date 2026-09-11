import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.batch.selector_identity import batch_selector_operation_id
from src.models.errors import BudgetExceededError, RateLimitError
from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import answer_calls, selection_calls
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch


async def test_each_item_selects_independently_without_public_usage_or_pricing_changes(
    selected_batch,
):
    h = selected_batch
    items = [h.item(1), h.item(2, "complex reasoning")]
    await h.worker._process_items(h.job, items)
    assert [call["model"] for call in answer_calls(h)] == [
        h.policy["members"][0]["deployment_id"],
        "quality",
    ]
    assert len(selection_calls(h)) == 2 and len(h.checkpoints.writes) == 4
    assert len(h.repository.completed_calls) == 2
    assert all(row["usage"]["total_tokens"] == 18 for row in h.repository.completed_calls)
    outboxes = {row["item_id"]: row for row in h.repository.completion_outbox_calls}
    for item in items:
        outbox = outboxes[item.item_id]
        assert outbox["billing_event_id"] == str(
            batch_selector_operation_id(h.job.batch_id, item.item_id)
        )
        assert outbox["usage"]["total_tokens"] == 18
        assert "selector" not in outbox["usage"]
    charges = [call.args[1] for call in h.billing.accept_selector.call_args_list]
    assert len({charge.attribution.component_event_id for charge in charges}) == 2
    assert all(charge.customer_charge == charge.provider_cost for charge in charges)


async def test_prepare_does_no_paid_work_until_caller_admission_and_heartbeat(
    selected_batch, monkeypatch
):
    h = selected_batch
    prepared = await h.worker._prepare_item_for_execution(h.job, h.item())
    assert prepared.primary_deployment is None
    assert not h.calls and not h.checkpoints.writes
    engine = h.worker._execution_engine
    acquire = engine._acquire_prepared_policy_lease
    events = []

    async def admitted(**kwargs):
        await acquire(**kwargs)
        events.append("admitted")

    write = h.checkpoints.write

    async def persist(*args, **kwargs):
        assert events == ["admitted"]
        assert prepared.policy_lease is not None
        await write(*args, **kwargs)

    monkeypatch.setattr(engine, "_acquire_prepared_policy_lease", admitted)
    monkeypatch.setattr(h.checkpoints, "write", persist)
    await engine._execute_prepared_chat_item(h.job, prepared)
    assert len(h.repository.completed_calls) == 1


async def test_reclaim_reuses_decision_and_stable_identity_without_second_charge(selected_batch):
    h = selected_batch
    item = h.item()
    prepared = await h.worker._prepare_item_for_execution(h.job, item)
    await h.worker._execution_engine._execute_prepared_chat_item(h.job, prepared)
    recovered = replace(item, claim_epoch=item.claim_epoch + 1)
    await h.worker._process_item(h.job, recovered)
    assert len(selection_calls(h)) == 1 and len(answer_calls(h)) == 2
    assert len(h.checkpoints.writes) == 2
    h.billing.reserve.assert_awaited_once()
    h.billing.accept_selector.assert_awaited_once()
    assert len({row["billing_event_id"] for row in h.repository.completion_outbox_calls}) == 1


async def test_equivalent_policy_republication_does_not_repeat_selector(selected_batch):
    h = selected_batch
    item = h.item()
    await h.worker._process_item(h.job, item)
    original_identity = item.selector_checkpoint["decision"]["policy_identity"]
    h.policy["policy_version"] = 19
    _publish(h.app, [h.policy])
    await h.worker._process_item(h.job, replace(item, claim_epoch=1))
    assert len(selection_calls(h)) == 1 and len(answer_calls(h)) == 2
    assert item.selector_checkpoint["decision"]["policy_identity"] == original_identity


async def test_selector_pass_through_uses_regular_provider_prices_and_answer_uses_batch_rates(
    selected_batch,
):
    from decimal import Decimal

    h = selected_batch
    for member in h.app.state.model_registry["backing"]:
        member["model_info"].update(
            batch_input_cost_per_token=0.0000001, batch_output_cost_per_token=0.0000002
        )
    _publish(h.app, [h.policy])
    await h.worker._process_item(h.job, h.item())
    charge = h.billing.accept_selector.call_args.args[1]
    assert charge.pricing.input_cost_per_token == Decimal("0.000001")
    assert charge.provider_cost == charge.customer_charge == Decimal("0.000023")
    assert h.repository.completed_calls[0]["billed_cost"] == pytest.approx(0.0000023)


@pytest.mark.parametrize(
    "change", ["input", "policy", "removed", "principal", "malformed", "pending"]
)
async def test_replay_fails_closed_on_changed_or_uncertain_checkpoint(selected_batch, change):
    h = selected_batch
    item = h.item()
    await h.worker._process_item(h.job, item)
    assert len(h.repository.completed_calls) == 1
    if change == "input":
        item.request_body["messages"][0]["content"] = "different"
    elif change == "policy":
        h.policy["selector"]["lanes"][0]["description"] = "Changed interpretation"
        _publish(h.app, [h.policy])
    elif change == "removed":
        h.policy.pop("selector")
        for member in h.policy["members"]:
            member.pop("lane")
        _publish(h.app, [h.policy])
    elif change == "principal":
        h.job.created_by_owner_account_id = "another-owner"
    elif change == "malformed":
        item.selector_checkpoint = {"version": "invalid"}
    elif change == "pending":
        item.selector_checkpoint["decision"] = None
    with pytest.raises(BatchSelectorUnavailable):
        await h.worker._prepare_item_for_execution(h.job, item)
    assert len(h.calls) == 2


async def test_receipt_checkpoint_crash_window_never_reissues_paid_selector(selected_batch):
    h = selected_batch
    h.checkpoints.fail_finish = True
    item = h.item()
    await h.worker._process_item(h.job, item)
    h.billing.accept_selector.assert_awaited_once()
    assert not answer_calls(h) and not h.repository.completed_calls
    assert item.selector_checkpoint["decision"] is None
    assert h.repository.failed_calls[0]["retryable"] is True
    with pytest.raises(BatchSelectorUnavailable):
        await h.worker._prepare_item_for_execution(h.job, replace(item, claim_epoch=1))
    assert len(selection_calls(h)) == 1


@pytest.mark.parametrize(
    "dependency", ["key_service", "budget_service", "limit_counter", "selector_execution_factory"]
)
async def test_missing_dependencies_never_authorize_paid_work(selected_batch, dependency):
    h = selected_batch
    setattr(h.app.state, dependency, None)
    await h.worker._process_item(h.job, h.item())
    assert not h.calls and not h.checkpoints.writes
    h.billing.reserve.assert_not_awaited()


@pytest.mark.parametrize("failure", ["budget", "rate"])
async def test_admission_denial_makes_no_checkpoint_or_classifier_call(
    selected_batch, monkeypatch, failure
):
    h = selected_batch
    if failure == "budget":
        h.app.state.budget_service.check_budgets = AsyncMock(side_effect=BudgetExceededError())
    else:
        monkeypatch.setattr(
            h.worker._execution_engine,
            "_acquire_prepared_policy_lease",
            AsyncMock(side_effect=RateLimitError()),
        )
    await h.worker._process_item(h.job, h.item())
    assert not h.calls and not h.checkpoints.writes
    h.billing.reserve.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["invalid", "unknown", "provider_error"])
async def test_safe_default_is_saved_once_and_reused(selected_batch, outcome):
    h = selected_batch
    h.selector_reply = "not JSON" if outcome == "invalid" else '{"lane":"missing"}'
    if outcome == "provider_error":
        h.selector_error = 503
    item = h.item()
    await h.worker._process_item(h.job, item)
    assert answer_calls(h)[0]["model"] == "quality"
    assert item.selector_checkpoint["decision"]["lane"] == "quality"
    await h.worker._process_item(h.job, replace(item, claim_epoch=1))
    assert len(selection_calls(h)) == 1


async def test_worker_cancellation_closes_classifier_and_never_starts_answer(selected_batch):
    h = selected_batch
    h.selector_gate = asyncio.Event()
    item = h.item()
    task = asyncio.create_task(h.worker._process_item(h.job, item))
    await asyncio.wait_for(h.selector_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.selector_closed.is_set() and not answer_calls(h)
    assert item.selector_checkpoint["decision"] is None
    assert not h.repository.completed_calls


async def test_lease_loss_during_selector_cancels_without_answer(selected_batch):
    h = selected_batch
    h.selector_gate = asyncio.Event()
    item = h.item()
    task = asyncio.create_task(h.worker._process_item(h.job, item))
    await asyncio.wait_for(h.selector_started.wait(), timeout=1)
    h.repository.renew_item_lease = AsyncMock(return_value=False)
    await asyncio.wait_for(task, timeout=1)
    assert h.selector_closed.is_set() and not answer_calls(h)
    assert not h.repository.completed_calls


async def test_expired_job_never_starts_selection(selected_batch):
    h = selected_batch
    h.job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await h.worker._process_item(h.job, h.item())
    assert not h.calls and not h.checkpoints.writes


async def test_selected_items_never_enter_sync_microbatch_and_bound_provider_concurrency(
    selected_batch,
):
    h = selected_batch
    for member in h.app.state.model_registry["backing"]:
        member["deltallm_params"]["chat_batching"] = {
            "mode": "sync_microbatch",
            "upstream_max_batch_size": 20,
        }
    _publish(h.app, [h.policy])
    h.app.state.chat_microbatch_executor = AsyncMock(
        side_effect=AssertionError("must execute individually")
    )
    h.selector_gate = asyncio.Event()
    task = asyncio.create_task(h.worker._process_items(h.job, [h.item(i) for i in range(1, 7)]))
    await asyncio.wait_for(h.selector_started.wait(), timeout=1)
    h.selector_gate.set()
    await task
    assert len(selection_calls(h)) == 6 and len(answer_calls(h)) == 6
    assert 1 <= h.peak <= 2
    h.app.state.chat_microbatch_executor.assert_not_awaited()
