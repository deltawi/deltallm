import pytest

from src.cache.execution_eligibility import ResponseCacheEligibility, ResponseCacheOutcome


@pytest.mark.parametrize("outcome", list(ResponseCacheOutcome))
def test_only_explicit_miss_or_bypass_allows_provider_execution(outcome):
    eligibility = ResponseCacheEligibility(outcome)
    if outcome in (ResponseCacheOutcome.MISS, ResponseCacheOutcome.BYPASS):
        eligibility.require_provider_execution()
    else:
        with pytest.raises(RuntimeError, match="cache"):
            eligibility.require_provider_execution()
