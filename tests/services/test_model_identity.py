from __future__ import annotations

import pytest

from src.services.model_identity import (
    creator_api_model_id,
    creator_prompt_template_key,
    creator_route_group_key,
    generate_compact_asset_code,
    normalize_creator_namespace,
    normalize_model_slug,
    normalize_route_group_slug,
    suggested_creator_namespace,
    suggested_model_slug,
    suggested_route_group_slug,
)


def test_creator_namespace_is_readable_stable_and_unique_per_account() -> None:
    first = suggested_creator_namespace("alex.smith@example.com", "account-1")
    again = suggested_creator_namespace("alex.smith@example.com", "account-1")
    second = suggested_creator_namespace("alex.smith@example.com", "account-2")

    assert first.startswith("ale-smi-")
    assert first == again
    assert first != second
    assert len(first.rsplit("-", 1)[1]) == 5


def test_single_label_and_missing_label_namespace_fallbacks() -> None:
    assert suggested_creator_namespace("creator@example.com", "account-1").startswith(
        "cre-tor-"
    )
    assert suggested_creator_namespace("@example.com", "account-1").startswith("usr-")


def test_model_slug_preserves_a_readable_id_without_auto_suffixing() -> None:
    assert suggested_model_slug("Customer Support — Arabic") == "customer-support-arabic"
    assert creator_api_model_id("ale-smi-k7m4q", "customer-support") == (
        "ale-smi-k7m4q/customer-support"
    )


def test_route_group_slug_is_derived_from_the_friendly_name() -> None:
    assert suggested_route_group_slug("Customer Support — Arabic") == (
        "customer-support-arabic"
    )


def test_route_group_key_uses_compact_generated_code() -> None:
    assert creator_route_group_key("k7m4", "support-primary") == "grp-k7m4-support-primary"


def test_generated_route_group_code_is_four_url_safe_characters() -> None:
    codes = {generate_compact_asset_code() for _ in range(20)}

    assert len(codes) > 1
    assert all(len(code) == 4 for code in codes)
    assert all(set(code) <= set("0123456789abcdefghjkmnpqrstvwxyz") for code in codes)


def test_prompt_template_key_uses_the_same_compact_code_pattern() -> None:
    assert creator_prompt_template_key("q2x9", "support.reply") == "prm-q2x9-support.reply"


@pytest.mark.parametrize(
    ("normalizer", "value"),
    (
        (normalize_creator_namespace, "a"),
        (normalize_creator_namespace, "ab"),
        (normalize_creator_namespace, "alex--smith"),
        (normalize_creator_namespace, "alex_smith"),
        (normalize_creator_namespace, "a" * 33),
        (normalize_model_slug, "-model"),
        (normalize_model_slug, "my--model"),
        (normalize_model_slug, "model/name"),
        (normalize_route_group_slug, "-group"),
        (normalize_route_group_slug, "my--group"),
        (normalize_route_group_slug, "group/name"),
        (lambda value: creator_route_group_key(value, "group"), "abcd1"),
    ),
)
def test_identity_parts_reject_ambiguous_or_unsafe_values(normalizer, value: str) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        normalizer(value)


@pytest.mark.parametrize("value", ("abc", "a" * 32))
def test_creator_namespace_accepts_length_boundaries(value: str) -> None:
    assert normalize_creator_namespace(value) == value
