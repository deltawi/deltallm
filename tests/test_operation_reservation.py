from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.billing.operation_reservation import (
    BoundedTokenQuote,
    OperationReservation,
    ProviderEnforcedSelectorCeiling,
)
from tests.test_selector_charge import make_selector_charge


def make_operation():
    charge = make_selector_charge()
    selector = BoundedTokenQuote(
        pricing=charge.pricing,
        max_input_tokens=1000,
        max_output_tokens=64,
        max_attempts=1,
        basis="provider-input-context-ceiling:v1",
    )
    answer = BoundedTokenQuote(
        pricing=charge.pricing,
        max_input_tokens=32000,
        max_output_tokens=4000,
        max_attempts=4,
        basis="bounded-answer-plan:v1",
    )
    return OperationReservation(
        attribution=charge.attribution,
        owner_token=uuid4(),
        selector=selector,
        selector_ceiling=ProviderEnforcedSelectorCeiling(
            deployment_id=charge.attribution.deployment_id,
            context_window_tokens=1000,
            contract_version="qualified-mock-context:v1",
            billing_dimensions="input_output_cache_read_request",
        ),
        answer=answer,
        expires_at=datetime.now(UTC) + timedelta(seconds=5),
    )


def test_exact_operation_allowance_covers_selector_and_all_answer_attempts():
    operation = make_operation()
    expected = operation.selector.allowance + operation.answer.allowance
    with localcontext() as context:
        context.prec = 5
        assert operation.total_allowance == expected
    assert operation.total_allowance > operation.answer.allowance
    assert operation.attribution.component_event_id != str(operation.attribution.operation_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_attempts", 0),
        ("max_attempts", 129),
        ("max_input_tokens", -1),
        ("max_output_tokens", 2**31),
        ("max_input_tokens", 1.1),
    ],
)
def test_unbounded_or_invalid_quotes_fail_closed(field, value):
    values = make_operation().selector.model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        BoundedTokenQuote(**values)


def test_selector_is_one_attempt_with_a_fixed_output_ceiling():
    operation = make_operation()
    values = operation.model_dump()
    values["selector"]["max_attempts"] = 2
    with pytest.raises(ValidationError, match="one bounded"):
        OperationReservation(**values)


def test_quote_reserves_the_maximum_cached_or_uncached_rate():
    operation = make_operation()
    pricing = operation.selector.pricing.model_copy(
        update={"input_cost_per_token_cache_hit": Decimal("0.01")}
    )
    quote = operation.selector.model_copy(update={"pricing": pricing})
    assert quote.allowance >= Decimal(10)


@pytest.mark.parametrize(
    "change", ["missing", "underquoted", "wrong_target", "unsupported_billing"]
)
def test_selector_requires_a_matching_qualified_provider_ceiling(change):
    values = make_operation().model_dump()
    if change == "missing":
        del values["selector_ceiling"]
    elif change == "underquoted":
        values["selector"]["max_input_tokens"] = 999
    elif change == "wrong_target":
        values["selector_ceiling"]["deployment_id"] = "other"
    else:
        values["selector_ceiling"]["billing_dimensions"] = "cache_creation"
    with pytest.raises(ValidationError):
        OperationReservation(**values)
