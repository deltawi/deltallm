"""A supervised processor keeps one exact handle and never claims behind it."""

import asyncio
from collections import deque
from time import monotonic
from uuid import uuid4

import pytest

from src.billing.accounting_journal_claims import JournalClaim, JournalFailure
from src.billing.accounting_journal_runtime import (
    MAX_RETAINED_CLAIM_BYTES,
    JournalProcessingWorker,
    JournalWorkerConfig,
)
from src.concurrency import CapacityGateFull
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.telemetry.lifecycle import WorkerState
from src.metrics.prometheus import get_prometheus_registry
from tests.test_preissued_permit_bytes import retained_object_bytes


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


def claim(keys=(1, 2)):
    return JournalClaim(
        protocol_generation=7, worker_id="worker", lease_token=uuid4(), sequences=keys
    )


class Persistence:
    def __init__(self, claims=()):
        self.claims = deque(claims)
        self.claim_calls = []
        self.materialize_calls = []
        self.failure_calls = []
        self.materialize_error = None
        self.failure_error = None
        self.claim_error = None
        self.materialize_result = None
        self.failure_result = None
        self.claim_resume = None
        self.materialize_resume = None
        self.claim_entered = asyncio.Event()
        self.materialize_entered = asyncio.Event()

    async def claim(self, **kwargs):
        self.claim_calls.append(kwargs)
        self.claim_entered.set()
        if self.claim_resume is not None:
            await self.claim_resume.wait()
        if self.claim_error is not None:
            raise self.claim_error
        return self.claims.popleft() if self.claims else claim(())

    async def materialize(self, owned, *, expires_at):
        self.materialize_calls.append((owned, expires_at))
        self.materialize_entered.set()
        if self.materialize_resume is not None:
            await self.materialize_resume.wait()
        if self.materialize_error is not None:
            raise self.materialize_error
        return len(owned.sequences) if self.materialize_result is None else self.materialize_result

    async def fail(self, owned, failure, *, expires_at):
        self.failure_calls.append((owned, failure, expires_at))
        if self.failure_error is not None:
            raise self.failure_error
        return len(owned.sequences) if self.failure_result is None else self.failure_result


def worker(persistence, **kwargs):
    return JournalProcessingWorker(
        persistence, JournalWorkerConfig(generation=7, worker_id="worker", **kwargs)
    )


async def test_one_tick_uses_one_claim_and_one_bulk_commit_with_the_same_deadline():
    persistence = Persistence([claim(tuple(range(1, 65)))])
    runtime = worker(persistence)
    expires_at = deadline()
    assert await runtime.run_once(expires_at=expires_at) == 64
    assert len(persistence.claim_calls) == len(persistence.materialize_calls) == 1
    assert persistence.claim_calls[0]["expires_at"] == expires_at
    assert persistence.materialize_calls[0][1] == expires_at
    assert runtime.retained_claim is None and not persistence.failure_calls


async def test_empty_tick_uses_no_materialization_or_failure_call():
    persistence = Persistence()
    runtime = worker(persistence)
    assert await runtime.run_once(expires_at=deadline()) == 0
    assert len(persistence.claim_calls) == 1
    assert not persistence.materialize_calls and not persistence.failure_calls
    assert runtime.retained_claim is None


@pytest.mark.parametrize("phase", ["cancel", "timeout"])
async def test_interrupted_commit_retains_exact_handle_and_retries_without_a_new_claim(phase):
    owned = claim()
    persistence = Persistence([owned])
    persistence.materialize_resume = asyncio.Event()
    runtime = worker(persistence)
    task = asyncio.create_task(runtime.run_once(expires_at=deadline(0.03)))
    await persistence.materialize_entered.wait()
    if phase == "cancel":
        task.cancel()
    with pytest.raises(asyncio.CancelledError if phase == "cancel" else TimeoutError):
        await task
    assert runtime.retained_claim == owned and not persistence.failure_calls
    persistence.materialize_resume = None
    assert await runtime.run_once(expires_at=deadline()) == 2
    assert len(persistence.claim_calls) == 1
    assert [value[0] for value in persistence.materialize_calls] == [owned, owned]
    assert runtime.retained_claim is None


async def test_unavailable_failure_ack_keeps_exact_claim_until_success():
    owned = claim()
    persistence = Persistence([owned])
    persistence.materialize_error = invalid_result()
    persistence.failure_error = invalid_result()
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert runtime.retained_claim == owned and len(persistence.failure_calls) == 1
    persistence.materialize_error = persistence.failure_error = None
    assert await runtime.run_once(expires_at=deadline()) == 2
    assert len(persistence.claim_calls) == 1 and runtime.retained_claim is None


async def test_definitive_failure_ack_releases_only_the_local_handle():
    owned = claim()
    persistence = Persistence([owned])
    persistence.materialize_error = invalid_result()
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert persistence.failure_calls[0][:2] == (owned, JournalFailure.PERSISTENCE)
    assert runtime.retained_claim is None
    assert len(persistence.claim_calls) == len(persistence.materialize_calls) == 1


@pytest.mark.parametrize("change", ["kind", "generation", "worker", "bool", "duplicate", "length"])
async def test_invalid_claim_cannot_materialize_or_drop_another_owners_proof(change):
    values = {
        "kind": object(),
        "generation": claim().model_copy(update={"protocol_generation": 8}),
        "worker": claim().model_copy(update={"worker_id": "other"}),
        "bool": claim().model_copy(update={"sequences": (True,)}),
        "duplicate": claim().model_copy(update={"sequences": (1, 1)}),
        "length": claim(tuple(range(1, 130))),
    }
    persistence = Persistence([values[change]])
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert not persistence.materialize_calls and not persistence.failure_calls
    assert runtime.retained_claim is None


@pytest.mark.parametrize("value", [True, -1, 3, "2", 1])
async def test_invalid_materialization_count_fails_closed(value):
    persistence = Persistence([claim()])
    persistence.materialize_result = value
    persistence.failure_error = invalid_result()
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert runtime.retained_claim == persistence.materialize_calls[0][0]


async def test_overlapping_tick_rejects_without_waiters_or_another_database_call():
    persistence = Persistence([claim()])
    persistence.materialize_resume = asyncio.Event()
    runtime = worker(persistence)
    task = asyncio.create_task(runtime.run_once(expires_at=deadline()))
    await persistence.materialize_entered.wait()
    with pytest.raises(CapacityGateFull):
        await runtime.run_once(expires_at=deadline())
    assert len(persistence.claim_calls) == 1
    persistence.materialize_resume.set()
    assert await task == 2


async def test_start_requires_a_completed_dependency_check_not_only_task_creation():
    persistence = Persistence()
    persistence.claim_resume = asyncio.Event()
    runtime = worker(persistence)
    task = asyncio.create_task(runtime.start(expires_at=deadline()))
    await persistence.claim_entered.wait()
    assert not task.done() and not runtime.worker_health.ready
    persistence.claim_resume.set()
    await task
    assert runtime.worker_health.ready and persistence.claim_calls
    assert await runtime.close(expires_at=deadline())
    assert runtime.worker_health.state is WorkerState.DISABLED
    assert runtime.task.done()
    assert await runtime.close(expires_at=deadline())
    with pytest.raises(RuntimeError, match="cannot restart"):
        await runtime.start(expires_at=deadline())
    with pytest.raises(RuntimeError, match="closed"):
        await runtime.run_once(expires_at=deadline())


async def test_failed_start_is_unready_and_keeps_durable_claim_recoverable():
    persistence = Persistence([claim()])
    persistence.materialize_error = invalid_result()
    persistence.failure_error = invalid_result()
    runtime = worker(persistence)
    with pytest.raises(RuntimeError, match="did not start"):
        await runtime.start(expires_at=deadline())
    assert runtime.task.done() and runtime.worker_health.state is WorkerState.FAILED
    assert runtime.retained_claim == persistence.materialize_calls[0][0]


async def test_close_finishes_one_owned_batch_without_claiming_another():
    persistence = Persistence()
    runtime = worker(persistence, poll_seconds=0.01)
    await runtime.start(expires_at=deadline())
    persistence.materialize_resume = asyncio.Event()
    persistence.claims.extend([claim(), claim((3, 4))])
    await persistence.materialize_entered.wait()
    calls = len(persistence.claim_calls)
    stopped = asyncio.create_task(runtime.close(expires_at=deadline()))
    await asyncio.sleep(0)
    assert runtime.worker_health.state is WorkerState.STOPPING
    persistence.materialize_resume.set()
    assert await stopped
    assert len(persistence.claim_calls) == calls and len(persistence.claims) == 1
    assert runtime.retained_claim is None


async def test_blocked_close_is_bounded_and_keeps_the_owned_claim():
    persistence = Persistence()
    runtime = worker(persistence, poll_seconds=0.01)
    await runtime.start(expires_at=deadline())
    persistence.materialize_resume = asyncio.Event()
    persistence.claims.append(claim())
    await persistence.materialize_entered.wait()
    started = monotonic()
    assert not await runtime.close(expires_at=deadline(0.02))
    assert monotonic() - started < 0.1
    await asyncio.gather(runtime.task, return_exceptions=True)
    assert runtime.retained_claim == persistence.materialize_calls[0][0]
    assert runtime.worker_health.state is WorkerState.FAILED


async def test_unexpected_exit_fails_health_without_exposing_exception_text():
    persistence = Persistence()
    runtime = worker(persistence, poll_seconds=0.01)
    await runtime.start(expires_at=deadline())
    persistence.claim_error = RuntimeError("private financial document")
    await asyncio.gather(runtime.task, return_exceptions=True)
    assert runtime.worker_health.state is WorkerState.FAILED
    assert runtime.worker_health.detail == "task_failed"
    assert not await runtime.close(expires_at=deadline())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"generation": True},
        {"worker_id": ""},
        {"batch_size": 257},
        {"batch_size": True},
        {"lease_seconds": 4},
        {"poll_seconds": float("nan")},
        {"call_budget_seconds": float("inf")},
        {"backoff_max_seconds": 31},
    ],
)
def test_configuration_is_strict_and_bounded(kwargs):
    with pytest.raises(ValueError):
        JournalWorkerConfig.model_validate({"generation": 7, "worker_id": "worker", **kwargs})


def test_copied_configuration_is_revalidated():
    config = JournalWorkerConfig(generation=7, worker_id="worker")
    with pytest.raises(ValueError):
        JournalProcessingWorker(Persistence(), config.model_copy(update={"batch_size": True}))


@pytest.mark.parametrize("failures", [0, 1, 8, 1000])
def test_jitter_and_exponential_backoff_stay_within_fixed_bounds(failures):
    runtime = worker(Persistence(), poll_seconds=0.01)
    runtime._failures = min(8, failures)
    samples = [runtime._next_delay() for _ in range(20)]
    if failures:
        base = min(5, 0.01 * 2 ** min(8, failures))
        assert all(base / 2 <= value <= base for value in samples)
    else:
        assert all(0.008 <= value <= 0.012 for value in samples)


@pytest.mark.parametrize("expires_at", [float("nan"), float("inf"), True])
async def test_invalid_deadlines_reject_before_persistence(expires_at):
    persistence = Persistence()
    runtime = worker(persistence)
    with pytest.raises(ValueError):
        await runtime.run_once(expires_at=expires_at)
    assert not persistence.claim_calls


async def test_expired_deadline_rejects_before_persistence():
    persistence = Persistence()
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline(-1))
    assert not persistence.claim_calls


async def test_fixed_claim_charge_covers_the_largest_allowed_unicode_and_key_graph():
    owned = JournalClaim(
        protocol_generation=2**63 - 1,
        worker_id="😀" * 256,
        lease_token=uuid4(),
        sequences=tuple(range(2**63 - 257, 2**63 - 1)),
    )
    persistence = Persistence([owned])
    persistence.materialize_error = asyncio.CancelledError()
    runtime = JournalProcessingWorker(
        persistence,
        JournalWorkerConfig(
            generation=owned.protocol_generation, worker_id=owned.worker_id, batch_size=256
        ),
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime.run_once(expires_at=deadline())
    assert runtime.retained_claim_bytes == MAX_RETAINED_CLAIM_BYTES
    assert retained_object_bytes(runtime.retained_claim) <= runtime.retained_claim_bytes
    assert set(runtime.retained_claim.model_dump()) == {
        "protocol_generation",
        "worker_id",
        "lease_token",
        "sequences",
    }


def action_count(action, outcome):
    return (
        get_prometheus_registry().get_sample_value(
            "deltallm_accounting_journal_worker_actions_total",
            {"action": action, "outcome": outcome},
        )
        or 0
    )


async def test_action_metrics_use_fixed_classes_and_count_each_stage_once():
    before = {
        (action, outcome): action_count(action, outcome)
        for action, outcome in (
            ("claim", "success"),
            ("materialize", "success"),
            ("claim", "empty"),
        )
    }
    runtime = worker(Persistence([claim()]))
    assert await runtime.run_once(expires_at=deadline()) == 2
    assert await runtime.run_once(expires_at=deadline()) == 0
    for key, value in before.items():
        assert action_count(*key) == value + 1


async def test_cancelled_action_is_not_a_failure_transition_or_success_metric():
    before = action_count("materialize", "cancelled")
    persistence = Persistence([claim()])
    persistence.materialize_error = asyncio.CancelledError()
    runtime = worker(persistence)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run_once(expires_at=deadline())
    assert action_count("materialize", "cancelled") == before + 1
    assert not persistence.failure_calls and runtime.retained_claim is not None
