"""Integration tests for the proxy server."""

import pytest
from fastapi.testclient import TestClient

from deltallm.proxy.app import create_app


@pytest.fixture
def test_app():
    """Create a test application."""
    import os
    import tempfile
    import yaml
    
    # Create minimal config
    config = {
        "model_list": [
            {
                "model_name": "gpt-4o",
                "litellm_params": {
                    "model": "openai/gpt-4o",
                    "api_key": "test-key",
                },
            },
        ],
        "general_settings": {
            "master_key": "sk-master-test",
        },
    }
    
    # Write temp config file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name
    
    try:
        app = create_app(config_path)
        return app
    finally:
        os.unlink(config_path)


@pytest.fixture
def client(test_app):
    """Create a test client using context manager for lifespan."""
    with TestClient(test_app) as c:
        yield c


class TestHealthEndpoints:
    """Tests for health endpoints (no auth required)."""
    
    def test_health_check(self, client):
        """Test basic health check."""
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
    
    def test_readiness_check(self, client):
        """Test readiness probe."""
        response = client.get("/health/readiness")
        
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    
    def test_liveness_check(self, client):
        """Test liveness probe."""
        response = client.get("/health/liveness")
        
        assert response.status_code == 200
        assert response.json()["status"] == "alive"
    
    def test_detailed_health(self, client):
        """Test detailed health check."""
        response = client.get("/health/detailed")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert "deployments" in response.json()


class TestAppStructure:
    """Tests for app structure and state."""
    
    def test_app_has_router(self, client):
        """Test that app has router in state."""
        assert hasattr(client.app.state, "router")
        assert client.app.state.router is not None
    
    def test_app_has_key_manager(self, client):
        """Test that app has key manager in state."""
        assert hasattr(client.app.state, "key_manager")
        assert client.app.state.key_manager is not None
    
    def test_app_has_config(self, client):
        """Test that app has config in state."""
        assert hasattr(client.app.state, "config")
        assert client.app.state.config is not None
    
    def test_router_has_models(self, client):
        """Test that router has models configured."""
        models = client.app.state.router.get_available_models()
        assert "gpt-4o" in models
