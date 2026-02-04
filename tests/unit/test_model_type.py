"""Tests for ModelType enum and model type detector utility."""

import pytest

from deltallm.types.common import ModelType
from deltallm.utils.model_type_detector import (
    detect_model_type,
    suggest_model_type,
    get_all_model_types,
    MODEL_TYPE_PATTERNS,
)


class TestModelType:
    """Tests for ModelType enum."""

    def test_model_type_values(self):
        """Test that all expected model types exist."""
        assert ModelType.CHAT.value == "chat"
        assert ModelType.EMBEDDING.value == "embedding"
        assert ModelType.IMAGE_GENERATION.value == "image_generation"
        assert ModelType.AUDIO_TRANSCRIPTION.value == "audio_transcription"
        assert ModelType.AUDIO_SPEECH.value == "audio_speech"
        assert ModelType.RERANK.value == "rerank"
        assert ModelType.MODERATION.value == "moderation"

    def test_model_type_is_string_enum(self):
        """Test that ModelType inherits from str."""
        assert isinstance(ModelType.CHAT, str)
        assert ModelType.CHAT == "chat"

    def test_get_endpoint_type_mapping(self):
        """Test get_endpoint_type returns correct mappings."""
        assert ModelType.get_endpoint_type(ModelType.CHAT) == "chat"
        assert ModelType.get_endpoint_type(ModelType.EMBEDDING) == "embedding"
        assert ModelType.get_endpoint_type(ModelType.IMAGE_GENERATION) == "image"
        assert ModelType.get_endpoint_type(ModelType.AUDIO_TRANSCRIPTION) == "audio_transcription"
        assert ModelType.get_endpoint_type(ModelType.AUDIO_SPEECH) == "audio_speech"
        assert ModelType.get_endpoint_type(ModelType.RERANK) == "rerank"
        assert ModelType.get_endpoint_type(ModelType.MODERATION) == "moderation"

    def test_values_method(self):
        """Test that values() returns all enum values."""
        values = ModelType.values()
        assert isinstance(values, list)
        assert "chat" in values
        assert "embedding" in values
        assert "image_generation" in values
        assert "audio_transcription" in values
        assert "audio_speech" in values
        assert "rerank" in values
        assert "moderation" in values
        assert len(values) == 7


class TestDetectModelType:
    """Tests for detect_model_type function."""

    def test_detect_embedding_model(self):
        """Test detection of embedding models."""
        assert detect_model_type("text-embedding-3-small") == "embedding"
        assert detect_model_type("my-embed-model") == "embedding"
        assert detect_model_type("bge-large-en") == "embedding"
        assert detect_model_type("ada-002") == "embedding"

    def test_detect_embedding_from_provider_model(self):
        """Test detection from provider_model parameter."""
        assert detect_model_type("my-model", "text-embedding-3-large") == "embedding"

    def test_detect_image_generation_model(self):
        """Test detection of image generation models."""
        assert detect_model_type("dall-e-3") == "image_generation"
        assert detect_model_type("dalle-2") == "image_generation"
        assert detect_model_type("stable-diffusion-xl") == "image_generation"
        assert detect_model_type("imagen-3") == "image_generation"

    def test_detect_audio_transcription_model(self):
        """Test detection of audio transcription models."""
        assert detect_model_type("whisper-large") == "audio_transcription"
        assert detect_model_type("speech-to-text-v1") == "audio_transcription"

    def test_detect_audio_speech_model(self):
        """Test detection of text-to-speech models."""
        assert detect_model_type("tts-1") == "audio_speech"
        assert detect_model_type("tts-1-hd") == "audio_speech"
        assert detect_model_type("text-to-speech-v1") == "audio_speech"

    def test_detect_rerank_model(self):
        """Test detection of rerank models."""
        assert detect_model_type("rerank-english-v2") == "rerank"
        assert detect_model_type("cross-encoder-ms-marco") == "rerank"

    def test_detect_moderation_model(self):
        """Test detection of moderation models."""
        assert detect_model_type("text-moderation-latest") == "moderation"
        assert detect_model_type("content-filter-v1") == "moderation"

    def test_default_to_chat(self):
        """Test that unrecognized models default to chat."""
        assert detect_model_type("gpt-4o") == "chat"
        assert detect_model_type("claude-3-opus") == "chat"
        assert detect_model_type("llama-3-70b") == "chat"
        assert detect_model_type("unknown-model") == "chat"

    def test_case_insensitive(self):
        """Test that detection is case insensitive."""
        assert detect_model_type("TEXT-EMBEDDING-3-LARGE") == "embedding"
        assert detect_model_type("DALL-E-3") == "image_generation"
        assert detect_model_type("WhIsPeR-LarGe") == "audio_transcription"


class TestSuggestModelType:
    """Tests for suggest_model_type function."""

    def test_suggest_returns_dict(self):
        """Test that suggest_model_type returns a dict with expected keys."""
        result = suggest_model_type("gpt-4o")
        assert isinstance(result, dict)
        assert "suggested_type" in result
        assert "confidence" in result
        assert "matched_pattern" in result
        assert "alternatives" in result

    def test_suggest_embedding_with_confidence(self):
        """Test suggestion for embedding model with confidence."""
        result = suggest_model_type("text-embedding-3-large")
        assert result["suggested_type"] == "embedding"
        assert result["confidence"] >= 0.7
        assert result["matched_pattern"] is not None

    def test_suggest_chat_for_unknown(self):
        """Test that unknown models suggest chat with lower confidence."""
        result = suggest_model_type("unknown-model-xyz")
        assert result["suggested_type"] == "chat"
        assert result["confidence"] == 0.5
        assert result["matched_pattern"] is None

    def test_suggest_alternatives(self):
        """Test that alternatives are provided when multiple patterns match."""
        # This should match 'embed' pattern
        result = suggest_model_type("embed-rerank-model")
        assert isinstance(result["alternatives"], list)


class TestGetAllModelTypes:
    """Tests for get_all_model_types function."""

    def test_returns_list(self):
        """Test that get_all_model_types returns a list."""
        result = get_all_model_types()
        assert isinstance(result, list)
        assert len(result) == 7  # 7 model types

    def test_returns_dicts_with_value_and_description(self):
        """Test that each item has value and description."""
        result = get_all_model_types()
        for item in result:
            assert "value" in item
            assert "description" in item
            assert isinstance(item["value"], str)
            assert isinstance(item["description"], str)

    def test_contains_all_types(self):
        """Test that all model types are included."""
        result = get_all_model_types()
        values = [item["value"] for item in result]
        assert "chat" in values
        assert "embedding" in values
        assert "image_generation" in values
        assert "audio_transcription" in values
        assert "audio_speech" in values
        assert "rerank" in values
        assert "moderation" in values


class TestModelTypePatterns:
    """Tests for MODEL_TYPE_PATTERNS constant."""

    def test_patterns_exist_for_each_type(self):
        """Test that patterns exist for non-chat types."""
        assert "embedding" in MODEL_TYPE_PATTERNS
        assert "image_generation" in MODEL_TYPE_PATTERNS
        assert "audio_transcription" in MODEL_TYPE_PATTERNS
        assert "audio_speech" in MODEL_TYPE_PATTERNS
        assert "rerank" in MODEL_TYPE_PATTERNS
        assert "moderation" in MODEL_TYPE_PATTERNS

    def test_patterns_are_lists(self):
        """Test that each pattern value is a list."""
        for model_type, patterns in MODEL_TYPE_PATTERNS.items():
            assert isinstance(patterns, list)
            assert len(patterns) > 0
