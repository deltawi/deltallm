from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.charges.realtime_usage import (
    normalize_realtime_tokens,
    price_realtime_usage,
    realtime_usage_receipt,
)


def usage():
    return {
        "total_tokens": 253,
        "input_tokens": 132,
        "output_tokens": 121,
        "input_token_details": {
            "text_tokens": 119,
            "audio_tokens": 13,
            "image_tokens": 0,
            "cached_tokens": 64,
            "cached_tokens_details": {"text_tokens": 64, "audio_tokens": 0, "image_tokens": 0},
        },
        "output_token_details": {"text_tokens": 30, "audio_tokens": 91},
    }


def test_text_audio_and_cached_units_are_exclusive_and_exact():
    normalized = normalize_realtime_tokens(usage())
    assert normalized.input_text == 55
    assert normalized.input_audio == 13
    assert normalized.cached_input_text == 64
    assert normalized.output_text == 30
    assert normalized.output_audio == 91
    amount = price_realtime_usage(
        normalized,
        prices={
            "input_text": Decimal("0.000001"),
            "input_audio": Decimal("0.00001"),
            "cached_input_text": Decimal("0.0000005"),
            "output_text": Decimal("0.000002"),
            "output_audio": Decimal("0.00002"),
        },
    )
    assert amount == Decimal("0.002097")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda u: u.pop("input_token_details"),
        lambda u: u["input_token_details"].pop("cached_tokens_details"),
        lambda u: u["input_token_details"].update(audio_tokens=True),
        lambda u: u["input_token_details"].update(text_tokens=-1),
        lambda u: u["input_token_details"].update(image_tokens=1),
        lambda u: u.update(total_tokens=999),
        lambda u: u["input_token_details"]["cached_tokens_details"].update(text_tokens=999),
        lambda u: u["output_token_details"].update(audio_tokens=1.5),
    ],
)
def test_unqualified_usage_remains_pending_instead_of_free(mutation):
    value = usage()
    mutation(value)
    receipt = realtime_usage_receipt(
        str(uuid4()), {"type": "response.done", "response": {"id": "resp1", "usage": value}}
    )
    assert receipt.usage is None
    assert receipt.pending_reason == "usage_unqualified"


def test_missing_receipt_usage_is_pending_and_idempotency_ignores_event_id():
    session = str(uuid4())
    event = {"type": "response.done", "event_id": "client-controlled", "response": {"id": "resp1"}}
    first = realtime_usage_receipt(session, event)
    event["event_id"] = "another"
    assert realtime_usage_receipt(session, event).receipt_id == first.receipt_id
    assert realtime_usage_receipt(str(uuid4()), event).receipt_id != first.receipt_id
    assert first.pending_reason == "usage_missing"


def test_transcription_is_a_separate_operation_with_text_output():
    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item1",
        "content_index": 0,
        "transcript": "private",
        "usage": {
            "type": "tokens",
            "total_tokens": 26,
            "input_tokens": 17,
            "input_token_details": {"text_tokens": 0, "audio_tokens": 17},
            "output_tokens": 9,
        },
    }
    session = str(uuid4())
    receipt = realtime_usage_receipt(session, event)
    assert receipt.operation == "transcription"
    assert receipt.usage.input_audio == 17
    assert receipt.usage.output_text == 9
    assert "private" not in repr(receipt)
    other = deepcopy(event)
    other["content_index"] = 1
    assert realtime_usage_receipt(session, other).receipt_id != receipt.receipt_id


def test_reported_duration_is_priced_exactly_without_guessing_tokens():
    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "i",
        "content_index": 0,
        "usage": {"type": "duration", "seconds": 2.4},
    }
    receipt = realtime_usage_receipt(str(uuid4()), event)
    assert receipt.pending_reason is None
    assert receipt.usage.seconds == Decimal("2.4")
    assert price_realtime_usage(receipt.usage, prices={"seconds": Decimal("0.0001")}) == Decimal(
        "0.00024"
    )


@pytest.mark.parametrize("seconds", [True, -1, float("nan"), float("inf"), "2.4", None, 86401])
def test_invalid_duration_remains_pending(seconds):
    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "i",
        "content_index": 0,
        "usage": {"type": "duration", "seconds": seconds},
    }
    receipt = realtime_usage_receipt(str(uuid4()), event)
    assert receipt.usage is None
    assert receipt.pending_reason == "usage_unqualified"


def test_unknown_top_level_charge_dimension_is_not_ignored():
    value = usage()
    value["additional_billable_units"] = 5
    with pytest.raises(ValueError):
        normalize_realtime_tokens(value)


@pytest.mark.parametrize("price", [None, 0.1, Decimal("NaN"), Decimal("-1")])
def test_prices_must_be_explicit_exact_and_nonnegative(price):
    with pytest.raises(ValueError):
        price_realtime_usage(normalize_realtime_tokens(usage()), prices={"input_text": price})
