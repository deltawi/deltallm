"""Router for load balancing and intelligent request routing."""

import asyncio
import random
import time
from typing import Any, AsyncIterator, Callable, Optional, Union
from dataclasses import dataclass, field
from enum import Enum

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    Message,
)
from deltallm.exceptions import RouterError, ServiceUnavailableError
from deltallm.providers.registry import ProviderRegistry
from deltallm.providers.base import BaseProvider


class RoutingStrategy(Enum):
    """Available routing strategies."""
    
    SIMPLE_SHUFFLE = "simple-shuffle"
    LEAST_BUSY = "least-busy"
    LATENCY_BASED = "latency-based"
    COST_BASED = "cost-based"
    USAGE_BASED = "usage-based"


@dataclass
class DeploymentConfig:
    """Configuration for a model deployment."""
    
    model_name: str
    litellm_params: dict[str, Any]
    model_info: Optional[dict[str, Any]] = None
    tpm: Optional[int] = None  # Tokens per minute limit
    rpm: Optional[int] = None  # Requests per minute limit
    timeout: Optional[float] = None
    
    # Runtime info
    current_requests: int = field(default=0, repr=False)
    total_requests: int = field(default=0, repr=False)
    failed_requests: int = field(default=0, repr=False)
    avg_latency: float = field(default=0.0, repr=False)
    last_used: float = field(default=0.0, repr=False)
    cooldown_until: float = field(default=0.0, repr=False)


@dataclass
class FallbackConfig:
    """Fallback configuration."""
    
    primary_model: str
    fallback_models: list[str]


class CooldownManager:
    """Manages deployment cooldowns."""
    
    def __init__(
        self,
        cooldown_time: float = 60.0,
        failure_threshold: int = 3,
    ):
        self.cooldown_time = cooldown_time
        self.failure_threshold = failure_threshold
        self._failures: dict[str, list[float]] = {}
    
    def record_failure(self, deployment_id: str) -> bool:
        """Record a failure and return True if deployment should be cooled down.
        
        Args:
            deployment_id: The deployment identifier
            
        Returns:
            True if deployment is now in cooldown
        """
        now = time.time()
        
        if deployment_id not in self._failures:
            self._failures[deployment_id] = []
        
        # Add failure
        self._failures[deployment_id].append(now)
        
        # Clean old failures (outside cooldown window)
        cutoff = now - self.cooldown_time
        self._failures[deployment_id] = [
            t for t in self._failures[deployment_id] if t > cutoff
        ]
        
        return len(self._failures[deployment_id]) >= self.failure_threshold
    
    def record_success(self, deployment_id: str) -> None:
        """Clear failures for a deployment.
        
        Args:
            deployment_id: The deployment identifier
        """
        self._failures.pop(deployment_id, None)
    
    def is_healthy(self, deployment_id: str) -> bool:
        """Check if deployment is healthy (not in cooldown).
        
        Args:
            deployment_id: The deployment identifier
            
        Returns:
            True if deployment is healthy
        """
        failures = self._failures.get(deployment_id, [])
        return len(failures) < self.failure_threshold


class Router:
    """Router for load balancing and intelligent request routing."""
    
    def __init__(
        self,
        model_list: list[dict[str, Any]],
        routing_strategy: Union[RoutingStrategy, str] = RoutingStrategy.SIMPLE_SHUFFLE,
        num_retries: int = 3,
        timeout: float = 60.0,
        fallbacks: Optional[list[dict[str, list[str]]]] = None,
        enable_cooldowns: bool = True,
        cooldown_time: float = 60.0,
        cooldown_failure_threshold: int = 3,
    ):
        """Initialize the router.
        
        Args:
            model_list: List of deployment configurations
            routing_strategy: Strategy for routing requests
            num_retries: Number of retries per deployment
            timeout: Default timeout for requests
            fallbacks: Fallback model mappings
            enable_cooldowns: Whether to enable deployment cooldowns
            cooldown_time: Cooldown duration in seconds
            cooldown_failure_threshold: Failures before cooldown
        """
        self.model_list: list[DeploymentConfig] = [
            DeploymentConfig(**config) for config in model_list
        ]
        self.routing_strategy = (
            routing_strategy 
            if isinstance(routing_strategy, RoutingStrategy)
            else RoutingStrategy(routing_strategy)
        )
        self.num_retries = num_retries
        self.timeout = timeout
        
        # Parse fallbacks
        self.fallbacks: dict[str, list[str]] = {}
        if fallbacks:
            for fallback in fallbacks:
                for primary, fallbacks_list in fallback.items():
                    self.fallbacks[primary] = fallbacks_list
        
        # Initialize cooldown manager
        self.cooldown = CooldownManager(
            cooldown_time=cooldown_time,
            failure_threshold=cooldown_failure_threshold,
        ) if enable_cooldowns else None
        
        # Provider cache
        self._providers: dict[str, BaseProvider] = {}
    
    def _get_provider(self, deployment: DeploymentConfig) -> BaseProvider:
        """Get or create provider for a deployment."""
        cache_key = f"{deployment.model_name}:{id(deployment)}"
        
        if cache_key not in self._providers:
            params = deployment.litellm_params.copy()
            model = params.pop("model", deployment.model_name)
            
            # Remove router-specific params that providers don't accept
            # These are used for rate limiting at the router level, not passed to providers
            params.pop("tpm", None)
            params.pop("rpm", None)
            
            # Get provider class
            provider_class = ProviderRegistry.get_for_model(model)
            
            # Create provider instance
            self._providers[cache_key] = provider_class(**params)
        
        return self._providers[cache_key]
    
    def _get_deployment_id(self, deployment: DeploymentConfig) -> str:
        """Get unique identifier for a deployment."""
        params = deployment.litellm_params
        return f"{params.get('model', deployment.model_name)}:{id(deployment)}"
    
    def _get_healthy_deployments(self, model: str) -> list[DeploymentConfig]:
        """Get healthy deployments for a model.
        
        Args:
            model: The model name
            
        Returns:
            List of healthy deployments
        """
        deployments = [
            d for d in self.model_list 
            if d.model_name == model
        ]
        
        if self.cooldown:
            deployments = [
                d for d in deployments
                if self.cooldown.is_healthy(self._get_deployment_id(d))
            ]
        
        return deployments
    
    def _select_deployment(self, model: str) -> Optional[DeploymentConfig]:
        """Select a deployment based on routing strategy.
        
        Args:
            model: The model name
            
        Returns:
            Selected deployment or None if none available
        """
        deployments = self._get_healthy_deployments(model)
        
        if not deployments:
            return None
        
        if self.routing_strategy == RoutingStrategy.SIMPLE_SHUFFLE:
            return random.choice(deployments)
        
        elif self.routing_strategy == RoutingStrategy.LEAST_BUSY:
            return min(deployments, key=lambda d: d.current_requests)
        
        elif self.routing_strategy == RoutingStrategy.LATENCY_BASED:
            # Prefer deployments with lower average latency
            # If no data, use random
            return min(deployments, key=lambda d: d.avg_latency or float('inf'))
        
        elif self.routing_strategy == RoutingStrategy.COST_BASED:
            # Prefer cheaper deployments
            # TODO: Implement cost-based routing
            return random.choice(deployments)
        
        elif self.routing_strategy == RoutingStrategy.USAGE_BASED:
            # Balance across deployments based on usage
            return min(deployments, key=lambda d: d.total_requests)
        
        return random.choice(deployments)
    
    def _update_deployment_stats(
        self, 
        deployment: DeploymentConfig, 
        success: bool,
        latency: float
    ) -> None:
        """Update deployment statistics.
        
        Args:
            deployment: The deployment
            success: Whether the request was successful
            latency: Request latency in seconds
        """
        deployment_id = self._get_deployment_id(deployment)
        
        if success:
            deployment.failed_requests = 0
            if self.cooldown:
                self.cooldown.record_success(deployment_id)
            
            # Update average latency
            if deployment.avg_latency == 0:
                deployment.avg_latency = latency
            else:
                deployment.avg_latency = 0.7 * deployment.avg_latency + 0.3 * latency
        else:
            deployment.failed_requests += 1
            if self.cooldown:
                if self.cooldown.record_failure(deployment_id):
                    deployment.cooldown_until = time.time() + (
                        self.cooldown.cooldown_time if self.cooldown else 60.0
                    )
        
        deployment.current_requests -= 1
        deployment.last_used = time.time()
    
    async def completion(
        self,
        model: str,
        messages: list[Message],
        *,
        stream: bool = False,
        timeout: Optional[float] = None,
        **kwargs
    ) -> Union[CompletionResponse, AsyncIterator[StreamChunk]]:
        """Execute a completion request with routing and fallbacks.
        
        Args:
            model: The model name (matches model_name in deployment config)
            messages: List of messages
            stream: Whether to stream
            timeout: Request timeout
            **kwargs: Additional parameters
            
        Returns:
            Completion response or async iterator
        """
        models_to_try = [model] + self.fallbacks.get(model, [])
        last_error = None
        
        for current_model in models_to_try:
            for attempt in range(self.num_retries + 1):
                deployment = self._select_deployment(current_model)
                
                if not deployment:
                    if attempt == self.num_retries:
                        break
                    continue
                
                deployment_id = self._get_deployment_id(deployment)
                provider = self._get_provider(deployment)
                
                start_time = time.time()
                deployment.current_requests += 1
                deployment.total_requests += 1
                
                try:
                    # Build request
                    request = CompletionRequest(
                        model=deployment.litellm_params.get("model", current_model),
                        messages=messages,
                        stream=stream,
                        timeout=timeout or deployment.timeout or self.timeout,
                        **kwargs
                    )
                    
                    if stream:
                        # For streaming, we need to handle differently
                        return self._stream_with_tracking(
                            provider, request, deployment, start_time
                        )
                    else:
                        # Non-streaming
                        response = await provider.chat_completion(request)
                        
                        latency = time.time() - start_time
                        self._update_deployment_stats(deployment, True, latency)
                        
                        return response
                
                except Exception as e:
                    latency = time.time() - start_time
                    self._update_deployment_stats(deployment, False, latency)
                    last_error = e
                    
                    # Wait before retry
                    if attempt < self.num_retries:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        # All attempts failed
        raise last_error or ServiceUnavailableError(
            f"No healthy deployments available for model '{model}'"
        )
    
    async def _stream_with_tracking(
        self,
        provider: BaseProvider,
        request: CompletionRequest,
        deployment: DeploymentConfig,
        start_time: float,
    ) -> AsyncIterator[StreamChunk]:
        """Stream with tracking.
        
        Args:
            provider: The provider
            request: The request
            deployment: The deployment
            start_time: Start time
            
        Yields:
            Stream chunks
        """
        try:
            async for chunk in provider.chat_completion_stream(request):
                yield chunk
            
            latency = time.time() - start_time
            self._update_deployment_stats(deployment, True, latency)
        
        except Exception as e:
            latency = time.time() - start_time
            self._update_deployment_stats(deployment, False, latency)
            raise
    
    def get_available_models(self) -> list[str]:
        """Get list of available models.
        
        Returns:
            List of model names
        """
        return list(set(d.model_name for d in self.model_list))
    
    def get_deployment_stats(self) -> list[dict[str, Any]]:
        """Get statistics for all deployments.
        
        Returns:
            List of deployment statistics
        """
        return [
            {
                "model_name": d.model_name,
                "provider": d.litellm_params.get("model", "unknown"),
                "current_requests": d.current_requests,
                "total_requests": d.total_requests,
                "avg_latency": d.avg_latency,
                "healthy": self.cooldown.is_healthy(self._get_deployment_id(d)) if self.cooldown else True,
            }
            for d in self.model_list
        ]
