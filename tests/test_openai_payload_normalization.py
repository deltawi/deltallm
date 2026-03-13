from __future__ import annotations

from src.providers.resolution import normalize_openai_image_generation_payload


def test_gpt_image_payload_omits_style_for_openai_family() -> None:
    payload = {
        "model": "gpt-image-1-mini",
        "prompt": "draw a cat",
        "quality": "standard",
        "style": "vivid",
        "response_format": "b64_json",
    }

    normalize_openai_image_generation_payload(
        payload,
        provider="openai",
        upstream_model="gpt-image-1-mini",
    )

    assert "style" not in payload
    assert "response_format" not in payload
    assert payload["quality"] == "medium"


def test_gpt_image_payload_maps_hd_quality_to_high() -> None:
    payload = {
        "model": "gpt-image-1-mini",
        "prompt": "draw a cat",
        "quality": "hd",
    }

    normalize_openai_image_generation_payload(
        payload,
        provider="openai",
        upstream_model="gpt-image-1-mini",
    )

    assert payload["quality"] == "high"


def test_dalle_2_payload_omits_quality_and_style_for_openai_family() -> None:
    payload = {
        "model": "dall-e-2",
        "prompt": "draw a cat",
        "quality": "standard",
        "style": "vivid",
    }

    normalize_openai_image_generation_payload(
        payload,
        provider="openai",
        upstream_model="dall-e-2",
    )

    assert "quality" not in payload
    assert "style" not in payload


def test_dalle_3_payload_preserves_quality_and_style() -> None:
    payload = {
        "model": "dall-e-3",
        "prompt": "draw a cat",
        "quality": "hd",
        "style": "natural",
    }

    normalize_openai_image_generation_payload(
        payload,
        provider="openai",
        upstream_model="dall-e-3",
    )

    assert payload["quality"] == "hd"
    assert payload["style"] == "natural"


def test_non_openai_family_provider_is_unchanged() -> None:
    payload = {
        "model": "gpt-image-1-mini",
        "prompt": "draw a cat",
        "style": "vivid",
    }

    normalize_openai_image_generation_payload(
        payload,
        provider="openrouter",
        upstream_model="gpt-image-1-mini",
    )

    assert payload["style"] == "vivid"
