from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID, uuid5

from src.billing.money import canonical_money


@dataclass(frozen=True, slots=True)
class RealtimeTokenUsage:
    input_text: int
    input_audio: int
    cached_input_text: int
    cached_input_audio: int
    output_text: int
    output_audio: int

    def __post_init__(self) -> None:
        for item in fields(self):
            _count(getattr(self, item.name))


@dataclass(frozen=True, slots=True)
class RealtimeUsageReceipt:
    receipt_id: str
    operation: Literal["response", "transcription"]
    provider_id: str
    usage: RealtimeTokenUsage | None
    pending_reason: str | None


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53:
        raise ValueError("invalid token count")
    return value


def _object(value: object) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError("missing usage details")
    return value


def _known_dimensions(details: Mapping, supported: set[str]) -> None:
    for key, value in details.items():
        if key not in supported and value not in (0, None):
            raise ValueError("unsupported usage dimension")


def normalize_realtime_tokens(usage: Mapping, *, transcription: bool = False) -> RealtimeTokenUsage:
    """Exclusive priced dimensions: cached tokens are a subset of input.

    Aggregate totals validate the receipt, but are never charged in addition
    to the text/audio components. Unsupported or incomplete usage must remain
    pending with the durable accounting owner, never become a zero-cost turn.
    """
    _known_dimensions(
        usage,
        {
            "type",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_token_details",
            "output_token_details",
        },
    )
    if usage.get("type", "tokens") != "tokens":
        raise ValueError("unsupported usage type")
    inputs = _object(usage.get("input_token_details"))
    _known_dimensions(
        inputs, {"text_tokens", "audio_tokens", "cached_tokens", "cached_tokens_details"}
    )
    text = _count(inputs.get("text_tokens"))
    audio = _count(inputs.get("audio_tokens"))
    cached_total = _count(inputs.get("cached_tokens", 0))
    cached = _object(inputs.get("cached_tokens_details", {}))
    _known_dimensions(cached, {"text_tokens", "audio_tokens"})
    cached_text = _count(cached.get("text_tokens", 0))
    cached_audio = _count(cached.get("audio_tokens", 0))
    if cached_total != cached_text + cached_audio or cached_text > text or cached_audio > audio:
        raise ValueError("inconsistent cached usage")
    if transcription:
        if usage.get("type") != "tokens":
            raise ValueError("unsupported transcription usage")
        output_text = _count(usage.get("output_tokens"))
        output_audio = 0
        if usage.get("output_token_details") is not None:
            outputs = _object(usage["output_token_details"])
            _known_dimensions(outputs, {"text_tokens"})
            if outputs.get("text_tokens") != output_text:
                raise ValueError("inconsistent transcription output")
    else:
        outputs = _object(usage.get("output_token_details"))
        _known_dimensions(outputs, {"text_tokens", "audio_tokens"})
        output_text = _count(outputs.get("text_tokens"))
        output_audio = _count(outputs.get("audio_tokens"))
    input_total = _count(usage.get("input_tokens"))
    output_total = _count(usage.get("output_tokens"))
    if text + audio != input_total or output_text + output_audio != output_total:
        raise ValueError("inconsistent token totals")
    if "total_tokens" in usage and _count(usage["total_tokens"]) != input_total + output_total:
        raise ValueError("inconsistent aggregate usage")
    return RealtimeTokenUsage(
        text - cached_text,
        audio - cached_audio,
        cached_text,
        cached_audio,
        output_text,
        output_audio,
    )


def realtime_usage_receipt(session_id: str, event: Mapping) -> RealtimeUsageReceipt:
    """Stable session/operation identity, independent of a client event_id.

    No transcripts, audio, request bodies or credentials enter the receipt.
    Durable deduplication belongs to spend ingestion, not an in-memory set.
    """
    content_index = 0
    if event.get("type") == "response.done":
        operation = "response"
        response = _object(event.get("response"))
        provider_id = response.get("id")
        usage = response.get("usage")
    elif event.get("type") == "conversation.item.input_audio_transcription.completed":
        operation = "transcription"
        provider_id = event.get("item_id")
        content_index = _count(event.get("content_index"))
        usage = event.get("usage")
    else:
        raise ValueError("event is not a Realtime usage receipt")
    if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 256:
        raise ValueError("missing provider operation identity")
    receipt_id = str(uuid5(UUID(session_id), f"realtime:{operation}:{content_index}:{provider_id}"))
    tokens = None
    pending = "usage_missing" if usage is None else None
    if usage is not None:
        try:
            tokens = normalize_realtime_tokens(
                _object(usage), transcription=operation == "transcription"
            )
        except ValueError:
            pending = "usage_unqualified"
    return RealtimeUsageReceipt(receipt_id, operation, provider_id, tokens, pending)


def price_realtime_usage(usage: RealtimeTokenUsage, *, prices: Mapping[str, Decimal]) -> Decimal:
    """Use an admission-pinned rate card; omitted charged dimensions fail closed."""
    with localcontext() as context:
        context.prec = 80
        total = Decimal(0)
        for item in fields(usage):
            count = getattr(usage, item.name)
            if not count:
                continue
            price = prices.get(item.name)
            if not isinstance(price, Decimal) or not price.is_finite() or price < 0:
                raise ValueError("missing or invalid Realtime price")
            total += count * price
        return canonical_money(total)
