from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.services.tiers import (
    float_gte_one_or_none,
    ensure_single_active_primary_assignment,
    non_negative_float,
    normalize_access_mode,
    normalize_assignment_type,
    normalize_callable_key,
    normalize_capacity_strategy,
    normalize_metadata,
    normalize_pool_key,
    normalize_pricing,
    normalize_status,
    normalize_tier_key,
    positive_float_or_none,
    positive_int_or_none,
    positive_weight,
    ratio_gt_zero_lte_one_or_none,
    validate_effective_window,
)


def test_normalize_tier_key_trims_and_lowercases() -> None:
    assert normalize_tier_key(" Pro_Annual ") == "pro_annual"
    assert normalize_pool_key(" Burstable-1 ") == "burstable-1"


@pytest.mark.parametrize("value", ["", "bad key", "-bad", "bad.key", "bad/key"])
def test_normalize_tier_key_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="tier_key"):
        normalize_tier_key(value)


def test_normalize_callable_key_preserves_provider_model_shape() -> None:
    assert normalize_callable_key(" openai/gpt-4.1 ") == "openai/gpt-4.1"
    assert normalize_callable_key("Provider/Model:Variant") == "Provider/Model:Variant"


def test_enum_normalizers_accept_known_values_and_defaults() -> None:
    assert normalize_status(None) == "draft"
    assert normalize_status("ACTIVE") == "active"
    assert normalize_assignment_type(None) == "primary"
    assert normalize_assignment_type("Addon") == "addon"
    assert normalize_access_mode(None) == "allow"
    assert normalize_access_mode("DENY") == "deny"
    assert normalize_capacity_strategy(None) == "hard_cap"
    assert normalize_capacity_strategy("Weighted_Fair") == "weighted_fair"


@pytest.mark.parametrize(
    ("normalizer", "value"),
    [
        (normalize_status, "published"),
        (normalize_assignment_type, "default"),
        (normalize_access_mode, "inherit"),
        (normalize_capacity_strategy, "soft"),
    ],
)
def test_enum_normalizers_reject_unknown_values(normalizer, value: str) -> None:
    with pytest.raises(ValueError, match="must be one of"):
        normalizer(value)


def test_positive_int_or_none_accepts_positive_ints_and_empty_values() -> None:
    assert positive_int_or_none(None, "rpm_limit") is None
    assert positive_int_or_none("", "rpm_limit") is None
    assert positive_int_or_none("001", "rpm_limit") == 1
    assert positive_int_or_none("42", "rpm_limit") == 42
    assert positive_int_or_none(9, "rpm_limit") == 9


@pytest.mark.parametrize("value", [0, -1, "nope", True, 1.0, 1.9, "1.0", "+1"])
def test_positive_int_or_none_rejects_non_positive_or_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError, match="rpm_limit"):
        positive_int_or_none(value, "rpm_limit")


def test_positive_weight_accepts_positive_values_and_default() -> None:
    assert positive_weight(None) == 1
    assert positive_weight("3") == 3


@pytest.mark.parametrize("value", [0, -1, "", False, 1.0, "1.0"])
def test_positive_weight_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="weight"):
        positive_weight(value)


def test_non_negative_float_accepts_zero_and_positive_numbers() -> None:
    assert non_negative_float(0, "pricing.input_cost_per_token") == 0.0
    assert non_negative_float("0.002", "pricing.input_cost_per_token") == 0.002


@pytest.mark.parametrize(
    "value",
    [-0.1, "bad", False, "nan", float("nan"), "inf", float("inf"), "-inf"],
)
def test_non_negative_float_rejects_negative_or_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError, match="pricing.input_cost_per_token"):
        non_negative_float(value, "pricing.input_cost_per_token")


def test_positive_float_or_none_accepts_positive_values_and_empty_values() -> None:
    assert positive_float_or_none(None, "saturation_threshold") is None
    assert positive_float_or_none("", "saturation_threshold") is None
    assert positive_float_or_none("0.1", "saturation_threshold") == 0.1


@pytest.mark.parametrize(
    "value",
    [0, -0.1, "bad", True, "nan", float("nan"), "inf", float("inf")],
)
def test_positive_float_or_none_rejects_non_positive_or_non_numeric_values(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="saturation_threshold"):
        positive_float_or_none(value, "saturation_threshold")


def test_ratio_gt_zero_lte_one_or_none_matches_capacity_threshold_constraint() -> None:
    assert ratio_gt_zero_lte_one_or_none(None, "saturation_threshold") is None
    assert ratio_gt_zero_lte_one_or_none("0.8", "saturation_threshold") == 0.8
    assert ratio_gt_zero_lte_one_or_none(1, "saturation_threshold") == 1.0

    with pytest.raises(ValueError, match="saturation_threshold"):
        ratio_gt_zero_lte_one_or_none(0, "saturation_threshold")
    with pytest.raises(ValueError, match="saturation_threshold"):
        ratio_gt_zero_lte_one_or_none(1.1, "saturation_threshold")
    with pytest.raises(ValueError, match="saturation_threshold"):
        ratio_gt_zero_lte_one_or_none(float("nan"), "saturation_threshold")


def test_float_gte_one_or_none_matches_burst_multiplier_constraint() -> None:
    assert float_gte_one_or_none(None, "burst_multiplier") is None
    assert float_gte_one_or_none("", "burst_multiplier") is None
    assert float_gte_one_or_none(1, "burst_multiplier") == 1.0
    assert float_gte_one_or_none("1.5", "burst_multiplier") == 1.5

    with pytest.raises(ValueError, match="burst_multiplier"):
        float_gte_one_or_none(0.99, "burst_multiplier")
    with pytest.raises(ValueError, match="burst_multiplier"):
        float_gte_one_or_none(False, "burst_multiplier")
    with pytest.raises(ValueError, match="burst_multiplier"):
        float_gte_one_or_none("inf", "burst_multiplier")


def test_normalize_pricing_accepts_known_non_negative_fields() -> None:
    assert normalize_pricing(
        {
            "input_cost_per_token": "0.001",
            "output_cost_per_token": 0.002,
            "cost_per_request": 0,
        }
    ) == {
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.002,
        "cost_per_request": 0.0,
    }


def test_normalize_pricing_rejects_unknown_or_negative_fields() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalize_pricing({"unknown": 1})
    with pytest.raises(ValueError, match="pricing.input_cost_per_token"):
        normalize_pricing({"input_cost_per_token": -1})


def test_normalize_metadata_requires_object() -> None:
    metadata = {"owner": "growth"}

    assert normalize_metadata(metadata) == metadata
    assert normalize_metadata(None) is None
    with pytest.raises(ValueError, match="metadata"):
        normalize_metadata(["bad"])


def test_validate_effective_window_rejects_inverted_windows() -> None:
    starts_at = datetime(2026, 1, 1, tzinfo=UTC)
    ends_at = datetime(2026, 2, 1, tzinfo=UTC)

    assert validate_effective_window(starts_at, ends_at) == (starts_at, ends_at)
    with pytest.raises(ValueError, match="starts_at"):
        validate_effective_window(ends_at, starts_at)
    with pytest.raises(ValueError, match="starts_at"):
        validate_effective_window(starts_at, starts_at)


def test_ensure_single_active_primary_assignment_rejects_multiple_active_primaries() -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    assignments = [
        SimpleNamespace(assignment_type="primary", enabled=True, starts_at=None, ends_at=None),
        SimpleNamespace(
            assignment_type="primary",
            enabled=True,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        ),
    ]

    with pytest.raises(ValueError, match="one active primary"):
        ensure_single_active_primary_assignment(assignments, reference_time=now)


def test_ensure_single_active_primary_assignment_ignores_inactive_and_addon_rows() -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    assignments = [
        SimpleNamespace(assignment_type="primary", enabled=True, starts_at=None, ends_at=None),
        SimpleNamespace(assignment_type="addon", enabled=True, starts_at=None, ends_at=None),
        SimpleNamespace(assignment_type="primary", enabled=False, starts_at=None, ends_at=None),
        SimpleNamespace(
            assignment_type="primary",
            enabled=True,
            starts_at=now + timedelta(days=1),
            ends_at=None,
        ),
    ]

    ensure_single_active_primary_assignment(assignments, reference_time=now)
