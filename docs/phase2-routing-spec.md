# Phase 2: Routing & Reliability Technical Specification

> **Source:** Master PRD §6.1-6.3, §6.13  
> **Phase:** 2 - Reliability & Routing  
> **Status:** Draft

---

## 1. Router Architecture

### 1.1 Router Class Interface

```python
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

class RoutingStrategy(Enum):
    SIMPLE_SHUFFLE = "simple-shuffle"
    LEAST_BUSY = "least-busy"
    LATENCY_BASED = "latency-based-routing"
    COST_BASED = "cost-based-routing"
    USAGE_BASED = "usage-based-routing"
    TAG_BASED = "tag-based-routing"
    PRIORITY_BASED = "priority-based-routing"
    WEIGHTED = "weighted"
    RATE_LIMIT_AWARE = "rate-limit-aware"

@dataclass
class Deployment:
    """Deployment configuration from model_list"""
    deployment_id: str  # unique identifier
    model_name: str     # public model group name
    litellm_params: Dict[str, Any]
    model_info: Dict[str, Any]
    
    # Routing metadata
    weight: int = 1
    priority: int = 0  # 0 = highest
    tags: List[str] = None
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None

@dataclass  
class DeploymentState:
    """Runtime state for a deployment"""
    deployment_id: str
    active_requests: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None  # unix timestamp
    last_success_at: Optional[float] = None
    cooldown_until: Optional[float] = None
    healthy: bool = True
    
    # Latency tracking (rolling window)
    latency_window: List[tuple] = None  # [(timestamp_ms, latency_ms), ...]
    
    # Usage tracking
    current_window_rpm: int = 0
    current_window_tpm: int = 0
    window_start: Optional[float] = None

class Router:
    """Main router coordinating deployment selection and failover"""
    
    def __init__(
        self,
        strategy: RoutingStrategy,
        state_backend: "DeploymentStateBackend",
        config: "RouterConfig"
    ):
        self.strategy = strategy
        self.state = state_backend
        self.config = config
        self._strategy_impl = self._load_strategy(strategy)
    
    async def select_deployment(
        self,
        model_group: str,
        request_context: Dict[str, Any]
    ) -> Optional[Deployment]:
        """
        Select a deployment for the request.
        
        Args:
            model_group: Target model group name
            request_context: Contains metadata.tags, priority hints, etc.
            
        Returns:
            Selected deployment or None if none available
        """
        # 1. Get candidate deployments
        candidates = await self._get_candidates(model_group, request_context)
        
        # 2. Filter out unhealthy/cooled-down deployments
        healthy = await self._filter_healthy(candidates)
        
        # 3. Apply tag/priority filters if specified
        filtered = self._apply_filters(healthy, request_context)
        
        # 4. Apply routing strategy
        if not filtered:
            return None
            
        return await self._strategy_impl.select(filtered, request_context)
    
    async def execute_with_failover(
        self,
        deployment: Deployment,
        request: Any,
        fallback_chain: List[Deployment] = None
    ) -> Any:
        """
        Execute request with retry and failover logic.
        
        Args:
            deployment: Primary deployment
            request: Prepared provider request
            fallback_chain: Ordered list of fallback deployments
            
        Returns:
            Provider response
            
        Raises:
            ProxyError: If all attempts exhausted
        """
        attempts = [deployment] + (fallback_chain or [])
        last_error = None
        
        for attempt, dep in enumerate(attempts):
            for retry in range(self.config.num_retries + 1):
                try:
                    # Track active request
                    await self.state.increment_active(dep.deployment_id)
                    
                    # Execute with timeout
                    response = await asyncio.wait_for(
                        self._execute(dep, request),
                        timeout=self.config.timeout
                    )
                    
                    # Record success
                    await self.state.record_success(dep.deployment_id)
                    return response
                    
                except asyncio.TimeoutError:
                    last_error = TimeoutError(f"Deployment {dep.deployment_id} timed out")
                    await self.state.record_failure(dep.deployment_id, "timeout")
                    
                except Exception as e:
                    last_error = e
                    await self.state.record_failure(dep.deployment_id, str(e))
                    
                    # Check if error is retryable
                    if not self._is_retryable(e):
                        break
                        
                    if retry < self.config.num_retries:
                        await asyncio.sleep(self.config.retry_after)
                        
            # Move to next deployment in chain
            continue
            
        raise ProxyError(f"All deployments exhausted: {last_error}")
    
    def _load_strategy(self, strategy: RoutingStrategy) -> "RoutingStrategyImpl":
        """Load the routing strategy implementation"""
        strategies = {
            RoutingStrategy.SIMPLE_SHUFFLE: SimpleShuffleStrategy(),
            RoutingStrategy.LEAST_BUSY: LeastBusyStrategy(self.state),
            RoutingStrategy.LATENCY_BASED: LatencyBasedStrategy(self.state),
            RoutingStrategy.COST_BASED: CostBasedStrategy(),
            RoutingStrategy.USAGE_BASED: UsageBasedStrategy(self.state),
            RoutingStrategy.TAG_BASED: TagBasedStrategy(),
            RoutingStrategy.PRIORITY_BASED: PriorityBasedStrategy(),
            RoutingStrategy.WEIGHTED: WeightedStrategy(),
            RoutingStrategy.RATE_LIMIT_AWARE: RateLimitAwareStrategy(self.state),
        }
        return strategies[strategy]
```

### 1.2 Deployment Selection Algorithm Flow

```
select_deployment(model_group, request_context)
  |
  v
1. Get deployments for model_group from registry
  |-- Load from model_list config
  |
  v
2. Get current state for each deployment
  |-- Redis: active_requests, latency, health, cooldown
  |
  v
3. Filter: exclude if cooldown_until > now()
  |
  v
4. Filter: exclude if healthy=False (from health checks)
  |
  v
5. Filter: apply tag filter if metadata.tags specified
  |-- deployment.tags must intersect with request tags
  |
  v
6. Filter: apply priority filter
  |-- Group by priority, start with priority=0
  |-- Only fallback to lower priority if higher priority pool exhausted
  |
  v
7. Pre-call checks (if enable_pre_call_checks)
  |-- Check RPM/TPM capacity per deployment
  |-- Skip deployments near limits
  |
  v
8. Apply routing strategy to filtered pool
  |-- Simple shuffle: random.choice()
  |-- Least busy: min(active_requests)
  |-- Latency: min(rolling_avg_latency)
  |-- Cost: min(input_cost + output_cost)
  |-- etc.
  |
  v
Return: Selected Deployment or None
```

---

## 2. Routing Strategy Implementations

### 2.1 Simple Shuffle (Default)

```python
import random

class SimpleShuffleStrategy:
    """Random selection across healthy deployments"""
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        """Random weighted selection"""
        if not deployments:
            return None
            
        # Weighted random choice
        weights = [d.weight for d in deployments]
        total = sum(weights)
        
        if total == 0:
            return random.choice(deployments)
            
        r = random.uniform(0, total)
        cumulative = 0
        for dep in deployments:
            cumulative += dep.weight
            if r <= cumulative:
                return dep
                
        return deployments[-1]
```

### 2.2 Least Busy

```python
class LeastBusyStrategy:
    """Route to deployment with fewest in-flight requests"""
    
    def __init__(self, state_backend: "DeploymentStateBackend"):
        self.state = state_backend
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Get active request counts for all candidates
        counts = await self.state.get_active_requests_batch(
            [d.deployment_id for d in deployments]
        )
        
        # Find minimum
        min_count = min(counts.values())
        candidates = [
            d for d in deployments 
            if counts.get(d.deployment_id, 0) == min_count
        ]
        
        # Break ties with weighted random
        return weighted_random_choice(candidates)
```

### 2.3 Latency-Based Routing

```python
class LatencyBasedStrategy:
    """Route to deployment with lowest rolling average latency"""
    
    def __init__(self, state_backend: "DeploymentStateBackend"):
        self.state = state_backend
        self.window_size_ms = 300000  # 5 minute window
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Get latency windows for all candidates
        latencies = await self.state.get_latency_windows_batch(
            [d.deployment_id for d in deployments],
            window_ms=self.window_size_ms
        )
        
        # Calculate weighted moving average for each
        avg_latencies = {}
        for dep in deployments:
            window = latencies.get(dep.deployment_id, [])
            if not window:
                # No data: assign high penalty but still consider
                avg_latencies[dep.deployment_id] = float('inf')
            else:
                avg_latencies[dep.deployment_id] = self._weighted_avg(window)
        
        # Select lowest average
        best_id = min(avg_latencies, key=avg_latencies.get)
        return next(d for d in deployments if d.deployment_id == best_id)
    
    def _weighted_avg(self, window: List[tuple]) -> float:
        """Exponentially weighted moving average"""
        # window: [(timestamp, latency_ms), ...]
        if not window:
            return float('inf')
            
        now = time.time() * 1000
        total_weight = 0
        weighted_sum = 0
        
        for ts, latency in window:
            age = now - ts
            weight = math.exp(-age / 60000)  # 1 minute decay
            weighted_sum += latency * weight
            total_weight += weight
            
        return weighted_sum / total_weight if total_weight > 0 else float('inf')
```

### 2.4 Cost-Based Routing

```python
class CostBasedStrategy:
    """Route to cheapest deployment first"""
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Sort by total cost per token
        sorted_deps = sorted(
            deployments,
            key=lambda d: d.input_cost_per_token + d.output_cost_per_token
        )
        
        return sorted_deps[0]
```

### 2.5 Usage-Based Routing

```python
class UsageBasedStrategy:
    """Balance based on TPM/RPM utilization percentage"""
    
    def __init__(self, state_backend: "DeploymentStateBackend"):
        self.state = state_backend
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Get current window usage for each deployment
        usage = await self.state.get_usage_batch(
            [d.deployment_id for d in deployments]
        )
        
        # Calculate utilization ratios
        utilizations = {}
        for dep in deployments:
            dep_usage = usage.get(dep.deployment_id, {})
            rpm_util = self._calc_utilization(
                dep_usage.get('rpm', 0),
                dep.rpm_limit
            )
            tpm_util = self._calc_utilization(
                dep_usage.get('tpm', 0),
                dep.tpm_limit
            )
            # Use max of RPM/TPM utilization
            utilizations[dep.deployment_id] = max(rpm_util, tpm_util)
        
        # Select lowest utilization
        best_id = min(utilizations, key=utilizations.get)
        return next(d for d in deployments if d.deployment_id == best_id)
    
    def _calc_utilization(self, current: int, limit: Optional[int]) -> float:
        if not limit or limit == 0:
            return 0.0
        return current / limit
```

### 2.6 Tag-Based Routing

```python
class TagBasedStrategy:
    """Filter by tags, then apply secondary strategy"""
    
    def __init__(self, fallback_strategy: "RoutingStrategyImpl" = None):
        self.fallback = fallback_strategy or SimpleShuffleStrategy()
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        request_tags = context.get('metadata', {}).get('tags', [])
        
        if not request_tags:
            # No tags specified, use fallback strategy on all
            return await self.fallback.select(deployments, context)
        
        # Filter to deployments matching ALL requested tags
        tagged = [
            d for d in deployments
            if d.tags and all(t in d.tags for t in request_tags)
        ]
        
        if not tagged:
            return None
            
        return await self.fallback.select(tagged, context)
```

### 2.7 Priority-Based Routing

```python
class PriorityBasedStrategy:
    """Try all priority-0 deployments first, then fallback"""
    
    def __init__(self, fallback_strategy: "RoutingStrategyImpl" = None):
        self.fallback = fallback_strategy or SimpleShuffleStrategy()
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Group by priority
        by_priority: Dict[int, List[Deployment]] = {}
        for d in deployments:
            by_priority.setdefault(d.priority, []).append(d)
        
        # Try priorities in order (0 = highest)
        for priority in sorted(by_priority.keys()):
            pool = by_priority[priority]
            selected = await self.fallback.select(pool, context)
            if selected:
                return selected
                
        return None
```

### 2.8 Rate-Limit Aware Routing

```python
class RateLimitAwareStrategy:
    """Skip deployments near rate limits"""
    
    def __init__(self, state_backend: "DeploymentStateBackend"):
        self.state = state_backend
        self.utilization_threshold = 0.9  # Skip if >90% utilized
    
    async def select(
        self,
        deployments: List[Deployment],
        context: Dict[str, Any]
    ) -> Optional[Deployment]:
        if not deployments:
            return None
            
        # Get usage for all candidates
        usage = await self.state.get_usage_batch(
            [d.deployment_id for d in deployments]
        )
        
        # Filter to deployments with capacity
        available = []
        for d in deployments:
            dep_usage = usage.get(d.deployment_id, {})
            rpm_util = dep_usage.get('rpm', 0) / (d.rpm_limit or float('inf'))
            tpm_util = dep_usage.get('tpm', 0) / (d.tpm_limit or float('inf'))
            
            if rpm_util < self.utilization_threshold and \
               tpm_util < self.utilization_threshold:
                available.append(d)
        
        if not available:
            return None
            
        # Use simple shuffle among available
        return random.choice(available)
```

---

## 3. Deployment State Management

### 3.1 Redis Schema

```
# ============================================================================
# ACTIVE REQUESTS (Counters)
# ============================================================================
# Key: active_requests:{deployment_id}
# Type: Integer counter (INCR/DECR)
# TTL: None (maintained by application)
# Value: Current number of in-flight requests

active_requests:openai-gpt4-primary -> 5
active_requests:azure-gpt4-westus -> 3

# ============================================================================
# LATENCY TRACKING (Sorted Sets)
# ============================================================================
# Key: latency:{deployment_id}
# Type: Sorted Set (ZADD with score=timestamp)
# TTL: 300 seconds (5 minute window)
# Value: latency_ms at timestamp

latency:openai-gpt4-primary -> {
  1707830400000: 450,  # timestamp_ms -> latency_ms
  1707830401000: 420,
  1707830402000: 480
}

# ============================================================================
# USAGE TRACKING (Rolling Window Counters)
# ============================================================================
# Key: usage_rpm:{deployment_id}:{window_minute}
# Type: Counter (INCR)
# TTL: 120 seconds (2 minutes, covers 1-minute window + buffer)
# Value: Request count for that minute window

usage_rpm:openai-gpt4-primary:2024-02-13T14:28 -> 150
usage_tpm:openai-gpt4-primary:2024-02-13T14:28 -> 45000

# ============================================================================
# COOLDOWN STATE (Strings with TTL)
# ============================================================================
# Key: cooldown:{deployment_id}
# Type: String with TTL
# Value: reason JSON {"reason": "timeout", "count": 3}
# TTL: cooldown_time seconds

cooldown:openai-gpt4-primary -> {"reason": "timeout", "count": 3}
  (expires in 60 seconds)

# ============================================================================
# HEALTH STATUS (Hash)
# ============================================================================
# Key: health:{deployment_id}
# Type: Hash
# TTL: None
# Fields:
#   - healthy: "true"/"false"
#   - consecutive_failures: "3"
#   - last_error: "timeout"
#   - last_error_at: "1707830400"
#   - last_success_at: "1707830300"

health:openai-gpt4-primary -> {
  healthy: "true",
  consecutive_failures: "0",
  last_error: "",
  last_error_at: "",
  last_success_at: "1707830400"
}

# ============================================================================
# FAILURE COUNTERS (for cooldown trigger)
# ============================================================================
# Key: failures:{deployment_id}
# Type: Counter
# TTL: 300 seconds (sliding window for failure counting)
# Value: Consecutive failure count

failures:openai-gpt4-primary -> 2
```

### 3.2 DeploymentStateBackend Interface

```python
from typing import Protocol
import json
import aioredis

class DeploymentStateBackend(Protocol):
    """Abstract backend for deployment state (Redis or in-memory)"""
    
    # Active Request Tracking
    async def increment_active(self, deployment_id: str) -> int: ...
    async def decrement_active(self, deployment_id: str) -> int: ...
    async def get_active_requests(self, deployment_id: str) -> int: ...
    async def get_active_requests_batch(self, deployment_ids: List[str]) -> Dict[str, int]: ...
    
    # Latency Tracking
    async def record_latency(self, deployment_id: str, latency_ms: float) -> None: ...
    async def get_latency_window(self, deployment_id: str, window_ms: int) -> List[tuple]: ...
    async def get_latency_windows_batch(
        self, 
        deployment_ids: List[str], 
        window_ms: int
    ) -> Dict[str, List[tuple]]: ...
    
    # Usage Tracking (RPM/TPM)
    async def increment_usage(
        self, 
        deployment_id: str, 
        tokens: int,
        window: str  # "2024-02-13T14:28"
    ) -> None: ...
    async def get_usage(self, deployment_id: str) -> Dict[str, int]: ...
    async def get_usage_batch(self, deployment_ids: List[str]) -> Dict[str, Dict[str, int]]: ...
    
    # Cooldown Management
    async def set_cooldown(
        self, 
        deployment_id: str, 
        duration_sec: int,
        reason: str
    ) -> None: ...
    async def clear_cooldown(self, deployment_id: str) -> None: ...
    async def is_cooled_down(self, deployment_id: str) -> bool: ...
    
    # Health Tracking
    async def record_success(self, deployment_id: str) -> None: ...
    async def record_failure(self, deployment_id: str, error: str) -> None: ...
    async def set_health(self, deployment_id: str, healthy: bool) -> None: ...
    async def get_health(self, deployment_id: str) -> Dict[str, Any]: ...
    async def get_health_batch(self, deployment_ids: List[str]) -> Dict[str, Dict[str, Any]]: ...


class RedisStateBackend:
    """Redis-backed deployment state"""
    
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.latency_window_ms = 300000  # 5 minutes
    
    async def increment_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        return await self.redis.incr(key)
    
    async def decrement_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        return await self.redis.decr(key)
    
    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        key = f"latency:{deployment_id}"
        timestamp = int(time.time() * 1000)
        pipe = self.redis.pipeline()
        pipe.zadd(key, {str(latency_ms): timestamp})
        pipe.pexpire(key, self.latency_window_ms)
        await pipe.execute()
    
    async def set_cooldown(
        self, 
        deployment_id: str, 
        duration_sec: int,
        reason: str
    ) -> None:
        key = f"cooldown:{deployment_id}"
        value = json.dumps({"reason": reason, "at": time.time()})
        await self.redis.setex(key, duration_sec, value)
    
    async def is_cooled_down(self, deployment_id: str) -> bool:
        key = f"cooldown:{deployment_id}"
        return await self.redis.exists(key) > 0
    
    async def record_failure(self, deployment_id: str, error: str) -> None:
        # Increment failure counter
        failures_key = f"failures:{deployment_id}"
        pipe = self.redis.pipeline()
        pipe.incr(failures_key)
        pipe.expire(failures_key, 300)  # 5 min sliding window
        results = await pipe.execute()
        
        failure_count = results[0]
        
        # Update health hash
        health_key = f"health:{deployment_id}"
        await self.redis.hmset(health_key, {
            "consecutive_failures": str(failure_count),
            "last_error": error[:200],  # truncate
            "last_error_at": str(int(time.time()))
        })
        
        return failure_count
    
    async def record_success(self, deployment_id: str) -> None:
        # Reset failure counter
        failures_key = f"failures:{deployment_id}"
        await self.redis.delete(failures_key)
        
        # Update health
        health_key = f"health:{deployment_id}"
        await self.redis.hmset(health_key, {
            "healthy": "true",
            "consecutive_failures": "0",
            "last_success_at": str(int(time.time()))
        })
```

---

## 4. Failover Logic

### 4.1 Fallback Chain Execution

```python
@dataclass
class FallbackConfig:
    """Configuration for failover behavior"""
    num_retries: int = 0
    retry_after: float = 0.0  # seconds between retries
    timeout: float = 600.0
    fallbacks: Dict[str, List[str]] = None  # model_group -> [fallback_groups]
    context_window_fallbacks: Dict[str, List[str]] = None
    content_policy_fallbacks: Dict[str, List[str]] = None

class FailoverManager:
    """Manages fallback chains and retry logic"""
    
    def __init__(
        self,
        config: FallbackConfig,
        deployment_registry: "DeploymentRegistry",
        state_backend: DeploymentStateBackend
    ):
        self.config = config
        self.registry = deployment_registry
        self.state = state_backend
    
    async def execute_with_failover(
        self,
        primary_deployment: Deployment,
        request: Any,
        model_group: str,
        request_tokens: int = 0
    ) -> Any:
        """
        Execute with full fallback chain:
        1. Retry primary deployment N times
        2. Try fallback deployments in order
        3. Try context window fallbacks if applicable
        4. Try content policy fallbacks if applicable
        """
        # Build execution chain
        chain = await self._build_fallback_chain(
            primary_deployment,
            model_group,
            request_tokens
        )
        
        last_error = None
        
        for priority_level, deployments in enumerate(chain):
            for deployment in deployments:
                # Check if deployment is healthy
                if not await self._is_available(deployment):
                    continue
                
                # Try with retries
                for attempt in range(self.config.num_retries + 1):
                    try:
                        result = await self._try_deployment(deployment, request)
                        return result
                        
                    except RetryableError as e:
                        last_error = e
                        if attempt < self.config.num_retries:
                            await asyncio.sleep(self.config.retry_after)
                            
                    except NonRetryableError as e:
                        # Don't retry, move to next deployment
                        last_error = e
                        break
                        
                    except ContextWindowExceededError as e:
                        # Skip to context window fallbacks
                        last_error = e
                        raise FallbackTriggerError("context_window", e)
                        
                    except ContentPolicyError as e:
                        # Skip to content policy fallbacks
                        last_error = e
                        raise FallbackTriggerError("content_policy", e)
        
        raise AllDeploymentsExhaustedError(f"All fallbacks exhausted: {last_error}")
    
    async def _build_fallback_chain(
        self,
        primary: Deployment,
        model_group: str,
        request_tokens: int
    ) -> List[List[Deployment]]:
        """
        Build ordered list of deployment groups to try.
        Returns list of priority groups.
        """
        chain = []
        seen = {primary.deployment_id}
        
        # Priority 0: Primary deployment (already passed in)
        chain.append([primary])
        
        # Priority 1: Same model group fallbacks
        same_group = await self.registry.get_deployments(model_group)
        same_group_filtered = [
            d for d in same_group 
            if d.deployment_id not in seen
        ]
        if same_group_filtered:
            chain.append(same_group_filtered)
            seen.update(d.deployment_id for d in same_group_filtered)
        
        # Priority 2: Model group fallbacks from config
        if self.config.fallbacks and model_group in self.config.fallbacks:
            for fallback_group in self.config.fallbacks[model_group]:
                group_deployments = await self.registry.get_deployments(fallback_group)
                new_deployments = [
                    d for d in group_deployments
                    if d.deployment_id not in seen
                ]
                if new_deployments:
                    chain.append(new_deployments)
                    seen.update(d.deployment_id for d in new_deployments)
        
        # Priority 3: Context window fallbacks (if token count known and large)
        if request_tokens > 0:
            primary_info = primary.model_info or {}
            primary_context = primary_info.get('max_tokens', 0)
            
            if primary_context > 0 and request_tokens > primary_context * 0.8:
                if self.config.context_window_fallbacks and model_group in self.config.context_window_fallbacks:
                    for fallback_group in self.config.context_window_fallbacks[model_group]:
                        group_deployments = await self.registry.get_deployments(fallback_group)
                        new_deployments = [
                            d for d in group_deployments
                            if d.deployment_id not in seen
                        ]
                        if new_deployments:
                            chain.append(new_deployments)
                            seen.update(d.deployment_id for d in new_deployments)
        
        return chain
    
    async def _is_available(self, deployment: Deployment) -> bool:
        """Check if deployment can accept requests"""
        # Check cooldown
        if await self.state.is_cooled_down(deployment.deployment_id):
            return False
        
        # Check health status
        health = await self.state.get_health(deployment.deployment_id)
        if health.get('healthy') == 'false':
            return False
            
        return True
    
    async def _try_deployment(self, deployment: Deployment, request: Any) -> Any:
        """Execute request on a single deployment with timeout"""
        start_time = time.time()
        
        try:
            # Increment active counter
            await self.state.increment_active(deployment.deployment_id)
            
            # Execute with timeout
            result = await asyncio.wait_for(
                self._execute_request(deployment, request),
                timeout=self.config.timeout
            )
            
            # Record success metrics
            latency_ms = (time.time() - start_time) * 1000
            await self.state.record_latency(deployment.deployment_id, latency_ms)
            await self.state.record_success(deployment.deployment_id)
            
            return result
            
        except asyncio.TimeoutError:
            await self.state.record_failure(deployment.deployment_id, "timeout")
            raise TimeoutError(f"Deployment {deployment.deployment_id} timed out")
            
        finally:
            # Decrement active counter
            await self.state.decrement_active(deployment.deployment_id)
```

### 4.2 Retry Policy

```python
class RetryPolicy:
    """Determines which errors are retryable"""
    
    # Errors that should trigger retry
    RETRYABLE_ERRORS = {
        "timeout",
        "connection_error",
        "rate_limit",
        "service_unavailable",
        "gateway_timeout",
    }
    
    # Errors that should NOT retry
    NON_RETRYABLE_ERRORS = {
        "authentication_error",
        "permission_denied",
        "invalid_request_error",
        "budget_exceeded",
        "model_not_found",
        "content_filter",
    }
    
    @classmethod
    def is_retryable(cls, error: Exception) -> bool:
        """Determine if error should trigger retry"""
        error_type = getattr(error, 'error_type', None)
        status_code = getattr(error, 'status_code', None)
        
        # By status code
        if status_code in (408, 429, 502, 503, 504):
            return True
            
        # By error type
        if error_type in cls.RETRYABLE_ERRORS:
            return True
            
        if error_type in cls.NON_RETRYABLE_ERRORS:
            return False
            
        # Default: don't retry unknown errors
        return False
```

---

## 5. Cooldown System

### 5.1 Cooldown Manager

```python
class CooldownManager:
    """
    Manages deployment cooldown based on failures.
    Triggered after allowed_fails consecutive failures.
    """
    
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        cooldown_time: int = 60,
        allowed_fails: int = 0,
        alert_callback: Callable = None
    ):
        self.state = state_backend
        self.cooldown_time = cooldown_time
        self.allowed_fails = allowed_fails
        self.alert_callback = alert_callback
    
    async def record_failure(
        self, 
        deployment_id: str, 
        error: str
    ) -> bool:
        """
        Record a failure. Returns True if deployment entered cooldown.
        """
        # Increment consecutive failures
        failure_count = await self.state.record_failure(deployment_id, error)
        
        # Check if cooldown should trigger
        if failure_count > self.allowed_fails:
            await self._enter_cooldown(deployment_id, error, failure_count)
            return True
            
        return False
    
    async def record_success(self, deployment_id: str) -> None:
        """Record success - resets failure counters"""
        await self.state.record_success(deployment_id)
    
    async def _enter_cooldown(
        self, 
        deployment_id: str, 
        reason: str,
        failure_count: int
    ) -> None:
        """Put deployment into cooldown"""
        # Set cooldown in state backend
        await self.state.set_cooldown(
            deployment_id, 
            self.cooldown_time,
            reason
        )
        
        # Update health status
        await self.state.set_health(deployment_id, False)
        
        # Fire alert if configured
        if self.alert_callback:
            await self.alert_callback({
                "alert_type": "cooldown_deployment",
                "deployment_id": deployment_id,
                "reason": reason,
                "failure_count": failure_count,
                "cooldown_until": time.time() + self.cooldown_time
            })
    
    async def check_cooldown(self, deployment_id: str) -> Optional[Dict]:
        """
        Check if deployment is in cooldown.
        Returns cooldown info or None if not in cooldown.
        """
        if await self.state.is_cooled_down(deployment_id):
            # Get health info for additional context
            health = await self.state.get_health(deployment_id)
            return {
                "in_cooldown": True,
                "consecutive_failures": int(health.get('consecutive_failures', 0)),
                "last_error": health.get('last_error'),
                "last_error_at": health.get('last_error_at')
            }
        return None
    
    async def manual_cooldown(
        self, 
        deployment_id: str, 
        duration_sec: int,
        reason: str = "manual"
    ) -> None:
        """Manually trigger cooldown for a deployment"""
        await self._enter_cooldown(deployment_id, reason, 0)
```

### 5.2 Cooldown State Recovery

```python
class CooldownRecoveryMonitor:
    """Monitors deployments and clears cooldown when they recover"""
    
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        deployment_registry: "DeploymentRegistry",
        check_interval: int = 30
    ):
        self.state = state_backend
        self.registry = deployment_registry
        self.check_interval = check_interval
    
    async def start_monitoring(self):
        """Start background cooldown recovery monitoring"""
        while True:
            await self._check_recoveries()
            await asyncio.sleep(self.check_interval)
    
    async def _check_recoveries(self):
        """Check if cooled-down deployments have recovered"""
        # Get all cooled-down deployments
        # Note: This requires scanning Redis for cooldown:* keys
        cooled_down = await self._get_cooled_down_deployments()
        
        for deployment_id in cooled_down:
            # Check if cooldown TTL has expired
            if not await self.state.is_cooled_down(deployment_id):
                # Cooldown naturally expired - mark healthy
                await self.state.set_health(deployment_id, True)
                await self.state.record_success(deployment_id)  # Reset failures
```

---

## 6. Health Check System

### 6.1 Background Health Checks

```python
@dataclass
class HealthCheckConfig:
    enabled: bool = False
    interval_seconds: int = 300  # 5 minutes
    timeout_seconds: int = 30
    health_check_model: str = "gpt-3.5-turbo"  # Lightweight model
    max_failures_before_unhealthy: int = 2

class BackgroundHealthChecker:
    """Periodic health checks for all deployments"""
    
    def __init__(
        self,
        config: HealthCheckConfig,
        deployment_registry: "DeploymentRegistry",
        state_backend: DeploymentStateBackend,
        provider_executor: "ProviderExecutor"
    ):
        self.config = config
        self.registry = deployment_registry
        self.state = state_backend
        self.executor = provider_executor
        self._running = False
    
    async def start(self):
        """Start background health check loop"""
        if not self.config.enabled:
            return
            
        self._running = True
        while self._running:
            try:
                await self._run_health_checks()
            except Exception as e:
                logger.error(f"Health check cycle failed: {e}")
                
            await asyncio.sleep(self.config.interval_seconds)
    
    def stop(self):
        """Stop background health checks"""
        self._running = False
    
    async def _run_health_checks(self):
        """Check all deployments"""
        deployments = await self.registry.get_all_deployments()
        
        # Run checks concurrently
        tasks = [
            self._check_deployment(d) 
            for d in deployments
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for deployment, result in zip(deployments, results):
            if isinstance(result, Exception):
                await self._mark_unhealthy(deployment, str(result))
            else:
                await self._mark_healthy(deployment)
    
    async def _check_deployment(self, deployment: Deployment) -> bool:
        """
        Perform health check on single deployment.
        Returns True if healthy.
        """
        # Build lightweight health check request
        check_request = {
            "model": self.config.health_check_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1
        }
        
        try:
            result = await asyncio.wait_for(
                self.executor.execute(deployment, check_request),
                timeout=self.config.timeout_seconds
            )
            return True
            
        except asyncio.TimeoutError:
            raise TimeoutError(f"Health check timeout after {self.config.timeout_seconds}s")
        except Exception as e:
            raise
    
    async def _mark_healthy(self, deployment: Deployment):
        """Mark deployment as healthy"""
        await self.state.set_health(deployment.deployment_id, True)
        health = await self.state.get_health(deployment.deployment_id)
        
        # Clear cooldown if it was in cooldown
        if await self.state.is_cooled_down(deployment.deployment_id):
            await self.state.clear_cooldown(deployment.deployment_id)
    
    async def _mark_unhealthy(self, deployment: Deployment, error: str):
        """Mark deployment as unhealthy"""
        current_health = await self.state.get_health(deployment.deployment_id)
        failures = int(current_health.get('consecutive_failures', 0)) + 1
        
        await self.state.set_health(deployment.deployment_id, False)
        await self.state.record_failure(deployment.deployment_id, error)
```

### 6.2 Passive Health Tracking

```python
class PassiveHealthTracker:
    """
    Tracks health based on actual request outcomes.
    Complements background health checks.
    """
    
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        failure_threshold: int = 3
    ):
        self.state = state_backend
        self.failure_threshold = failure_threshold
    
    async def record_request_outcome(
        self,
        deployment_id: str,
        success: bool,
        error: str = None
    ):
        """Called after every request to track health"""
        if success:
            await self.state.record_success(deployment_id)
        else:
            failures = await self.state.record_failure(deployment_id, error)
            
            if failures >= self.failure_threshold:
                await self.state.set_health(deployment_id, False)
```

### 6.3 Health Endpoint Response

```python
class HealthEndpointHandler:
    """Handles GET /health endpoint"""
    
    def __init__(
        self,
        deployment_registry: "DeploymentRegistry",
        state_backend: DeploymentStateBackend
    ):
        self.registry = deployment_registry
        self.state = state_backend
    
    async def get_health_status(self, model_filter: str = None) -> Dict:
        """
        Get health status for all or filtered deployments.
        
        Returns:
        {
          "status": "healthy" | "degraded" | "unhealthy",
          "timestamp": 1707830400,
          "deployments": [
            {
              "deployment_id": "openai-gpt4-primary",
              "model": "gpt-4",
              "healthy": true,
              "in_cooldown": false,
              "active_requests": 5,
              "consecutive_failures": 0,
              "last_error": null,
              "last_error_at": null,
              "last_success_at": 1707830300,
              "avg_latency_ms": 450
            },
            ...
          ]
        }
        """
        deployments = await self.registry.get_all_deployments()
        
        if model_filter:
            deployments = [
                d for d in deployments 
                if d.model_name == model_filter
            ]
        
        deployment_ids = [d.deployment_id for d in deployments]
        
        # Batch fetch health data
        health_data = await self.state.get_health_batch(deployment_ids)
        active_reqs = await self.state.get_active_requests_batch(deployment_ids)
        latencies = await self.state.get_latency_windows_batch(deployment_ids, 300000)
        
        dep_statuses = []
        healthy_count = 0
        
        for dep in deployments:
            h = health_data.get(dep.deployment_id, {})
            
            # Calculate average latency
            lat_window = latencies.get(dep.deployment_id, [])
            avg_latency = sum(l for _, l in lat_window) / len(lat_window) if lat_window else None
            
            in_cooldown = await self.state.is_cooled_down(dep.deployment_id)
            is_healthy = h.get('healthy') == 'true' and not in_cooldown
            
            if is_healthy:
                healthy_count += 1
            
            dep_statuses.append({
                "deployment_id": dep.deployment_id,
                "model": dep.model_name,
                "healthy": is_healthy,
                "in_cooldown": in_cooldown,
                "active_requests": active_reqs.get(dep.deployment_id, 0),
                "consecutive_failures": int(h.get('consecutive_failures', 0)),
                "last_error": h.get('last_error') or None,
                "last_error_at": int(h.get('last_error_at')) if h.get('last_error_at') else None,
                "last_success_at": int(h.get('last_success_at')) if h.get('last_success_at') else None,
                "avg_latency_ms": round(avg_latency, 2) if avg_latency else None
            })
        
        # Overall status
        total = len(deployments)
        if healthy_count == total:
            status = "healthy"
        elif healthy_count == 0:
            status = "unhealthy"
        else:
            status = "degraded"
        
        return {
            "status": status,
            "timestamp": int(time.time()),
            "healthy_count": healthy_count,
            "total_count": total,
            "deployments": dep_statuses
        }
```

---

## 7. Integration with Phase 1

### 7.1 Request Lifecycle Integration

```
Phase 1 Integration Points:

[Existing Phase 1 Pipeline]
1. Auth/Key Validation (worktree-core-auth)
2. Rate Limit Check (worktree-core-limits)
3. Cache Lookup (Phase 3)
   |-- Cache HIT --> Return cached response
   |-- Cache MISS --> Continue
   v
4. ROUTER SELECT (NEW - Phase 2) <-- INTEGRATION POINT
   |-- Call: router.select_deployment(model_group, context)
   |-- Receives: model_group, metadata.tags
   |-- Returns: Deployment
   v
5. Provider Execution (worktree-core-proxy)
   |-- Call: failover_manager.execute_with_failover(deployment, request)
   |-- Handles: retries, timeouts, fallback chains
   |-- Records: success/failure for cooldown/health
   v
6. Post-call processing
   |-- Call: passive_health_tracker.record_request_outcome()
   |-- Records latency to state backend
   v
7. Response
```

### 7.2 Router Integration Code

```python
# In worktree-core-proxy (Phase 1) request handler

async def handle_chat_completion(request: Request):
    # ... Phase 1: Auth, rate limits ...
    
    # Get model group from request
    model_group = resolve_model_group(request.json["model"])
    
    # Phase 2: Select deployment via router
    deployment = await router.select_deployment(
        model_group=model_group,
        request_context={
            "metadata": request.json.get("metadata", {}),
            "user_id": auth_context.user_id,
            "team_id": auth_context.team_id
        }
    )
    
    if not deployment:
        raise ModelUnavailableError(f"No healthy deployments for {model_group}")
    
    # Phase 2: Execute with failover
    try:
        response = await failover_manager.execute_with_failover(
            primary_deployment=deployment,
            request=prepare_provider_request(request.json),
            model_group=model_group,
            request_tokens=estimate_tokens(request.json["messages"])
        )
        
        # Record passive health (success)
        await passive_health_tracker.record_request_outcome(
            deployment.deployment_id,
            success=True
        )
        
    except AllDeploymentsExhaustedError as e:
        # Record passive health (failure)
        await passive_health_tracker.record_request_outcome(
            deployment.deployment_id,
            success=False,
            error=str(e)
        )
        raise ProxyError(f"All deployments failed: {e}")
    
    # ... Phase 1: Post-processing, logging ...
    return response
```

### 7.3 Integration Dependencies

| Phase 1 Component | Integration Point | Data Flow |
|-------------------|-------------------|-----------|
| `AuthMiddleware` | Provides `user_id`, `team_id` | Context to router |
| `RateLimitMiddleware` | Calls before routing | Pre-call RPM/TPM check |
| `ModelRegistry` | Model group resolution | Input to router |
| `ProviderExecutor` | Execution with failover | Wraps Phase 1 execution |
| `Cache` (Phase 3) | Skip routing on cache hit | Router only on miss |

---

## 8. Configuration Schema

### 8.1 router_settings YAML Structure

```yaml
# Router configuration
router_settings:
  # Routing strategy selection
  routing_strategy: "simple-shuffle"  # Options below
  
  # Strategy options:
  # - "simple-shuffle"          # Random weighted selection (default)
  # - "least-busy"              # Fewest in-flight requests
  # - "latency-based-routing"   # Lowest rolling avg latency
  # - "cost-based-routing"      # Cheapest per token
  # - "usage-based-routing"     # Lowest RPM/TPM utilization
  # - "tag-based-routing"       # Filter by tags, then shuffle
  # - "priority-based-routing"  # Try priority-0 first
  # - "weighted"                # Traffic distribution by weight
  # - "rate-limit-aware"        # Skip near-limit deployments
  
  # Retry and failover settings
  num_retries: 3              # Retry attempts per deployment
  retry_after: 5              # Seconds between retries
  timeout: 300                # Global request timeout (seconds)
  
  # Cooldown settings
  cooldown_time: 60           # Seconds deployment stays cooled
  allowed_fails: 0            # Failures before cooldown (0=immediate)
  
  # Pre-call checks
  enable_pre_call_checks: true  # Check RPM/TPM before routing
  
  # Model group aliases (for backward compatibility)
  model_group_alias:
    "gpt4": "gpt-4"
    "claude": "claude-3-sonnet"

# LiteLLM settings (fallbacks)
litellm_settings:
  # Fallback chain: primary -> [fallback1, fallback2]
  fallbacks:
    - gpt-4:
        - gpt-4-turbo-preview
        - gpt-3.5-turbo
    - claude-3-opus:
        - claude-3-sonnet
        - gpt-4
  
  # Context window fallbacks (triggered on token limit)
  context_window_fallbacks:
    - gpt-4:
        - gpt-4-32k
        - gpt-4-turbo-preview
  
  # Content policy fallbacks (triggered on content filter)
  content_policy_fallbacks:
    - claude-3-opus:
        - gpt-4

# General settings (health checks)
general_settings:
  background_health_checks: true
  health_check_interval: 300      # Seconds between checks
  health_check_model: "gpt-3.5-turbo"  # Lightweight model for probes

# Model list with routing metadata
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      # Routing metadata
      weight: 100              # Traffic weight (default: 1)
      priority: 0              # Priority tier (0=highest)
      tags: ["premium", "us-region"]
      input_cost_per_token: 0.00003
      output_cost_per_token: 0.00006
      rpm_limit: 10000
      tpm_limit: 1000000
      
  - model_name: gpt-4
    litellm_params:
      model: azure/gpt-4-deployment
      api_base: https://my-azure.openai.azure.com/
      api_key: os.environ/AZURE_API_KEY
    model_info:
      weight: 50
      priority: 1              # Lower priority than OpenAI
      tags: ["enterprise", "eu-region"]
      input_cost_per_token: 0.00004
      output_cost_per_token: 0.00008
```

### 8.2 Configuration Validation

```python
from pydantic import BaseModel, validator, Field
from typing import Literal, Dict, List, Optional

class RouterSettings(BaseModel):
    routing_strategy: Literal[
        "simple-shuffle",
        "least-busy", 
        "latency-based-routing",
        "cost-based-routing",
        "usage-based-routing",
        "tag-based-routing",
        "priority-based-routing",
        "weighted",
        "rate-limit-aware"
    ] = "simple-shuffle"
    
    num_retries: int = Field(default=0, ge=0, le=10)
    retry_after: float = Field(default=0.0, ge=0)
    timeout: float = Field(default=600.0, ge=1)
    cooldown_time: int = Field(default=60, ge=0)
    allowed_fails: int = Field(default=0, ge=0)
    enable_pre_call_checks: bool = False
    model_group_alias: Dict[str, str] = Field(default_factory=dict)
    
    @validator('timeout')
    def timeout_reasonable(cls, v):
        if v > 3600:
            raise ValueError('timeout should be under 1 hour')
        return v

class HealthCheckSettings(BaseModel):
    background_health_checks: bool = False
    health_check_interval: int = Field(default=300, ge=10)
    health_check_model: str = "gpt-3.5-turbo"

class FallbackConfig(BaseModel):
    fallbacks: List[Dict[str, List[str]]] = Field(default_factory=list)
    context_window_fallbacks: List[Dict[str, List[str]]] = Field(default_factory=list)
    content_policy_fallbacks: List[Dict[str, List[str]]] = Field(default_factory=list)
```

---

## 9. Worktree Breakdown

### 9.1 worktree-routing-engine

**Scope:** All load balancing strategies and deployment selection

**Inputs:**
- Router configuration from config.yaml
- Deployment registry from Phase 1
- Deployment state from worktree-health

**Deliverables:**
1. `src/routing/router.py` - Main Router class interface
2. `src/routing/strategies/base.py` - RoutingStrategyImpl abstract base
3. `src/routing/strategies/simple_shuffle.py` - Random weighted selection
4. `src/routing/strategies/least_busy.py` - In-flight request based
5. `src/routing/strategies/latency_based.py` - Rolling avg latency based
6. `src/routing/strategies/cost_based.py` - Cost-per-token based
7. `src/routing/strategies/usage_based.py` - RPM/TPM utilization based
8. `src/routing/strategies/tag_based.py` - Tag filtering strategy
9. `src/routing/strategies/priority_based.py` - Priority tier strategy
10. `src/routing/strategies/rate_limit_aware.py` - Pre-call limit checks
11. `tests/routing/test_strategies.py` - Strategy unit tests

**Integration Points:**
- Calls: `state_backend.get_active_requests_batch()`, `get_latency_windows_batch()`
- Called by: `middleware.request_router` (from worktree-core-proxy)

**Acceptance Criteria:**
- Each strategy correctly implements documented algorithm
- Strategy switching via config requires no code changes
- All strategies respect healthy/cooldown filters
- Tag/priority filtering applied before LB strategy
- Unit tests cover edge cases (empty pool, all unhealthy, etc.)

---

### 9.2 worktree-reliability

**Scope:** Retries, timeouts, failover chains, error classification

**Inputs:**
- Router settings (num_retries, retry_after, timeout, fallbacks)
- Deployment from router
- Provider executor from Phase 1

**Deliverables:**
1. `src/reliability/failover_manager.py` - FailoverManager class
2. `src/reliability/retry_policy.py` - Error classification (retryable vs non-retryable)
3. `src/reliability/fallback_chains.py` - Fallback chain builder
4. `src/reliability/errors.py` - FallbackTriggerError, AllDeploymentsExhaustedError
5. `tests/reliability/test_failover.py` - Failover logic tests
6. `tests/reliability/test_retry_policy.py` - Error classification tests

**Integration Points:**
- Calls: `provider_executor.execute()` (from Phase 1)
- Calls: `state_backend.increment_active()`, `record_success()`, `record_failure()`
- Called by: `request_handler.handle_chat_completion()` (wraps provider call)

**Acceptance Criteria:**
- Retries respect num_retries and retry_after settings
- Timeout applies to both standard and streaming flows
- Fallback chains execute in declared order
- Context window fallbacks trigger on appropriate error
- Content policy fallbacks trigger on content filter errors
- Non-retryable errors don't trigger unnecessary retries

---

### 9.3 worktree-health

**Scope:** Health checks, deployment state, cooldown system

**Inputs:**
- Router settings (cooldown_time, allowed_fails)
- General settings (background_health_checks, health_check_interval)
- Redis connection config

**Deliverables:**
1. `src/health/state_backend.py` - DeploymentStateBackend interface + Redis impl
2. `src/health/cooldown_manager.py` - CooldownManager class
3. `src/health/background_checker.py` - BackgroundHealthChecker class
4. `src/health/passive_tracker.py` - PassiveHealthTracker class
5. `src/health/health_endpoint.py` - HealthEndpointHandler for GET /health
6. `src/health/recovery_monitor.py` - CooldownRecoveryMonitor class
7. `tests/health/test_cooldown.py` - Cooldown logic tests
8. `tests/health/test_health_checks.py` - Health check tests

**Integration Points:**
- Calls: Redis (active_requests, latency, health, cooldown keys)
- Called by: `failover_manager` (records success/failure)
- Called by: `router` (checks cooldown/health before selection)
- Called by: `background_health_checker` (periodic health checks)

**Acceptance Criteria:**
- Redis key patterns follow specification exactly
- Cooldown triggers after allowed_fails+1 consecutive failures
- Cooldown state shared across instances via Redis
- Background health checks run at configured interval
- Health endpoint returns per-deployment status with timestamps
- Passive tracking updates health on every request outcome
- Automatic recovery when cooldown expires

---

## 10. Cross-Module Dependencies

```
                    ┌─────────────────────────────────────┐
                    │         worktree-core-proxy         │
                    │   (Phase 1 - request lifecycle)     │
                    └───────────────┬─────────────────────┘
                                    │ calls
                                    v
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│ worktree-routing-   │◄───│   worktree-health   │───►│  worktree-core-db   │
│      engine         │uses│  (state backend)    │uses│   (deployment info) │
└─────────┬───────────┘    └─────────────────────┘    └─────────────────────┘
          │ provides deployment
          v
┌─────────────────────┐
│ worktree-reliability│
│  (failover/retries) │
└─────────┬───────────┘
          │ executes with
          v
┌─────────────────────┐
│  Provider Executor  │
│    (Phase 1)        │
└─────────────────────┘
```

---

## 11. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Redis unavailable | Graceful degradation: fall back to in-memory state (single-instance only) |
| Cooldown state inconsistency | Use Redis TTL for automatic expiration; periodic reconciliation |
| Latency window overflow | Bounded window size with automatic pruning (5 min max) |
| Health check costs | Configurable `health_check_model` for lightweight probes |
| Tag routing empty pool | Return clear error; don't fall back silently |
| Priority routing exhaustion | Return None for exhausted pool; let caller handle |
| Pre-call RPM/TPM estimation | Approximate based on request tokens; document limitations |

---

## 12. Open Questions

1. **Retry backoff strategy**: Linear (current) vs exponential backoff?
2. **Circuit breaker pattern**: Implement explicit circuit breaker beyond cooldown?
3. **Health check request cost**: Should we track health check spend separately?
4. **Cross-region routing**: Should latency-based consider client region?
