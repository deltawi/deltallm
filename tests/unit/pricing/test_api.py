"""Tests for Pricing API routes."""

import json
import pytest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock, patch

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from deltallm.pricing import PricingManager, CostCalculator, PricingConfig
from deltallm.proxy.routes.pricing import router as pricing_router
from deltallm.proxy.schemas_pricing import (
    PricingCreateRequest,
    CostCalculationRequest,
)


# Mock authentication dependency
def mock_require_user():
    """Mock user dependency."""
    user = Mock()
    user.is_superuser = True
    user.id = uuid4()
    return user


@pytest.fixture
def app():
    """Create test app with pricing routes."""
    app = FastAPI()
    
    # Set up mock pricing manager and calculator
    pricing_manager = PricingManager(enable_hot_reload=False)
    cost_calculator = CostCalculator(pricing_manager)
    
    app.state.pricing_manager = pricing_manager
    app.state.cost_calculator = cost_calculator
    
    # Override the auth dependency
    app.dependency_overrides = {}
    
    app.include_router(pricing_router, prefix="")
    
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def authenticated_client(app):
    """Create authenticated test client."""
    from deltallm.proxy.dependencies import require_user
    
    # Mock the require_user dependency
    app.dependency_overrides[require_user] = mock_require_user
    
    return TestClient(app)


class TestPricingListEndpoint:
    """Tests for GET /v1/pricing/models."""
    
    def test_list_models_authenticated(self, authenticated_client):
        """Test listing models with authentication."""
        response = authenticated_client.get("/v1/pricing/models")
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert data["page"] == 1
        assert len(data["items"]) > 0
    
    def test_list_models_filter_by_mode(self, authenticated_client):
        """Test filtering models by mode."""
        response = authenticated_client.get("/v1/pricing/models?mode=chat")
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        for item in data["items"]:
            assert item["mode"] == "chat"
    
    def test_list_models_search(self, authenticated_client):
        """Test searching models by name."""
        response = authenticated_client.get("/v1/pricing/models?search=gpt-4o")
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        # Should find gpt-4o variants
        model_names = [item["model"] for item in data["items"]]
        assert any("gpt-4o" in name for name in model_names)
    
    def test_list_models_pagination(self, authenticated_client):
        """Test pagination of models."""
        # Get first page
        response1 = authenticated_client.get("/v1/pricing/models?page=1&page_size=5")
        assert response1.status_code == status.HTTP_200_OK
        data1 = response1.json()
        assert len(data1["items"]) <= 5
        
        # Get second page
        response2 = authenticated_client.get("/v1/pricing/models?page=2&page_size=5")
        assert response2.status_code == status.HTTP_200_OK
        data2 = response2.json()
        assert len(data2["items"]) <= 5


class TestPricingGetEndpoint:
    """Tests for GET /v1/pricing/models/{model}."""
    
    def test_get_model_pricing_success(self, authenticated_client):
        """Test getting pricing for a known model."""
        response = authenticated_client.get("/v1/pricing/models/gpt-4o")
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["model"] == "gpt-4o"
        assert data["mode"] == "chat"
        assert "input_cost_per_token" in data
        assert "output_cost_per_token" in data
        assert data["source"] in ["yaml", "db_global", "db_org", "db_team"]
    
    def test_get_model_pricing_unknown_model(self, authenticated_client):
        """Test getting pricing for unknown model returns default."""
        response = authenticated_client.get("/v1/pricing/models/unknown-model-xyz")
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["model"] == "unknown-model-xyz"
        # Should have zero pricing
        assert Decimal(data["input_cost_per_token"]) == Decimal("0")


class TestPricingSetEndpoint:
    """Tests for POST /v1/pricing/models/{model}."""
    
    def test_set_pricing_global(self, authenticated_client):
        """Test setting global pricing."""
        payload = {
            "mode": "chat",
            "input_cost_per_token": "0.000001",
            "output_cost_per_token": "0.000002",
            "max_tokens": 128000,
        }
        
        response = authenticated_client.post(
            "/v1/pricing/models/custom-model",
            json=payload,
        )
        assert response.status_code == status.HTTP_201_CREATED
        
        data = response.json()
        assert data["model"] == "custom-model"
        assert Decimal(data["input_cost_per_token"]) == Decimal("0.000001")
        assert data["source"] == "db_global"
    
    def test_set_pricing_with_image_sizes(self, authenticated_client):
        """Test setting pricing with image sizes."""
        payload = {
            "mode": "image_generation",
            "image_sizes": {
                "1024x1024": "0.050",
                "512x512": "0.025",
            },
            "quality_pricing": {
                "standard": 1.0,
                "hd": 2.5,
            },
        }
        
        response = authenticated_client.post(
            "/v1/pricing/models/custom-image-model",
            json=payload,
        )
        assert response.status_code == status.HTTP_201_CREATED
        
        data = response.json()
        assert data["mode"] == "image_generation"
        assert data["image_sizes"]["1024x1024"] == "0.050"
    
    def test_set_pricing_with_cache_pricing(self, authenticated_client):
        """Test setting pricing with prompt caching."""
        payload = {
            "mode": "chat",
            "input_cost_per_token": "0.000003",
            "output_cost_per_token": "0.000015",
            "cache_creation_input_token_cost": "0.00000375",
            "cache_read_input_token_cost": "0.0000003",
        }
        
        response = authenticated_client.post(
            "/v1/pricing/models/claude-custom",
            json=payload,
        )
        assert response.status_code == status.HTTP_201_CREATED
        
        data = response.json()
        # Decimal may be serialized as scientific notation
        cache_cost = Decimal(data["cache_read_input_token_cost"])
        assert cache_cost == Decimal("0.0000003")


class TestPricingDeleteEndpoint:
    """Tests for DELETE /v1/pricing/models/{model}."""
    
    def test_delete_pricing_success(self, authenticated_client):
        """Test deleting custom pricing."""
        # First set some custom pricing
        payload = {
            "mode": "chat",
            "input_cost_per_token": "0.000001",
        }
        authenticated_client.post(
            "/v1/pricing/models/delete-test-model",
            json=payload,
        )
        
        # Then delete it
        response = authenticated_client.delete("/v1/pricing/models/delete-test-model")
        assert response.status_code == status.HTTP_204_NO_CONTENT
    
    def test_delete_pricing_not_found(self, authenticated_client):
        """Test deleting non-existent pricing."""
        response = authenticated_client.delete("/v1/pricing/models/non-existent-model")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCostCalculationEndpoint:
    """Tests for POST /v1/pricing/test-calculate."""
    
    def test_calculate_chat_cost(self, authenticated_client):
        """Test calculating chat completion cost."""
        payload = {
            "model": "gpt-4o",
            "endpoint": "/v1/chat/completions",
            "params": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            },
        }
        
        response = authenticated_client.post(
            "/v1/pricing/test-calculate",
            json=payload,
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert "total_cost" in data
        assert Decimal(data["total_cost"]) > 0
        assert data["input_cost"] is not None
        assert data["output_cost"] is not None
    
    def test_calculate_image_cost(self, authenticated_client):
        """Test calculating image generation cost."""
        payload = {
            "model": "dall-e-3",
            "endpoint": "/v1/images/generations",
            "params": {
                "size": "1024x1024",
                "quality": "hd",
                "n": 2,
            },
        }
        
        response = authenticated_client.post(
            "/v1/pricing/test-calculate",
            json=payload,
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        # DALL-E 3 HD: 4 cents * 2.0 * 2 images = 16 cents
        assert Decimal(data["total_cost"]) == Decimal("0.160")
        assert data["image_cost"] is not None
    
    def test_calculate_with_cached_tokens(self, authenticated_client):
        """Test calculating cost with cached tokens."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "endpoint": "/v1/chat/completions",
            "params": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "cached_tokens": 800,
            },
        }
        
        response = authenticated_client.post(
            "/v1/pricing/test-calculate",
            json=payload,
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["cache_read_cost"] is not None
        assert Decimal(data["cache_read_cost"]) > 0
    
    def test_calculate_with_batch_discount(self, authenticated_client):
        """Test calculating cost with batch discount."""
        payload = {
            "model": "gpt-4o",
            "endpoint": "/v1/chat/completions",
            "params": {
                "prompt_tokens": 10000,
                "completion_tokens": 5000,
                "is_batch": True,
            },
        }
        
        response = authenticated_client.post(
            "/v1/pricing/test-calculate",
            json=payload,
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["batch_discount"] is not None
        assert Decimal(data["batch_discount"]) > 0
        assert data["discount_percent"] == 50.0
        assert data["original_cost"] is not None


class TestPricingExportEndpoint:
    """Tests for GET /v1/pricing/export."""
    
    def test_export_yaml(self, authenticated_client):
        """Test exporting pricing as YAML."""
        response = authenticated_client.get("/v1/pricing/export?format=yaml")
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "application/x-yaml"
        assert "pricing.yaml" in response.headers["content-disposition"]
        
        # Verify it's valid YAML
        import yaml
        data = yaml.safe_load(response.content)
        assert "version" in data
        assert "pricing" in data
    
    def test_export_json(self, authenticated_client):
        """Test exporting pricing as JSON."""
        response = authenticated_client.get("/v1/pricing/export?format=json")
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "application/json"
        assert "pricing.json" in response.headers["content-disposition"]
        
        # Verify it's valid JSON
        data = json.loads(response.content)
        assert "version" in data
        assert "pricing" in data


class TestPricingImportEndpoint:
    """Tests for POST /v1/pricing/import."""
    
    def test_import_yaml(self, authenticated_client):
        """Test importing pricing from YAML."""
        yaml_content = b"""
version: "1.0"
pricing:
  imported-model:
    mode: chat
    input_cost_per_token: 0.000001
    output_cost_per_token: 0.000002
"""
        
        response = authenticated_client.post(
            "/v1/pricing/import",
            files={"file": ("pricing.yaml", yaml_content, "application/x-yaml")},
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["success"] is True
        assert data["imported_count"] == 1
        assert len(data["errors"]) == 0
    
    def test_import_dry_run(self, authenticated_client):
        """Test importing with dry run."""
        yaml_content = b"""
version: "1.0"
pricing:
  dry-run-model:
    mode: embedding
    input_cost_per_token: 0.0000001
"""
        
        response = authenticated_client.post(
            "/v1/pricing/import?dry_run=true",
            files={"file": ("pricing.yaml", yaml_content, "application/x-yaml")},
        )
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert data["dry_run"] is True
        assert data["imported_count"] == 1
    
    def test_import_invalid_yaml(self, authenticated_client):
        """Test importing invalid YAML."""
        response = authenticated_client.post(
            "/v1/pricing/import",
            files={"file": ("pricing.yaml", b"invalid: [", "application/x-yaml")},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_import_missing_pricing_section(self, authenticated_client):
        """Test importing YAML without pricing section."""
        yaml_content = b"""
version: "1.0"
models:
  test-model:
    mode: chat
"""
        
        response = authenticated_client.post(
            "/v1/pricing/import",
            files={"file": ("pricing.yaml", yaml_content, "application/x-yaml")},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestPricingReloadEndpoint:
    """Tests for POST /v1/pricing/reload."""
    
    def test_reload_pricing(self, authenticated_client):
        """Test reloading pricing configuration."""
        response = authenticated_client.post("/v1/pricing/reload")
        assert response.status_code == status.HTTP_204_NO_CONTENT


class TestPricingPermissions:
    """Tests for pricing endpoint permissions."""
    
    def test_non_superuser_cannot_set_global_pricing(self, app):
        """Test that non-superusers cannot set global pricing."""
        # Create non-superuser mock
        def mock_non_superuser():
            user = Mock()
            user.is_superuser = False
            user.id = uuid4()
            return user
        
        from deltallm.proxy.dependencies import require_user
        app.dependency_overrides[require_user] = mock_non_superuser
        
        client = TestClient(app)
        
        payload = {
            "mode": "chat",
            "input_cost_per_token": "0.000001",
        }
        
        response = client.post(
            "/v1/pricing/models/test-model",
            json=payload,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
    
    def test_non_superuser_cannot_import(self, app):
        """Test that non-superusers cannot import pricing."""
        def mock_non_superuser():
            user = Mock()
            user.is_superuser = False
            user.id = uuid4()
            return user
        
        from deltallm.proxy.dependencies import require_user
        app.dependency_overrides[require_user] = mock_non_superuser
        
        client = TestClient(app)
        
        response = client.post(
            "/v1/pricing/import",
            files={"file": ("pricing.yaml", b"test: value", "application/x-yaml")},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
