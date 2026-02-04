"""Model type detection utility for auto-classifying model types."""

from typing import Optional

from ..types.common import ModelType


# Patterns for detecting model types based on naming conventions
MODEL_TYPE_PATTERNS: dict[str, list[str]] = {
    ModelType.EMBEDDING.value: [
        "embed",
        "text-embedding",
        "bge-",
        "e5-",
        "ada-002",
        "embedding",
    ],
    ModelType.IMAGE_GENERATION.value: [
        "dall-e",
        "dalle",
        "stable-diffusion",
        "imagen",
        "midjourney",
        "sdxl",
    ],
    ModelType.AUDIO_TRANSCRIPTION.value: [
        "whisper",
        "speech-to-text",
        "stt",
        "transcrib",
    ],
    ModelType.AUDIO_SPEECH.value: [
        "tts",
        "text-to-speech",
        "speech-",
        "elevenlabs",
    ],
    ModelType.RERANK.value: [
        "rerank",
        "cross-encoder",
        "ranker",
    ],
    ModelType.MODERATION.value: [
        "moderation",
        "content-filter",
        "safety",
    ],
}


def detect_model_type(
    model_name: str, provider_model: Optional[str] = None
) -> str:
    """
    Auto-detect model type based on naming patterns.

    Args:
        model_name: The public model name (e.g., 'my-embed-model')
        provider_model: The provider's model ID (e.g., 'text-embedding-3-large')

    Returns:
        The detected model type value (defaults to 'chat' if no match)
    """
    # Check both model_name and provider_model (lowercase for matching)
    names_to_check = [model_name.lower()]
    if provider_model:
        names_to_check.append(provider_model.lower())

    for model_type, patterns in MODEL_TYPE_PATTERNS.items():
        for name in names_to_check:
            for pattern in patterns:
                if pattern in name:
                    return model_type

    # Default to chat
    return ModelType.CHAT.value


def suggest_model_type(
    model_name: str, provider_model: Optional[str] = None
) -> dict:
    """
    Returns suggested type with confidence score.

    Args:
        model_name: The public model name
        provider_model: The provider's model ID

    Returns:
        Dictionary with:
            - suggested_type: The detected type
            - confidence: Confidence score (0.0 - 1.0)
            - matched_pattern: The pattern that matched (if any)
            - alternatives: List of possible alternative types
    """
    names_to_check = [model_name.lower()]
    if provider_model:
        names_to_check.append(provider_model.lower())

    matches: list[tuple[str, str, int]] = []  # (type, pattern, match_count)

    for model_type, patterns in MODEL_TYPE_PATTERNS.items():
        for name in names_to_check:
            for pattern in patterns:
                if pattern in name:
                    # Count how many patterns match for this type
                    match_count = sum(
                        1 for p in patterns for n in names_to_check if p in n
                    )
                    matches.append((model_type, pattern, match_count))

    if not matches:
        return {
            "suggested_type": ModelType.CHAT.value,
            "confidence": 0.5,
            "matched_pattern": None,
            "alternatives": [],
        }

    # Sort by match count (more matches = higher confidence)
    matches.sort(key=lambda x: x[2], reverse=True)
    best_match = matches[0]

    # Calculate confidence based on match count and pattern specificity
    confidence = min(0.7 + (best_match[2] * 0.1), 1.0)

    # Get alternative types (other types that also matched)
    seen_types = set()
    alternatives = []
    for match in matches[1:]:
        if match[0] not in seen_types:
            seen_types.add(match[0])
            alternatives.append(match[0])

    return {
        "suggested_type": best_match[0],
        "confidence": confidence,
        "matched_pattern": best_match[1],
        "alternatives": alternatives[:3],  # Max 3 alternatives
    }


def get_all_model_types() -> list[dict]:
    """
    Returns all available model types with descriptions.

    Returns:
        List of dictionaries with value and description for each type
    """
    type_descriptions = {
        ModelType.CHAT.value: "Conversational AI models (GPT, Claude, etc.)",
        ModelType.EMBEDDING.value: "Text embedding models for similarity search",
        ModelType.IMAGE_GENERATION.value: "Image generation models (DALL-E, etc.)",
        ModelType.AUDIO_TRANSCRIPTION.value: "Speech-to-text models (Whisper, etc.)",
        ModelType.AUDIO_SPEECH.value: "Text-to-speech models",
        ModelType.RERANK.value: "Reranking models for search results",
        ModelType.MODERATION.value: "Content moderation models",
    }

    return [
        {"value": t.value, "description": type_descriptions.get(t.value, "")}
        for t in ModelType
    ]
