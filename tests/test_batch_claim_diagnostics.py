from __future__ import annotations

from src.batch.claim_diagnostics import (
    BatchClaimDecisionDiagnostic,
    ClaimDecisionDiagnosticProbeLimiter,
)


def test_claim_decision_diagnostic_probe_limiter_caches_only_low_cardinality_context() -> None:
    limiter = ClaimDecisionDiagnosticProbeLimiter(window_seconds=10.0, max_keys=10)
    key = ("empty", "slice_v1")

    first = limiter.should_probe(key, now=0.0)
    assert first.should_probe is True
    assert first.cached_diagnostic is None
    assert first.suppressed_count == 0

    limiter.record_probe_result(
        key,
        BatchClaimDecisionDiagnostic(
            reason="not_before_future",
            batch_id="batch-1",
            model_group="model-a",
            service_tier="standard",
            tenant_scope_type="team",
            tenant_scope_id="team-1",
            head_item_work_units=42,
        ),
    )

    second = limiter.should_probe(key, now=1.0)
    assert second.should_probe is False
    assert second.suppressed_count == 1
    assert second.cached_diagnostic is not None
    assert second.cached_diagnostic.reason == "not_before_future"
    assert second.cached_diagnostic.model_group == "model-a"
    assert second.cached_diagnostic.service_tier == "standard"
    assert second.cached_diagnostic.batch_id is None
    assert second.cached_diagnostic.tenant_scope_id is None
    assert second.cached_diagnostic.head_item_work_units is None

    third = limiter.should_probe(key, now=11.0)
    assert third.should_probe is True
    assert third.suppressed_count == 1
    assert third.cached_diagnostic is not None
    assert third.cached_diagnostic.reason == "not_before_future"


def test_claim_decision_diagnostic_probe_limiter_retries_failures_before_success_window() -> None:
    limiter = ClaimDecisionDiagnosticProbeLimiter(window_seconds=60.0, max_keys=10)
    key = ("empty", "slice_v1")

    first = limiter.should_probe(key, now=0.0)
    assert first.should_probe is True

    limiter.record_probe_failure(key, now=0.0)

    second = limiter.should_probe(key, now=1.0)
    assert second.should_probe is False
    assert second.suppressed_count == 1

    third = limiter.should_probe(key, now=5.0)
    assert third.should_probe is True
    assert third.suppressed_count == 1


def test_claim_decision_diagnostic_probe_limiter_preserves_cache_after_failure() -> None:
    limiter = ClaimDecisionDiagnosticProbeLimiter(window_seconds=60.0, max_keys=10)
    key = ("empty", "slice_v1")

    assert limiter.should_probe(key, now=0.0).should_probe is True
    limiter.record_probe_result(
        key,
        BatchClaimDecisionDiagnostic(
            reason="not_before_future",
            model_group="model-a",
            service_tier="standard",
            batch_id="batch-1",
        ),
    )

    refresh = limiter.should_probe(key, now=61.0)
    assert refresh.should_probe is True
    assert refresh.cached_diagnostic is not None
    assert refresh.cached_diagnostic.reason == "not_before_future"

    limiter.record_probe_failure(key, now=61.0)

    suppressed = limiter.should_probe(key, now=62.0)
    assert suppressed.should_probe is False
    assert suppressed.cached_diagnostic is not None
    assert suppressed.cached_diagnostic.reason == "not_before_future"
    assert suppressed.cached_diagnostic.batch_id is None


def test_claim_decision_log_key_ignores_high_cardinality_context() -> None:
    first = BatchClaimDecisionDiagnostic(
        reason="not_before_future",
        diagnostic_source="db",
        batch_id="batch-1",
        model_group="model-a",
        service_tier="standard",
        tenant_scope_type="org",
        tenant_scope_id="team-1",
        head_item_work_units=10,
    )
    second = BatchClaimDecisionDiagnostic(
        reason="not_before_future",
        diagnostic_source="cached",
        batch_id="batch-2",
        model_group="model-a",
        service_tier="standard",
        tenant_scope_type="team",
        tenant_scope_id="team-2",
        head_item_work_units=99,
    )

    assert first.log_key(
        decision="empty",
        claim_mode="slice_v1",
        scheduler_mode="slice_v1",
    ) == second.log_key(
        decision="empty",
        claim_mode="slice_v1",
        scheduler_mode="slice_v1",
    )
