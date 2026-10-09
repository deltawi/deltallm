from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.charges.realtime_charge import RealtimeAttribution, RealtimeChargeContext
from src.billing.charges.realtime_pricing import RealtimePrices
from src.billing.charges.realtime_usage import RealtimeTokenUsage, RealtimeUsageReceipt


def charge_context(identity="owner"):
    prices = RealtimePrices.from_model_info(
        {
            "input_cost_per_token": "0.000001",
            "output_cost_per_token": "0.000002",
            "input_cost_per_audio_token": "0.000003",
            "output_cost_per_audio_token": "0.000004",
            "input_cost_per_audio_token_cache_hit": "0.0000003",
            "input_cost_per_second": "0.0001",
        }
    )
    return RealtimeChargeContext(
        RealtimeAttribution(
            str(uuid4()),
            identity,
            identity,
            identity,
            identity,
            None,
            "voice",
            "deployment",
            "gpt-realtime",
        ),
        prices,
        prices,
        datetime.now(UTC),
    )


def receipt(session_id):
    from src.billing.charges.realtime_usage import realtime_usage_receipt

    return realtime_usage_receipt(
        session_id,
        {
            "type": "response.done",
            "response": {
                "id": "resp_1",
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 7,
                    "total_tokens": 37,
                    "input_token_details": {
                        "text_tokens": 10,
                        "audio_tokens": 20,
                        "cached_tokens": 5,
                        "cached_tokens_details": {"text_tokens": 2, "audio_tokens": 3},
                    },
                    "output_token_details": {"text_tokens": 3, "audio_tokens": 4},
                },
            },
        },
    )


def test_exact_exclusive_pricing_and_frozen_snapshot():
    context = charge_context()
    usage = receipt(context.attribution.session_id)
    assert context.customer.cost(usage.usage) == Decimal("0.0000839")
    with pytest.raises(TypeError):
        context.customer.rates["input_text"] = Decimal(99)
    payload = context.spend_payload(
        usage, operation_started_at=context.started_at, completed_at=context.started_at
    )
    assert payload["usage"]["total_tokens"] == 37
    assert payload["cost_exact"] == "0.000083900000000000"
    assert payload["metadata"]["billing"]["realtime_components"]["cached_input_audio"] == "3"
    assert "owner" not in repr(context)


@pytest.mark.parametrize("value", [False, "", "NaN", "Infinity", "-1", object()])
def test_invalid_rates_and_fees_fail_closed(value):
    for field in ("input_cost_per_token", "cost_per_request"):
        with pytest.raises(ValueError):
            RealtimePrices.from_model_info({field: value})


def test_missing_zero_usage_dimensions_do_not_require_prices_but_admission_does():
    prices = RealtimePrices.from_model_info({"input_cost_per_token": "0.01"})
    assert prices.cost(RealtimeTokenUsage(2, 0, 0, 0, 0, 0)) == Decimal("0.02")
    with pytest.raises(ValueError):
        prices.require_profile(transcription=False)


def test_missing_usage_cannot_be_made_into_a_zero_charge():
    context = charge_context()
    pending = RealtimeUsageReceipt(str(uuid4()), "response", "resp_1", None, "usage_missing")
    with pytest.raises(ValueError):
        context.spend_payload(
            pending, operation_started_at=context.started_at, completed_at=context.started_at
        )


def test_turn_timing_excludes_idle_session_time():
    context = charge_context()
    started = context.started_at + timedelta(minutes=2)
    payload = context.spend_payload(
        receipt(context.attribution.session_id),
        operation_started_at=started,
        completed_at=started + timedelta(seconds=2),
    )
    assert datetime.fromisoformat(payload["end_time"]) - datetime.fromisoformat(
        payload["start_time"]
    ) == timedelta(seconds=2)
    assert payload["metadata"]["realtime_session_started_at"] == context.started_at.isoformat()
