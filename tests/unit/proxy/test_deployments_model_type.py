"""Tests for model type functionality in deployments API."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from pydantic import ValidationError

from deltallm.proxy.schemas import (
    ModelDeploymentCreate,
    ModelDeploymentUpdate,
    ModelDeploymentResponse,
)
from deltallm.types.common import ModelType
from deltallm.utils.model_type_detector import suggest_model_type, get_all_model_types


class TestModelDeploymentCreateSchema:
    """Tests for ModelDeploymentCreate schema with model_type."""

    def test_default_model_type_is_chat(self):
        """Test that model_type defaults to 'chat'."""
        data = ModelDeploymentCreate(
            model_name="test-model",
            provider_model="gpt-4o",
            provider_config_id=uuid4(),
        )
        assert data.model_type == ModelType.CHAT.value

    def test_create_with_embedding_type(self):
        """Test creating deployment with embedding type."""
        data = ModelDeploymentCreate(
            model_name="embed-model",
            provider_model="text-embedding-3-small",
            provider_config_id=uuid4(),
            model_type="embedding",
        )
        assert data.model_type == "embedding"

    def test_create_with_image_generation_type(self):
        """Test creating deployment with image_generation type."""
        data = ModelDeploymentCreate(
            model_name="dalle-model",
            provider_model="dall-e-3",
            provider_config_id=uuid4(),
            model_type="image_generation",
        )
        assert data.model_type == "image_generation"

    def test_create_with_audio_transcription_type(self):
        """Test creating deployment with audio_transcription type."""
        data = ModelDeploymentCreate(
            model_name="whisper-model",
            provider_model="whisper-1",
            provider_config_id=uuid4(),
            model_type="audio_transcription",
        )
        assert data.model_type == "audio_transcription"

    def test_create_with_audio_speech_type(self):
        """Test creating deployment with audio_speech type."""
        data = ModelDeploymentCreate(
            model_name="tts-model",
            provider_model="tts-1",
            provider_config_id=uuid4(),
            model_type="audio_speech",
        )
        assert data.model_type == "audio_speech"

    def test_create_with_rerank_type(self):
        """Test creating deployment with rerank type."""
        data = ModelDeploymentCreate(
            model_name="rerank-model",
            provider_model="rerank-english-v2",
            provider_config_id=uuid4(),
            model_type="rerank",
        )
        assert data.model_type == "rerank"

    def test_create_with_moderation_type(self):
        """Test creating deployment with moderation type."""
        data = ModelDeploymentCreate(
            model_name="mod-model",
            provider_model="text-moderation-latest",
            provider_config_id=uuid4(),
            model_type="moderation",
        )
        assert data.model_type == "moderation"

    def test_invalid_model_type_rejected(self):
        """Test that invalid model_type is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ModelDeploymentCreate(
                model_name="test-model",
                provider_model="gpt-4o",
                provider_config_id=uuid4(),
                model_type="invalid_type",
            )
        assert "model_type must be one of" in str(exc_info.value)

    def test_standalone_deployment_with_model_type(self):
        """Test standalone deployment with model_type."""
        data = ModelDeploymentCreate(
            model_name="my-embed",
            provider_model="text-embedding-3-large",
            provider_type="openai",
            api_key="sk-test123",
            model_type="embedding",
        )
        assert data.model_type == "embedding"
        assert data.provider_config_id is None


class TestModelDeploymentUpdateSchema:
    """Tests for ModelDeploymentUpdate schema with model_type."""

    def test_update_model_type(self):
        """Test updating model_type field."""
        data = ModelDeploymentUpdate(model_type="embedding")
        assert data.model_type == "embedding"

    def test_model_type_optional_in_update(self):
        """Test that model_type is optional in update."""
        data = ModelDeploymentUpdate(model_name="new-name")
        assert data.model_type is None

    def test_update_all_model_types(self):
        """Test updating to all valid model types."""
        for model_type in ModelType.values():
            data = ModelDeploymentUpdate(model_type=model_type)
            assert data.model_type == model_type


class TestModelDeploymentResponseSchema:
    """Tests for ModelDeploymentResponse schema with model_type."""

    def test_response_includes_model_type(self):
        """Test that response includes model_type field."""
        from datetime import datetime

        response = ModelDeploymentResponse(
            id=uuid4(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            model_name="test-model",
            provider_model="gpt-4o",
            provider_config_id=uuid4(),
            provider_type="openai",
            model_type="chat",
            api_base=None,
            org_id=None,
            is_active=True,
            priority=1,
            tpm_limit=None,
            rpm_limit=None,
            timeout=None,
            settings={},
        )
        assert response.model_type == "chat"


class TestSuggestModelTypeEndpoint:
    """Tests for the suggest-type endpoint functionality."""

    def test_suggest_embedding_type(self):
        """Test suggestion for embedding model name."""
        result = suggest_model_type("text-embedding-3-large")
        assert result["suggested_type"] == "embedding"
        assert result["confidence"] > 0.5

    def test_suggest_with_provider_model(self):
        """Test suggestion using provider_model."""
        result = suggest_model_type("my-model", "text-embedding-ada-002")
        assert result["suggested_type"] == "embedding"

    def test_suggest_chat_for_gpt(self):
        """Test suggestion for GPT models defaults to chat."""
        result = suggest_model_type("gpt-4o")
        assert result["suggested_type"] == "chat"

    def test_suggest_image_generation(self):
        """Test suggestion for image generation models."""
        result = suggest_model_type("dall-e-3")
        assert result["suggested_type"] == "image_generation"

    def test_suggest_audio_transcription(self):
        """Test suggestion for audio transcription models."""
        result = suggest_model_type("whisper-large-v3")
        assert result["suggested_type"] == "audio_transcription"


class TestGetAllModelTypesEndpoint:
    """Tests for the model-types endpoint functionality."""

    def test_returns_all_types(self):
        """Test that all model types are returned."""
        result = get_all_model_types()
        values = [item["value"] for item in result]

        assert "chat" in values
        assert "embedding" in values
        assert "image_generation" in values
        assert "audio_transcription" in values
        assert "audio_speech" in values
        assert "rerank" in values
        assert "moderation" in values

    def test_each_type_has_description(self):
        """Test that each type has a description."""
        result = get_all_model_types()

        for item in result:
            assert "value" in item
            assert "description" in item
            assert len(item["description"]) > 0


class TestModelTypeFilterInList:
    """Tests for model_type filter in list deployments."""

    def test_valid_model_types_for_filtering(self):
        """Test that all model types are valid for filtering."""
        valid_types = ModelType.values()

        assert "chat" in valid_types
        assert "embedding" in valid_types
        assert "image_generation" in valid_types
        assert "audio_transcription" in valid_types
        assert "audio_speech" in valid_types
        assert "rerank" in valid_types
        assert "moderation" in valid_types

    def test_model_type_enum_iteration(self):
        """Test that ModelType enum can be iterated."""
        types = list(ModelType)
        assert len(types) == 7
        assert ModelType.CHAT in types
        assert ModelType.EMBEDDING in types
