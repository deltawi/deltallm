"""Shared fixtures for proxy API tests."""

import pytest
from fastapi.testclient import TestClient

from deltallm.proxy.app import create_app


@pytest.fixture
def app():
    """Create a test FastAPI application."""
    return create_app()


@pytest.fixture
def client(app) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def sample_org_data():
    """Sample organization create data."""
    return {
        "name": "Test Organization",
        "slug": "test-org",
        "description": "A test organization",
        "max_budget": 1000.00,
    }


@pytest.fixture
def sample_team_data():
    """Sample team create data."""
    return {
        "name": "Test Team",
        "slug": "test-team",
        "description": "A test team",
        "max_budget": 500.00,
    }
