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
