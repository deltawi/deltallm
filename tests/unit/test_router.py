"""Tests for Router."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from deltallm.router import (
    Router,
    DeploymentConfig,
    RoutingStrategy,
    CooldownManager,
)
from deltallm.types import Message, CompletionRequest
from deltallm.exceptions import ServiceUnavailableError


class TestCooldownManager:
    """Tests for CooldownManager."""
    
    @pytest.fixture
    def manager(self):
        """Create a test cooldown manager."""
        return CooldownManager(cooldown_time=60.0, failure_threshold=3)
    
    def test_record_success(self, manager):
        """Test recording success clears failures."""
        manager.record_failure("deployment-1")
        manager.record_failure("deployment-1")
        
        # 2 failures with threshold of 3 should still be healthy
        assert manager.is_healthy("deployment-1")
        
        # Trigger cooldown
        manager.record_failure("deployment-1")
        assert not manager.is_healthy("deployment-1")
        
        # Success should clear the cooldown
        manager.record_success("deployment-1")
        assert manager.is_healthy("deployment-1")
    
    def test_cooldown_trigger(self, manager):
        """Test cooldown is triggered after threshold."""
        manager.record_failure("deployment-1")
        manager.record_failure("deployment-1")
        
        assert manager.is_healthy("deployment-1")  # 2 failures
        
        # Third failure triggers cooldown
        manager.record_failure("deployment-1")
        
        assert not manager.is_healthy("deployment-1")


class TestRouter:
    """Tests for Router."""
    
    @pytest.fixture
    def basic_config(self):
        """Basic router configuration."""
        return [
            {
                "model_name": "gpt-4",
                "litellm_params": {"model": "openai/gpt-4", "api_key": "test"},
            },
            {
                "model_name": "gpt-4",
                "litellm_params": {"model": "azure/gpt-4", "api_key": "test"},
            },
        ]
    
    def test_init(self, basic_config):
        """Test router initialization."""
        router = Router(model_list=basic_config)
        
        assert len(router.model_list) == 2
        assert router.routing_strategy == RoutingStrategy.SIMPLE_SHUFFLE
        assert router.num_retries == 3
    
    def test_init_with_strategy(self, basic_config):
        """Test initialization with specific strategy."""
        router = Router(
            model_list=basic_config,
            routing_strategy=RoutingStrategy.LEAST_BUSY
        )
        
        assert router.routing_strategy == RoutingStrategy.LEAST_BUSY
    
    def test_init_with_string_strategy(self, basic_config):
        """Test initialization with string strategy."""
        router = Router(
            model_list=basic_config,
            routing_strategy="least-busy"
        )
        
        assert router.routing_strategy == RoutingStrategy.LEAST_BUSY
    
    def test_get_healthy_deployments(self, basic_config):
        """Test getting healthy deployments."""
        router = Router(model_list=basic_config)
        
        deployments = router._get_healthy_deployments("gpt-4")
        
        assert len(deployments) == 2
    
    def test_get_healthy_deployments_with_cooldown(self, basic_config):
        """Test that cooled-down deployments are excluded."""
        router = Router(model_list=basic_config, enable_cooldowns=True)
        
        # Put one deployment in cooldown
        deployment_id = router._get_deployment_id(router.model_list[0])
        router.cooldown.record_failure(deployment_id)
        router.cooldown.record_failure(deployment_id)
        router.cooldown.record_failure(deployment_id)
        
        deployments = router._get_healthy_deployments("gpt-4")
        
        assert len(deployments) == 1
    
    def test_select_deployment_simple_shuffle(self, basic_config):
        """Test simple shuffle selection."""
        router = Router(
            model_list=basic_config,
            routing_strategy=RoutingStrategy.SIMPLE_SHUFFLE
        )
        
        deployment = router._select_deployment("gpt-4")
        
        assert deployment in router.model_list
    
    def test_select_deployment_least_busy(self, basic_config):
        """Test least busy selection."""
        router = Router(
            model_list=basic_config,
            routing_strategy=RoutingStrategy.LEAST_BUSY
        )
        
        # Set different request counts
        router.model_list[0].current_requests = 5
        router.model_list[1].current_requests = 2
        
        deployment = router._select_deployment("gpt-4")
        
        assert deployment == router.model_list[1]  # Less busy
    
    def test_get_available_models(self, basic_config):
        """Test getting available models."""
        router = Router(model_list=basic_config)
        
        models = router.get_available_models()
        
        assert "gpt-4" in models
        assert len(models) == 1
    
    def test_get_deployment_stats(self, basic_config):
        """Test getting deployment stats."""
        router = Router(model_list=basic_config)
        
        stats = router.get_deployment_stats()
        
        assert len(stats) == 2
        assert stats[0]["model_name"] == "gpt-4"
        assert "healthy" in stats[0]
