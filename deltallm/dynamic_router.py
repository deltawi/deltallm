"""Dynamic router for database-backed model deployments.

This module provides a router that fetches model deployments from the database,
enabling runtime configuration of model routing without server restarts.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Optional, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deltallm.db.models import ModelDeployment, ProviderConfig, TeamProviderAccess
from deltallm.db.session import get_session
from deltallm.exceptions import RouterError, ServiceUnavailableError
from deltallm.providers.base import BaseProvider
from deltallm.providers.registry import ProviderRegistry
from deltallm.types import CompletionRequest, CompletionResponse, Message, StreamChunk
from deltallm.utils.encryption import decrypt_api_key

logger = logging.getLogger(__name__)


class RoutingStrategy(Enum):
    """Available routing strategies."""

    SIMPLE_SHUFFLE = "simple-shuffle"
    LEAST_BUSY = "least-busy"
    LATENCY_BASED = "latency-based"
    PRIORITY_BASED = "priority-based"
    ROUND_ROBIN = "round-robin"


@dataclass
class DeploymentStats:
    """Runtime statistics for a deployment."""

    deployment_id: UUID
    current_requests: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    avg_latency: float = 0.0
    last_used: float = 0.0
    cooldown_until: float = 0.0


@dataclass
class CachedDeployment:
    """Cached deployment with provider info.

    Supports both linked deployments (with provider_config) and
    standalone deployments (deployment-level API key and config).
    """

    deployment: ModelDeployment
    provider_config: Optional[ProviderConfig]  # None for standalone deployments
    api_key: Optional[str] = None
    cached_at: float = field(default_factory=time.time)

    @property
    def model_type(self) -> str:
        """Get the model type for this deployment."""
        return getattr(self.deployment, 'model_type', 'chat')


class DeploymentCache:
    """Cache for model deployments with TTL."""

    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, list[CachedDeployment]] = {}
        self._last_refresh: float = 0.0

    def get(self, model_name: str, org_id: Optional[UUID] = None) -> Optional[list[CachedDeployment]]:
        """Get cached deployments for a model."""
        cache_key = self._make_key(model_name, org_id)
        cached = self._cache.get(cache_key)

        if cached and (time.time() - self._last_refresh) < self.ttl_seconds:
            return cached

        return None

    def set(
        self,
        model_name: str,
        deployments: list[CachedDeployment],
        org_id: Optional[UUID] = None,
    ) -> None:
        """Cache deployments for a model."""
        cache_key = self._make_key(model_name, org_id)
        self._cache[cache_key] = deployments
        self._last_refresh = time.time()

    def invalidate(self, model_name: Optional[str] = None, org_id: Optional[UUID] = None) -> None:
        """Invalidate cache entries."""
        if model_name:
            cache_key = self._make_key(model_name, org_id)
            self._cache.pop(cache_key, None)
        else:
            self._cache.clear()
        self._last_refresh = 0.0

    def _make_key(self, model_name: str, org_id: Optional[UUID]) -> str:
        """Create cache key."""
        return f"{model_name}:{org_id or 'global'}"


class CooldownManager:
    """Manages deployment cooldowns after failures."""

    def __init__(
        self,
        cooldown_time: float = 60.0,
        failure_threshold: int = 3,
    ):
        self.cooldown_time = cooldown_time
        self.failure_threshold = failure_threshold
        self._failures: dict[UUID, list[float]] = {}

    def record_failure(self, deployment_id: UUID) -> bool:
        """Record a failure and return True if deployment should be cooled down."""
        now = time.time()

        if deployment_id not in self._failures:
            self._failures[deployment_id] = []

        self._failures[deployment_id].append(now)

        # Clean old failures
        cutoff = now - self.cooldown_time
        self._failures[deployment_id] = [t for t in self._failures[deployment_id] if t > cutoff]

        return len(self._failures[deployment_id]) >= self.failure_threshold

    def record_success(self, deployment_id: UUID) -> None:
        """Clear failures for a deployment."""
        self._failures.pop(deployment_id, None)

    def is_healthy(self, deployment_id: UUID) -> bool:
        """Check if deployment is healthy."""
        failures = self._failures.get(deployment_id, [])
        return len(failures) < self.failure_threshold


class DynamicRouter:
    """Router that fetches model deployments from the database.

    This router supports:
    - Database-backed model deployments
    - Caching with configurable TTL
    - Multiple routing strategies
    - Automatic retries and fallbacks
    - Cooldown management for failing deployments
    - Organization-scoped deployments
    """

    def __init__(
        self,
        routing_strategy: Union[RoutingStrategy, str] = RoutingStrategy.PRIORITY_BASED,
        num_retries: int = 3,
        timeout: float = 60.0,
        fallbacks: Optional[dict[str, list[str]]] = None,
        enable_cooldowns: bool = True,
        cooldown_time: float = 60.0,
        cooldown_failure_threshold: int = 3,
        cache_ttl: float = 60.0,
    ):
        """Initialize the dynamic router.

        Args:
            routing_strategy: Strategy for selecting deployments
            num_retries: Number of retries per deployment
            timeout: Default timeout for requests
            fallbacks: Fallback model mappings
            enable_cooldowns: Whether to enable deployment cooldowns
            cooldown_time: Cooldown duration in seconds
            cooldown_failure_threshold: Failures before cooldown
            cache_ttl: Cache TTL in seconds
        """
        self.routing_strategy = (
            routing_strategy
            if isinstance(routing_strategy, RoutingStrategy)
            else RoutingStrategy(routing_strategy)
        )
        self.num_retries = num_retries
        self.timeout = timeout
        self.fallbacks = fallbacks or {}

        # Initialize cache and cooldown manager
        self._cache = DeploymentCache(ttl_seconds=cache_ttl)
        self.cooldown = (
            CooldownManager(
                cooldown_time=cooldown_time,
                failure_threshold=cooldown_failure_threshold,
            )
            if enable_cooldowns
            else None
        )

        # Runtime stats per deployment
        self._stats: dict[UUID, DeploymentStats] = {}

        # Provider instance cache
        self._providers: dict[str, BaseProvider] = {}

        # Round-robin counter
        self._round_robin_index: dict[str, int] = {}

    async def _fetch_deployments(
        self,
        model_name: str,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        model_type: Optional[str] = None,
    ) -> list[CachedDeployment]:
        """Fetch deployments from database.

        Args:
            model_name: The model name to find deployments for
            org_id: Optional organization ID for org-scoped deployments
            team_id: Optional team ID to filter by team's provider access
            model_type: Optional model type to filter by (chat, embedding, etc.)

        Returns:
            List of cached deployments
        """
        # Check cache first (include team_id and model_type in cache key)
        cache_key_suffix = f":{team_id}" if team_id else ""
        if model_type:
            cache_key_suffix += f":{model_type}"
        cached = self._cache.get(model_name + cache_key_suffix, org_id)
        if cached is not None:
            return cached

        # Fetch from database
        async with get_session() as session:
            # Build query
            query = (
                select(ModelDeployment)
                .where(
                    ModelDeployment.model_name == model_name,
                    ModelDeployment.is_active == True,
                )
                .options(selectinload(ModelDeployment.provider_config))
                .order_by(ModelDeployment.priority.desc())
            )

            # Filter by model type if specified
            if model_type:
                query = query.where(ModelDeployment.model_type == model_type)

            # Include both global and org-specific deployments
            if org_id:
                query = query.where(
                    (ModelDeployment.org_id == org_id) | (ModelDeployment.org_id.is_(None))
                )
            else:
                query = query.where(ModelDeployment.org_id.is_(None))

            # If team_id provided, filter to only deployments the team has access to
            # Team access is granted at the provider level
            if team_id:
                # Get provider IDs the team has access to
                team_access_query = select(TeamProviderAccess.provider_config_id).where(
                    TeamProviderAccess.team_id == team_id
                )
                team_provider_ids = (await session.execute(team_access_query)).scalars().all()

                # Filter deployments: include if linked to accessible provider OR standalone
                if team_provider_ids:
                    from sqlalchemy import or_
                    query = query.where(
                        or_(
                            ModelDeployment.provider_config_id.in_(team_provider_ids),
                            ModelDeployment.provider_config_id.is_(None),  # Standalone deployments
                        )
                    )
                else:
                    # Team has no provider access, only allow standalone deployments
                    query = query.where(ModelDeployment.provider_config_id.is_(None))

            result = await session.execute(query)
            deployments = result.scalars().all()

            # Build cached deployments with decrypted keys
            cached_deployments = []
            for deployment in deployments:
                # Handle linked deployments (with provider_config)
                if deployment.provider_config:
                    if not deployment.provider_config.is_active:
                        continue
                    
                    # Priority: deployment-level key > provider-level key
                    api_key = None
                    if deployment.api_key_encrypted:
                        # Deployment has its own API key (standalone override)
                        try:
                            api_key = decrypt_api_key(deployment.api_key_encrypted)
                        except Exception as e:
                            logger.warning(
                                f"Failed to decrypt deployment-level API key for "
                                f"{deployment.model_name}: {e}"
                            )
                    elif deployment.provider_config.api_key_encrypted:
                        # Fall back to provider-level key
                        try:
                            api_key = decrypt_api_key(deployment.provider_config.api_key_encrypted)
                        except Exception as e:
                            logger.warning(
                                f"Failed to decrypt provider API key for "
                                f"{deployment.provider_config.name}: {e}"
                            )
                            continue

                    cached_deployments.append(
                        CachedDeployment(
                            deployment=deployment,
                            provider_config=deployment.provider_config,
                            api_key=api_key,
                        )
                    )
                else:
                    # Standalone deployment (no provider_config)
                    # Must have deployment-level API key
                    if not deployment.api_key_encrypted:
                        logger.warning(
                            f"Standalone deployment {deployment.model_name} has no API key, skipping"
                        )
                        continue
                    
                    try:
                        api_key = decrypt_api_key(deployment.api_key_encrypted)
                    except Exception as e:
                        logger.warning(
                            f"Failed to decrypt API key for standalone deployment "
                            f"{deployment.model_name}: {e}"
                        )
                        continue
                    
                    # For standalone deployments, we store None as provider_config
                    # and use deployment-level fields directly
                    cached_deployments.append(
                        CachedDeployment(
                            deployment=deployment,
                            provider_config=None,
                            api_key=api_key,
                        )
                    )

            # Cache the results (include team_id in key)
            cache_model_key = model_name + cache_key_suffix
            self._cache.set(cache_model_key, cached_deployments, org_id)

            return cached_deployments

    def _get_healthy_deployments(
        self,
        deployments: list[CachedDeployment],
    ) -> list[CachedDeployment]:
        """Filter to healthy deployments only."""
        if not self.cooldown:
            return deployments

        return [d for d in deployments if self.cooldown.is_healthy(d.deployment.id)]

    def _get_stats(self, deployment_id: UUID) -> DeploymentStats:
        """Get or create stats for a deployment."""
        if deployment_id not in self._stats:
            self._stats[deployment_id] = DeploymentStats(deployment_id=deployment_id)
        return self._stats[deployment_id]

    def _select_deployment(
        self,
        deployments: list[CachedDeployment],
        model_name: str,
    ) -> Optional[CachedDeployment]:
        """Select a deployment based on routing strategy."""
        healthy = self._get_healthy_deployments(deployments)

        if not healthy:
            return None

        if self.routing_strategy == RoutingStrategy.SIMPLE_SHUFFLE:
            return random.choice(healthy)

        elif self.routing_strategy == RoutingStrategy.LEAST_BUSY:
            return min(healthy, key=lambda d: self._get_stats(d.deployment.id).current_requests)

        elif self.routing_strategy == RoutingStrategy.LATENCY_BASED:
            return min(
                healthy,
                key=lambda d: self._get_stats(d.deployment.id).avg_latency or float("inf"),
            )

        elif self.routing_strategy == RoutingStrategy.PRIORITY_BASED:
            # Already sorted by priority, pick from highest priority group
            max_priority = healthy[0].deployment.priority
            top_priority = [d for d in healthy if d.deployment.priority == max_priority]
            return random.choice(top_priority)

        elif self.routing_strategy == RoutingStrategy.ROUND_ROBIN:
            idx = self._round_robin_index.get(model_name, 0)
            selected = healthy[idx % len(healthy)]
            self._round_robin_index[model_name] = idx + 1
            return selected

        return random.choice(healthy)

    def _get_provider(self, cached_deployment: CachedDeployment) -> BaseProvider:
        """Get or create provider for a deployment.
        
        Supports both linked deployments (with provider_config) and
        standalone deployments (deployment-level configuration).
        """
        deployment = cached_deployment.deployment
        provider_config = cached_deployment.provider_config
        
        # Determine provider type and configuration source
        if provider_config:
            # Linked deployment - use provider config as base
            provider_type = provider_config.provider_type
            cache_key = f"{provider_config.id}:{deployment.id}"
            base_settings = provider_config.settings or {}
            base_api_base = provider_config.api_base
        else:
            # Standalone deployment - use deployment-level config
            if not deployment.provider_type:
                raise RouterError(f"Standalone deployment {deployment.id} has no provider_type")
            provider_type = deployment.provider_type
            cache_key = f"standalone:{deployment.id}"
            base_settings = {}
            base_api_base = None

        if cache_key not in self._providers:
            # Get provider class
            provider_class = ProviderRegistry.get_by_type(provider_type)

            if not provider_class:
                raise RouterError(f"Unknown provider type: {provider_type}")

            # Build provider kwargs
            kwargs: dict[str, Any] = {}
            if cached_deployment.api_key:
                kwargs["api_key"] = cached_deployment.api_key
            
            # Priority: deployment-level api_base > provider-level api_base
            api_base = deployment.api_base or base_api_base
            if api_base:
                kwargs["api_base"] = api_base

            # Merge settings: provider settings first, then deployment settings override
            merged_settings = dict(base_settings)
            if deployment.settings:
                merged_settings.update(deployment.settings)
            if merged_settings:
                kwargs.update(merged_settings)

            # Create provider instance
            self._providers[cache_key] = provider_class(**kwargs)

        return self._providers[cache_key]

    def _update_stats(
        self,
        deployment_id: UUID,
        success: bool,
        latency: float,
    ) -> None:
        """Update deployment statistics."""
        stats = self._get_stats(deployment_id)

        if success:
            stats.failed_requests = 0
            if self.cooldown:
                self.cooldown.record_success(deployment_id)

            # Update average latency (exponential moving average)
            if stats.avg_latency == 0:
                stats.avg_latency = latency
            else:
                stats.avg_latency = 0.7 * stats.avg_latency + 0.3 * latency
        else:
            stats.failed_requests += 1
            if self.cooldown:
                self.cooldown.record_failure(deployment_id)

        stats.current_requests -= 1
        stats.last_used = time.time()

    async def completion(
        self,
        model: str,
        messages: list[Message],
        *,
        stream: bool = False,
        timeout: Optional[float] = None,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        **kwargs,
    ) -> Union[CompletionResponse, AsyncIterator[StreamChunk]]:
        """Execute a completion request with routing.

        Args:
            model: The model name (public name from ModelDeployment)
            messages: List of messages
            stream: Whether to stream
            timeout: Request timeout
            org_id: Organization ID for org-scoped deployments
            team_id: Team ID for team-based provider access filtering
            **kwargs: Additional parameters

        Returns:
            Completion response or async iterator
        """
        models_to_try = [model] + self.fallbacks.get(model, [])
        last_error = None

        for current_model in models_to_try:
            # Fetch deployments (filtered by team access if team_id provided)
            deployments = await self._fetch_deployments(current_model, org_id, team_id)

            if not deployments:
                logger.warning(f"No deployments found for model: {current_model}")
                continue

            for attempt in range(self.num_retries + 1):
                deployment = self._select_deployment(deployments, current_model)

                if not deployment:
                    if attempt == self.num_retries:
                        break
                    continue

                stats = self._get_stats(deployment.deployment.id)
                provider = self._get_provider(deployment)

                start_time = time.time()
                stats.current_requests += 1
                stats.total_requests += 1

                try:
                    # Build request with provider's actual model name
                    request = CompletionRequest(
                        model=deployment.deployment.provider_model,
                        messages=messages,
                        stream=stream,
                        timeout=timeout
                        or (float(deployment.deployment.timeout) if deployment.deployment.timeout else None)
                        or self.timeout,
                        **kwargs,
                    )

                    if stream:
                        return self._stream_with_tracking(
                            provider,
                            request,
                            deployment.deployment.id,
                            start_time,
                        )
                    else:
                        response = await provider.chat_completion(request)
                        latency = time.time() - start_time
                        self._update_stats(deployment.deployment.id, True, latency)
                        return response

                except Exception as e:
                    latency = time.time() - start_time
                    self._update_stats(deployment.deployment.id, False, latency)
                    last_error = e
                    provider_name = deployment.provider_config.name if deployment.provider_config else "standalone"
                    logger.warning(
                        f"Request failed for {current_model} via {provider_name}: {e}"
                    )

                    # Wait before retry
                    if attempt < self.num_retries:
                        await asyncio.sleep(2**attempt)

        # All attempts failed
        raise last_error or ServiceUnavailableError(
            f"No healthy deployments available for model '{model}'"
        )

    async def _stream_with_tracking(
        self,
        provider: BaseProvider,
        request: CompletionRequest,
        deployment_id: UUID,
        start_time: float,
    ) -> AsyncIterator[StreamChunk]:
        """Stream with statistics tracking."""
        try:
            async for chunk in provider.chat_completion_stream(request):
                yield chunk

            latency = time.time() - start_time
            self._update_stats(deployment_id, True, latency)

        except Exception as e:
            latency = time.time() - start_time
            self._update_stats(deployment_id, False, latency)
            raise

    async def get_available_models(
        self,
        org_id: Optional[UUID] = None,
        model_type: Optional[str] = None,
    ) -> list[str]:
        """Get list of available models.

        Args:
            org_id: Optional organization ID
            model_type: Optional model type to filter by

        Returns:
            List of unique model names
        """
        async with get_session() as session:
            query = select(ModelDeployment.model_name).where(
                ModelDeployment.is_active == True,
            )

            if model_type:
                query = query.where(ModelDeployment.model_type == model_type)

            if org_id:
                query = query.where(
                    (ModelDeployment.org_id == org_id) | (ModelDeployment.org_id.is_(None))
                )
            else:
                query = query.where(ModelDeployment.org_id.is_(None))

            result = await session.execute(query.distinct())
            return [row[0] for row in result.all()]

    async def get_models_by_type(
        self,
        model_type: str,
        org_id: Optional[UUID] = None,
    ) -> list[str]:
        """Get available models filtered by type.

        Args:
            model_type: Model type to filter by (chat, embedding, etc.)
            org_id: Optional organization ID

        Returns:
            List of model names matching the type
        """
        return await self.get_available_models(org_id=org_id, model_type=model_type)

    async def get_deployment_info(
        self,
        model_name: str,
        org_id: Optional[UUID] = None,
    ) -> Optional[ModelDeployment]:
        """Get deployment info for validation.

        Args:
            model_name: The model name to look up
            org_id: Optional organization ID

        Returns:
            ModelDeployment if found, None otherwise
        """
        async with get_session() as session:
            query = (
                select(ModelDeployment)
                .where(
                    ModelDeployment.model_name == model_name,
                    ModelDeployment.is_active == True,
                )
            )

            if org_id:
                query = query.where(
                    (ModelDeployment.org_id == org_id) | (ModelDeployment.org_id.is_(None))
                )
            else:
                query = query.where(ModelDeployment.org_id.is_(None))

            result = await session.execute(query.limit(1))
            return result.scalar_one_or_none()

    async def get_deployment_stats(self, org_id: Optional[UUID] = None) -> list[dict[str, Any]]:
        """Get statistics for all deployments.

        Args:
            org_id: Optional organization ID

        Returns:
            List of deployment statistics
        """
        async with get_session() as session:
            query = (
                select(ModelDeployment)
                .where(ModelDeployment.is_active == True)
                .options(selectinload(ModelDeployment.provider_config))
            )

            if org_id:
                query = query.where(
                    (ModelDeployment.org_id == org_id) | (ModelDeployment.org_id.is_(None))
                )
            else:
                query = query.where(ModelDeployment.org_id.is_(None))

            result = await session.execute(query)
            deployments = result.scalars().all()

            stats_list = []
            for d in deployments:
                stats = self._get_stats(d.id)
                stats_list.append(
                    {
                        "model_name": d.model_name,
                        "provider_model": d.provider_model,
                        "provider": d.provider_config.name if d.provider_config else "unknown",
                        "provider_type": d.provider_config.provider_type if d.provider_config else "unknown",
                        "priority": d.priority,
                        "current_requests": stats.current_requests,
                        "total_requests": stats.total_requests,
                        "avg_latency": stats.avg_latency,
                        "healthy": self.cooldown.is_healthy(d.id) if self.cooldown else True,
                    }
                )

            return stats_list

    def invalidate_cache(self, model_name: Optional[str] = None) -> None:
        """Invalidate deployment cache.

        Args:
            model_name: Optional model name to invalidate (None = all)
        """
        self._cache.invalidate(model_name)
        logger.info(f"Invalidated deployment cache: {model_name or 'all'}")

    async def has_deployments(self) -> bool:
        """Check if any deployments exist in the database.

        Returns:
            True if at least one deployment exists
        """
        async with get_session() as session:
            result = await session.execute(
                select(ModelDeployment.id).where(ModelDeployment.is_active == True).limit(1)
            )
            return result.scalar_one_or_none() is not None
