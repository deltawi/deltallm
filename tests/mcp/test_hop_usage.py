import pytest

from src.billing.pricing.pricing import with_reported_prompt_cache
from src.mcp.hop_usage import add_hop_usage


def test_add_hop_usage_sums_counts_and_nested_details() -> None:
    total = add_hop_usage(
        None,
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "prompt_tokens_details": {"cached_tokens": 2},
        },
    )
    total = add_hop_usage(
        total,
        {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 8},
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    )

    assert total == {
        "prompt_tokens": 30,
        "completion_tokens": 9,
        "total_tokens": 39,
        "prompt_tokens_details": {"cached_tokens": 10},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }


def test_add_hop_usage_skips_missing_usage_and_does_not_alias_input() -> None:
    first = {"prompt_tokens": 1, "prompt_tokens_details": {"cached_tokens": 1}}
    total = add_hop_usage(first, None)
    assert total == first
    total = add_hop_usage(
        total, {"prompt_tokens": 2, "prompt_tokens_details": {"cached_tokens": 1}}
    )
    assert first == {"prompt_tokens": 1, "prompt_tokens_details": {"cached_tokens": 1}}
    assert total == {"prompt_tokens": 3, "prompt_tokens_details": {"cached_tokens": 2}}
    assert add_hop_usage(None, None) is None


def test_add_hop_usage_keeps_earlier_values_when_a_hop_reports_none() -> None:
    total = add_hop_usage(
        None,
        {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 5}, "cost": 0.25},
    )
    total = add_hop_usage(total, {"prompt_tokens": 20, "prompt_tokens_details": None, "cost": 0.5})

    assert total == {
        "prompt_tokens": 30,
        "prompt_tokens_details": {"cached_tokens": 5},
        "cost": 0.75,
    }


def test_add_hop_usage_drops_a_bad_cache_count_before_the_sum() -> None:
    # 150 cached tokens cannot come from a 100-token prompt, even when a later hop adds
    # 100 more prompt tokens.
    total = add_hop_usage(
        None,
        {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 150, "cache_write_tokens": 20},
        },
    )
    total = add_hop_usage(total, {"prompt_tokens": 100})

    assert total == {
        "prompt_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 20},
    }


def test_add_hop_usage_keeps_valid_cache_details_unchanged() -> None:
    hop = {
        "prompt_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 40, "audio_tokens": 0},
    }

    assert add_hop_usage(None, hop) == hop


# A profiled chat adapter (for example DeepSeek) reports an explicit count. The OpenAI,
# Anthropic and Bedrock adapters report the count only in prompt_tokens_details.
_EXPLICIT_HOP = {
    "prompt_tokens": 100,
    "completion_tokens": 0,
    "total_tokens": 100,
    "prompt_tokens_cached": 60,
}
_DETAILS_HOP = {
    "prompt_tokens": 100,
    "completion_tokens": 0,
    "total_tokens": 100,
    "prompt_tokens_details": {"cached_tokens": 80},
}


@pytest.mark.parametrize("hops", [(_EXPLICIT_HOP, _DETAILS_HOP), (_DETAILS_HOP, _EXPLICIT_HOP)])
def test_add_hop_usage_adds_explicit_and_details_cache_counts(hops) -> None:
    total = None
    for hop in hops:
        total = add_hop_usage(total, hop)

    assert total["prompt_tokens"] == 200
    assert total["prompt_tokens_cached"] == 140
    assert with_reported_prompt_cache(total)["prompt_tokens_cached"] == 140
    assert _EXPLICIT_HOP["prompt_tokens_cached"] == 60
    assert "prompt_tokens_cached" not in _DETAILS_HOP


def test_add_hop_usage_adds_details_after_an_explicit_zero() -> None:
    total = add_hop_usage(None, {**_EXPLICIT_HOP, "prompt_tokens_cached": 0})
    total = add_hop_usage(total, _DETAILS_HOP)

    assert total["prompt_tokens_cached"] == 80


def test_add_hop_usage_adds_explicit_and_details_cache_write_counts() -> None:
    total = add_hop_usage(None, {"prompt_tokens": 100, "prompt_tokens_cache_write": 30})
    total = add_hop_usage(
        total, {"prompt_tokens": 100, "prompt_tokens_details": {"cache_write_tokens": 20}}
    )

    assert total["prompt_tokens_cache_write"] == 50


def test_add_hop_usage_keeps_details_only_when_no_hop_has_an_explicit_count() -> None:
    total = add_hop_usage(None, _DETAILS_HOP)
    total = add_hop_usage(total, _DETAILS_HOP)

    assert "prompt_tokens_cached" not in total
    assert total["prompt_tokens_details"] == {"cached_tokens": 160}


_VALID_HOP = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 80}}


@pytest.mark.parametrize("bad", [0.0, False, 1.5, -1, "80"])
@pytest.mark.parametrize("bad_first", [False, True])
def test_add_hop_usage_replaces_a_non_int_cache_count_with_int_zero(bad, bad_first) -> None:
    bad_hop = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": bad}}
    hops = (bad_hop, _VALID_HOP) if bad_first else (_VALID_HOP, bad_hop)
    total = None
    for hop in hops:
        total = add_hop_usage(total, hop)

    cached = total["prompt_tokens_details"]["cached_tokens"]
    assert type(cached) is int and cached == 80
    assert with_reported_prompt_cache(total)["prompt_tokens_cached"] == 80
