from __future__ import annotations

import asyncio
from threading import Event, Lock

import pytest

from src.auth.external_crypto import ExternalCryptoExecutor
from src.auth.external_contracts import ExternalPurpose
from src.auth.external_errors import (
    ExternalAuthError,
    ExternalAuthUnavailable,
    InvalidExternalAssertion,
)
from src.auth.external_config import ExternalAuthSettings
from src.services.external_auth_admission import ExternalAuthAdmission
from src.models.errors import RateLimitError, ServiceUnavailableError
from tests.auth.test_external_assertions import NOW, valid_claims
from src.auth.external_contracts import ExternalAssertionClaims, VerifiedExternalAssertion


class BlockingVerifier:
    def __init__(self):
        self.release = Event()
        self.started = Event()
        self.lock = Lock()
        self.active = 0
        self.maximum = 0

    def verify(self, token, **kwargs):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if self.active == 2:
                self.started.set()
        try:
            if not self.release.wait(5):
                raise RuntimeError("Test did not release verification")
            return token
        finally:
            with self.lock:
                self.active -= 1


@pytest.mark.asyncio
async def test_crypto_work_remains_bounded_after_cancellation_and_queue_overflow():
    verifier = BlockingVerifier()
    executor = ExternalCryptoExecutor(verifier)
    await executor.start()
    tasks = []
    try:
        for index in range(2):
            tasks.append(
                asyncio.create_task(
                    executor.verify(str(index), purposes=(ExternalPurpose.EXCHANGE,), now=NOW)
                )
            )
        assert await asyncio.to_thread(verifier.started.wait, 1)
        tasks[0].cancel()
        await asyncio.gather(tasks[0], return_exceptions=True)
        for index in range(8):
            tasks.append(
                asyncio.create_task(
                    executor.verify(
                        "queued-" + str(index), purposes=(ExternalPurpose.EXCHANGE,), now=NOW
                    )
                )
            )
        await asyncio.sleep(0)
        with pytest.raises(ExternalAuthUnavailable):
            await executor.verify("overflow", purposes=(ExternalPurpose.EXCHANGE,), now=NOW)
        assert verifier.active == 2 and verifier.maximum == 2
        tasks[2].cancel()
        verifier.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert isinstance(results[2], asyncio.CancelledError)
        assert results[1] == "1" and verifier.maximum == 2
    finally:
        verifier.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await executor.close()
    assert not executor.ready
    with pytest.raises(ExternalAuthUnavailable):
        await executor.verify("closed", purposes=(ExternalPurpose.EXCHANGE,), now=NOW)


@pytest.mark.asyncio
async def test_verification_worker_survives_invalid_assertion():
    class Verifier:
        def verify(self, token, **kwargs):
            if token == "invalid":
                raise InvalidExternalAssertion()
            return token

    executor = ExternalCryptoExecutor(Verifier())
    await executor.start()
    try:
        with pytest.raises(InvalidExternalAssertion):
            await executor.verify("invalid", purposes=(ExternalPurpose.EXCHANGE,), now=NOW)
        assert (
            await executor.verify("valid", purposes=(ExternalPurpose.EXCHANGE,), now=NOW) == "valid"
        )
        assert executor.ready
    finally:
        await executor.close()


class Counter:
    redis = object()
    degraded_mode = "fail_closed"

    def __init__(self, error=None):
        self.error = error
        self.checks = []

    async def check_rate_limits_atomic(self, checks):
        self.checks = checks
        if self.error:
            raise self.error


@pytest.mark.parametrize(
    "error,status",
    [(RateLimitError(), 429), (ServiceUnavailableError(), 503), (TimeoutError(), 503)],
)
@pytest.mark.asyncio
async def test_distributed_admission_fails_closed_with_generic_errors(error, status):
    admission = ExternalAuthAdmission(Counter(error), ExternalAuthSettings(), environment="test")
    assertion = VerifiedExternalAssertion(
        "console", ExternalAssertionClaims.model_validate(valid_claims())
    )
    with pytest.raises(ExternalAuthError) as denied:
        await admission.admit(assertion)
    assert denied.value.status_code == status


@pytest.mark.asyncio
async def test_atomic_admission_uses_one_cluster_slot_without_raw_subjects():
    counter = Counter()
    admission = ExternalAuthAdmission(counter, ExternalAuthSettings(), environment="test")
    assertion = VerifiedExternalAssertion(
        "console", ExternalAssertionClaims.model_validate(valid_claims())
    )
    await admission.admit(assertion)
    assert len(counter.checks) == 3
    slots = {check.entity_id.split("}", 1)[0] for check in counter.checks}
    assert len(slots) == 1
    assert assertion.claims.sub not in repr(
        counter.checks
    ) and assertion.claims.binding_id not in repr(counter.checks)


def test_admission_requires_shared_fail_closed_backend():
    for counter in (
        type("Counter", (), {"redis": None, "degraded_mode": "fail_closed"})(),
        type("Counter", (), {"redis": object(), "degraded_mode": "fail_open"})(),
    ):
        with pytest.raises(ValueError):
            ExternalAuthAdmission(counter, ExternalAuthSettings(), environment="test")


async def test_unverified_denial_auditing_has_one_global_bounded_rate_bucket():
    counter = Counter()
    admission = ExternalAuthAdmission(counter, ExternalAuthSettings(), environment="test")
    await admission.admit_denial_audit()
    assert len(counter.checks) == 1 and counter.checks[0].limit == 60
    assert counter.checks[0].entity_id == "{unverified}"
