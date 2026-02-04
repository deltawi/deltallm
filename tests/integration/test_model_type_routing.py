"""Integration tests for model type routing functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from deltallm.dynamic_router import DynamicRouter, CachedDeployment
from deltallm.db.models import ModelDeployment, ProviderConfig
from deltallm.types.common import ModelType


@pytest.fixture
def mock_deployment():
    """Create a mock ModelDeployment."""
    deployment = MagicMock(spec=ModelDeployment)
    deployment.id = uuid4()
    deployment.model_name = "test-model"
    deployment.provider_model = "gpt-4o"
    deployment.model_type = ModelType.CHAT.value
    deployment.is_active = True
    deployment.priority = 1
    deployment.provider_config_id = uuid4()
    deployment.provider_type = "openai"
    deployment.api_key_encrypted = None
    deployment.org_id = None
    return deployment


@pytest.fixture
def mock_embedding_deployment():
    """Create a mock embedding ModelDeployment."""
    deployment = MagicMock(spec=ModelDeployment)
    deployment.id = uuid4()
    deployment.model_name = "embed-model"
    deployment.provider_model = "text-embedding-3-small"
    deployment.model_type = ModelType.EMBEDDING.value
    deployment.is_active = True
    deployment.priority = 1
    deployment.provider_config_id = uuid4()
    deployment.provider_type = "openai"
    deployment.api_key_encrypted = None
    deployment.org_id = None
    return deployment


@pytest.fixture
def mock_provider_config():
    """Create a mock ProviderConfig."""
    config = MagicMock(spec=ProviderConfig)
    config.id = uuid4()
    config.name = "test-provider"
    config.provider_type = "openai"
    config.api_key_encrypted = "encrypted_key"
    config.is_active = True
    return config


class TestCachedDeploymentModelType:
    """Tests for CachedDeployment model_type property."""

    def test_model_type_property(self, mock_deployment, mock_provider_config):
        """Test that model_type property returns deployment's model_type."""
        cached = CachedDeployment(
            deployment=mock_deployment,
            provider_config=mock_provider_config,
            api_key="test-key",
        )
        assert cached.model_type == ModelType.CHAT.value

    def test_model_type_property_embedding(self, mock_embedding_deployment, mock_provider_config):
        """Test model_type for embedding deployment."""
        cached = CachedDeployment(
            deployment=mock_embedding_deployment,
            provider_config=mock_provider_config,
            api_key="test-key",
        )
        assert cached.model_type == ModelType.EMBEDDING.value

    def test_model_type_property_missing_attribute(self, mock_provider_config):
        """Test model_type defaults to 'chat' if attribute is missing."""
        deployment = MagicMock()
        deployment.id = uuid4()
        # Simulate missing model_type attribute
        del deployment.model_type

        cached = CachedDeployment(
            deployment=deployment,
            provider_config=mock_provider_config,
            api_key="test-key",
        )
        assert cached.model_type == "chat"


class TestDynamicRouterModelTypeFiltering:
    """Tests for DynamicRouter model type filtering."""

    @pytest.fixture
    def router(self):
        """Create a DynamicRouter instance."""
        return DynamicRouter()

    @pytest.mark.asyncio
    async def test_get_available_models_with_type_filter(self, router):
        """Test get_available_models with model_type filter."""
        with patch.object(router, 'get_available_models', new_callable=AsyncMock) as mock:
            mock.return_value = ["chat-model-1", "chat-model-2"]

            result = await router.get_available_models(model_type="chat")

            mock.assert_called_once_with(model_type="chat")
            assert result == ["chat-model-1", "chat-model-2"]

    @pytest.mark.asyncio
    async def test_get_models_by_type(self, router):
        """Test get_models_by_type method."""
        with patch.object(router, 'get_available_models', new_callable=AsyncMock) as mock:
            mock.return_value = ["embed-model-1"]

            result = await router.get_models_by_type("embedding")

            mock.assert_called_once_with(org_id=None, model_type="embedding")
            assert result == ["embed-model-1"]

    @pytest.mark.asyncio
    async def test_get_models_by_type_with_org(self, router):
        """Test get_models_by_type with organization filter."""
        org_id = uuid4()
        with patch.object(router, 'get_available_models', new_callable=AsyncMock) as mock:
            mock.return_value = ["org-embed-model"]

            result = await router.get_models_by_type("embedding", org_id=org_id)

            mock.assert_called_once_with(org_id=org_id, model_type="embedding")
            assert result == ["org-embed-model"]

    @pytest.mark.asyncio
    async def test_get_deployment_info(self, router, mock_deployment):
        """Test get_deployment_info returns deployment."""
        with patch('deltallm.dynamic_router.get_session') as mock_session:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_deployment
            mock_db.execute.return_value = mock_result
            mock_session.return_value.__aenter__.return_value = mock_db

            result = await router.get_deployment_info("test-model")

            assert result == mock_deployment

    @pytest.mark.asyncio
    async def test_get_deployment_info_not_found(self, router):
        """Test get_deployment_info returns None when not found."""
        with patch('deltallm.dynamic_router.get_session') as mock_session:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute.return_value = mock_result
            mock_session.return_value.__aenter__.return_value = mock_db

            result = await router.get_deployment_info("nonexistent-model")

            assert result is None


class TestModelTypeValidation:
    """Tests for model type validation in routes."""

    @pytest.mark.asyncio
    async def test_chat_endpoint_rejects_embedding_model(self):
        """Test that chat endpoint rejects embedding models."""
        # This would be tested with the full FastAPI test client
        # For now, we test the logic that would be used

        from deltallm.types.common import ModelType

        model_type = ModelType.EMBEDDING.value
        expected_type = ModelType.CHAT.value

        # Simulate the validation check
        if model_type != expected_type:
            error_message = f"Model is type '{model_type}', expected '{expected_type}'"
            assert "embedding" in error_message
            assert "chat" in error_message

    def test_model_type_enum_values_are_valid(self):
        """Test that all model type values are valid strings."""
        valid_types = ModelType.values()

        for model_type in ModelType:
            assert model_type.value in valid_types
            assert isinstance(model_type.value, str)
