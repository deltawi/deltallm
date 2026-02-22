# Phase 3: Caching & Guardrails Technical Specification

> **Source:** Master PRD §6.4-6.12  
> **Phase:** 3 - Caching & Guardrails  
> **Status:** Draft

---

## 1. Cache Architecture

### 1.1 CacheBackend Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, AsyncIterator
from dataclasses import dataclass
from enum import Enum

class CacheBackendType(Enum):
    MEMORY = "local"           # In-memory dict
    REDIS = "redis"            # Standard Redis
    REDIS_CLUSTER = "redis-cluster"
    REDIS_SENTINEL = "redis-sentinel"
    S3 = "s3"                  # P1
    DISK = "disk"              # P2
    QDRANT_SEMANTIC = "qdrant-semantic"  # P2

@dataclass
class CacheEntry:
    """Cached response with metadata"""
    response: Dict[str, Any]   # Full OpenAI-compatible response
    model: str
    cached_at: float           # Unix timestamp
    ttl: int                   # Original TTL
    token_count: int           # For cache size tracking

@dataclass
class CacheConfig:
    """Cache configuration"""
    backend_type: CacheBackendType
    ttl: int = 3600           # Default TTL in seconds
    max_size: Optional[int] = None  # For in-memory cache
    
    # Redis settings
    redis_host: Optional[str] = None
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_url: Optional[str] = None
    redis_ssl: bool = False
    redis_cluster_nodes: Optional[List[str]] = None
    redis_sentinel_nodes: Optional[List[str]] = None
    redis_sentinel_master_name: Optional[str] = None
    
    # S3 settings (P1)
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_prefix: str = "deltallm-cache/"
    
    # Semantic cache (P2)
    similarity_threshold: float = 0.8
    qdrant_api_base: Optional[str] = None

class CacheBackend(ABC):
    """Abstract base for cache backends"""
    
    @abstractmethod
    async def get(self, key: str) -> Optional[CacheEntry]:
        """Retrieve cache entry by key"""
        pass
    
    @abstractmethod
    async def set(
        self, 
        key: str, 
        entry: CacheEntry,
        ttl: Optional[int] = None
    ) -> None:
        """Store cache entry with TTL"""
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete cache entry"""
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check backend connectivity"""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close backend connections"""
        pass
```

### 1.2 Backend Implementations

#### In-Memory Cache (P0)

```python
import asyncio
from collections import OrderedDict
from typing import Optional
import time

class InMemoryCacheBackend(CacheBackend):
    """Dictionary-based cache for single-instance deployments"""
    
    def __init__(self, max_size: Optional[int] = None):
        self.max_size = max_size or 10000
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[CacheEntry]:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            
            # Check TTL expiration
            if time.time() - entry.cached_at > entry.ttl:
                del self._cache[key]
                return None
            
            # Move to end (LRU)
            self._cache.move_to_end(key)
            return entry
    
    async def set(
        self, 
        key: str, 
        entry: CacheEntry,
        ttl: Optional[int] = None
    ) -> None:
        async with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = entry
    
    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)
    
    async def exists(self, key: str) -> bool:
        entry = await self.get(key)
        return entry is not None
    
    async def health_check(self) -> bool:
        return True  # Always healthy (in-memory)
    
    async def close(self) -> None:
        self._cache.clear()
```

#### Redis Cache (P0)

```python
import json
import aioredis
from aioredis.sentinel import Sentinel

class RedisCacheBackend(CacheBackend):
    """Redis-based cache for production multi-instance deployments"""
    
    def __init__(self, config: CacheConfig):
        self.config = config
        self.redis: Optional[aioredis.Redis] = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize Redis connection"""
        if self._initialized:
            return
            
        if self.config.redis_url:
            self.redis = aioredis.from_url(
                self.config.redis_url,
                ssl=self.config.redis_ssl
            )
        elif self.config.backend_type == CacheBackendType.REDIS_CLUSTER:
            self.redis = aioredis.RedisCluster(
                startup_nodes=self.config.redis_cluster_nodes,
                ssl=self.config.redis_ssl
            )
        elif self.config.backend_type == CacheBackendType.REDIS_SENTINEL:
            sentinel = Sentinel(
                self.config.redis_sentinel_nodes,
                ssl=self.config.redis_ssl
            )
            self.redis = sentinel.master_for(
                self.config.redis_sentinel_master_name
            )
        else:
            # Standard Redis
            self.redis = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                ssl=self.config.redis_ssl,
                decode_responses=False  # We'll handle encoding
            )
        
        self._initialized = True
    
    async def get(self, key: str) -> Optional[CacheEntry]:
        if not self.redis:
            return None
            
        try:
            data = await self.redis.get(f"cache:{key}")
            if data is None:
                return None
            
            # Decode and parse
            decoded = json.loads(data.decode('utf-8'))
            return CacheEntry(
                response=decoded['response'],
                model=decoded['model'],
                cached_at=decoded['cached_at'],
                ttl=decoded['ttl'],
                token_count=decoded.get('token_count', 0)
            )
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None
    
    async def set(
        self, 
        key: str, 
        entry: CacheEntry,
        ttl: Optional[int] = None
    ) -> None:
        if not self.redis:
            return
            
        try:
            # Serialize entry
            data = json.dumps({
                'response': entry.response,
                'model': entry.model,
                'cached_at': entry.cached_at,
                'ttl': entry.ttl,
                'token_count': entry.token_count
            })
            
            effective_ttl = ttl or entry.ttl
            await self.redis.setex(
                f"cache:{key}",
                effective_ttl,
                data.encode('utf-8')
            )
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
    
    async def delete(self, key: str) -> None:
        if self.redis:
            await self.redis.delete(f"cache:{key}")
    
    async def exists(self, key: str) -> bool:
        if not self.redis:
            return False
        return await self.redis.exists(f"cache:{key}") > 0
    
    async def health_check(self) -> bool:
        if not self.redis:
            return False
        try:
            await self.redis.ping()
            return True
        except:
            return False
    
    async def close(self) -> None:
        if self.redis:
            await self.redis.close()
```

#### S3 Cache Backend (P1)

```python
import boto3
import json
from botocore.exceptions import ClientError

class S3CacheBackend(CacheBackend):
    """S3-based cache for large/persistent storage (P1)"""
    
    def __init__(self, config: CacheConfig):
        self.config = config
        self.s3 = boto3.client('s3', region_name=config.s3_region)
        self.bucket = config.s3_bucket
        self.prefix = config.s3_prefix
    
    def _make_key(self, key: str) -> str:
        """Generate S3 object key"""
        return f"{self.prefix}{key[:2]}/{key[2:4]}/{key}"
    
    async def get(self, key: str) -> Optional[CacheEntry]:
        try:
            response = self.s3.get_object(
                Bucket=self.bucket,
                Key=self._make_key(key)
            )
            data = json.loads(response['Body'].read().decode('utf-8'))
            
            # Check expiration (stored as metadata)
            expires = response['Metadata'].get('expires')
            if expires and float(expires) < time.time():
                return None
            
            return CacheEntry(**data)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return None
            raise
    
    async def set(
        self, 
        key: str, 
        entry: CacheEntry,
        ttl: Optional[int] = None
    ) -> None:
        data = json.dumps({
            'response': entry.response,
            'model': entry.model,
            'cached_at': entry.cached_at,
            'ttl': entry.ttl,
            'token_count': entry.token_count
        })
        
        expires = time.time() + (ttl or entry.ttl)
        
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._make_key(key),
            Body=data.encode('utf-8'),
            Metadata={'expires': str(expires)}
        )
```

### 1.3 Backend Factory

```python
class CacheBackendFactory:
    """Factory for creating cache backends from config"""
    
    @staticmethod
    async def create(config: CacheConfig) -> CacheBackend:
        """Create and initialize appropriate backend"""
        
        if config.backend_type == CacheBackendType.MEMORY:
            return InMemoryCacheBackend(max_size=config.max_size)
            
        elif config.backend_type in (
            CacheBackendType.REDIS,
            CacheBackendType.REDIS_CLUSTER,
            CacheBackendType.REDIS_SENTINEL
        ):
            backend = RedisCacheBackend(config)
            await backend.initialize()
            return backend
            
        elif config.backend_type == CacheBackendType.S3:
            return S3CacheBackend(config)
            
        else:
            raise ValueError(f"Unsupported cache backend: {config.backend_type}")
```

---

## 2. Cache Key Composition

### 2.1 Key Fields

```python
from typing import Dict, Any, List, Optional
import hashlib
import json

# Default fields included in cache key
DEFAULT_CACHE_KEY_FIELDS = {
    "model",
    "messages",
    "temperature",
    "top_p",
    "max_tokens",
    "n",
    "stop",
    "tools",
    "tool_choice",
    "response_format",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
    "user",
    "seed",
}

class CacheKeyBuilder:
    """Builds deterministic cache keys from request data"""
    
    def __init__(
        self,
        fields: Optional[set] = None,
        custom_salt: str = ""
    ):
        self.fields = fields or DEFAULT_CACHE_KEY_FIELDS
        self.salt = custom_salt
    
    def build_key(
        self, 
        request_data: Dict[str, Any],
        custom_key: Optional[str] = None
    ) -> str:
        """
        Build cache key from request data.
        
        Args:
            request_data: Full request payload
            custom_key: Optional override (from metadata.cache_key)
            
        Returns:
            SHA256 hex digest of normalized key components
        """
        if custom_key:
            return f"custom:{custom_key}"
        
        # Extract relevant fields
        key_components = {}
        for field in self.fields:
            if field in request_data:
                key_components[field] = request_data[field]
        
        # Normalize and hash
        normalized = self._normalize(key_components)
        key_string = json.dumps(normalized, sort_keys=True, separators=(',', ':'))
        
        if self.salt:
            key_string = f"{self.salt}:{key_string}"
        
        return hashlib.sha256(key_string.encode('utf-8')).hexdigest()
    
    def _normalize(self, data: Any) -> Any:
        """Normalize data for consistent hashing"""
        if isinstance(data, dict):
            return {k: self._normalize(v) for k, v in sorted(data.items())}
        elif isinstance(data, list):
            return [self._normalize(item) for item in data]
        elif isinstance(data, float):
            # Normalize float precision
            return round(data, 6)
        else:
            return data
```

### 2.2 Cache Control

```python
from enum import Enum
from typing import Optional

class CacheControl(Enum):
    """Cache control directives"""
    DEFAULT = "default"      # Normal cache behavior
    NO_CACHE = "no-cache"    # Skip read, write new
    NO_STORE = "no-store"    # Skip write, read ok
    BYPASS = "bypass"        # Skip both read and write

@dataclass
class CacheOptions:
    """Per-request cache options"""
    control: CacheControl = CacheControl.DEFAULT
    ttl: Optional[int] = None           # Override default TTL
    custom_key: Optional[str] = None    # Override key generation
    tags: Optional[List[str]] = None    # For cache invalidation groups

def parse_cache_options(request_data: Dict[str, Any], headers: Dict[str, str]) -> CacheOptions:
    """Parse cache options from request"""
    options = CacheOptions()
    
    # Check headers first (highest priority)
    cache_control_header = headers.get('Cache-Control', '').lower()
    if 'no-cache' in cache_control_header:
        options.control = CacheControl.NO_CACHE
    elif 'no-store' in cache_control_header:
        options.control = CacheControl.NO_STORE
    
    # Check for custom TTL header
    ttl_header = headers.get('Cache-TTL')
    if ttl_header:
        try:
            options.ttl = int(ttl_header)
        except ValueError:
            pass
    
    # Check metadata
    metadata = request_data.get('metadata', {})
    
    if 'cache_ttl' in metadata:
        options.ttl = metadata['cache_ttl']
    
    if 'cache_key' in metadata:
        options.custom_key = metadata['cache_key']
    
    if 'cache' in metadata:
        # Explicit cache control in metadata
        cache_setting = metadata['cache']
        if cache_setting == False:
            options.control = CacheControl.BYPASS
        elif cache_setting == 'no-cache':
            options.control = CacheControl.NO_CACHE
        elif cache_setting == 'no-store':
            options.control = CacheControl.NO_STORE
    
    return options
```

---

## 3. Cache Middleware

### 3.1 Cache Middleware Class

```python
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

class CacheMiddleware(BaseHTTPMiddleware):
    """
    Middleware handling cache lookup and write.
    Integrates into request lifecycle between guardrails and routing.
    """
    
    def __init__(
        self,
        app,
        cache_backend: CacheBackend,
        key_builder: CacheKeyBuilder,
        default_ttl: int = 3600,
        enabled_endpoints: Optional[set] = None
    ):
        super().__init__(app)
        self.backend = cache_backend
        self.key_builder = key_builder
        self.default_ttl = default_ttl
        self.enabled_endpoints = enabled_endpoints or {
            "/v1/chat/completions",
            "/v1/completions",
            "/v1/embeddings"
        }
    
    async def dispatch(self, request: Request, call_next):
        """Process request through cache layer"""
        
        # Check if caching applies to this endpoint
        if not self._should_cache(request):
            return await call_next(request)
        
        # Parse cache control options
        cache_options = parse_cache_options(
            await self._get_request_data(request),
            dict(request.headers)
        )
        
        # Handle BYPASS
        if cache_options.control == CacheControl.BYPASS:
            response = await call_next(request)
            response.headers['x-litellm-cache-hit'] = 'false'
            return response
        
        # Try cache lookup (unless no-cache)
        cache_key = None
        cache_entry = None
        
        if cache_options.control != CacheControl.NO_CACHE:
            cache_key = self.key_builder.build_key(
                await self._get_request_data(request),
                cache_options.custom_key
            )
            cache_entry = await self.backend.get(cache_key)
        
        if cache_entry:
            # Cache HIT
            response = self._build_cached_response(cache_entry, cache_key)
            response.headers['x-litellm-cache-hit'] = 'true'
            
            # Emit cache hit event
            await self._emit_cache_event('hit', cache_key, cache_entry)
            
            return response
        
        # Cache MISS - proceed to actual handler
        response = await call_next(request)
        
        # Mark as cache miss
        response.headers['x-litellm-cache-hit'] = 'false'
        
        # Store in cache (unless no-store)
        if cache_options.control != CacheControl.NO_STORE:
            await self._store_response(
                request, 
                response, 
                cache_key,
                cache_options.ttl
            )
        
        return response
    
    def _should_cache(self, request: Request) -> bool:
        """Check if request should use cache"""
        # Only POST requests
        if request.method != "POST":
            return False
        
        # Only enabled endpoints
        return request.url.path in self.enabled_endpoints
    
    async def _get_request_data(self, request: Request) -> Dict[str, Any]:
        """Extract request data (cached to avoid re-reading body)"""
        if not hasattr(request.state, 'request_data'):
            body = await request.body()
            request.state.request_data = json.loads(body)
        return request.state.request_data
    
    def _build_cached_response(
        self, 
        entry: CacheEntry,
        cache_key: str
    ) -> Response:
        """Build response from cache entry"""
        response_data = entry.response.copy()
        
        # Add cache metadata
        response_data['_cache_metadata'] = {
            'cache_hit': True,
            'cached_at': entry.cached_at,
            'cache_key': cache_key
        }
        
        return Response(
            content=json.dumps(response_data),
            media_type="application/json",
            headers={
                'x-litellm-cache-hit': 'true',
                'x-litellm-cache-key': cache_key
            }
        )
    
    async def _store_response(
        self,
        request: Request,
        response: Response,
        cache_key: Optional[str],
        ttl: Optional[int]
    ):
        """Store response in cache"""
        try:
            # Only cache successful responses
            if response.status_code != 200:
                return
            
            # Parse response body
            response_data = json.loads(response.body)
            
            # Don't cache error responses
            if 'error' in response_data:
                return
            
            # Generate cache key if not already done
            if cache_key is None:
                cache_key = self.key_builder.build_key(
                    await self._get_request_data(request)
                )
            
            # Build cache entry
            entry = CacheEntry(
                response=response_data,
                model=response_data.get('model', 'unknown'),
                cached_at=time.time(),
                ttl=ttl or self.default_ttl,
                token_count=response_data.get('usage', {}).get('total_tokens', 0)
            )
            
            # Store
            await self.backend.set(cache_key, entry, ttl)
            
            # Emit cache miss event
            await self._emit_cache_event('miss', cache_key, entry)
            
        except Exception as e:
            logger.warning(f"Failed to cache response: {e}")
    
    async def _emit_cache_event(
        self, 
        event_type: str, 
        cache_key: str,
        entry: CacheEntry
    ):
        """Emit cache event for metrics/logging"""
        # To be implemented by worktree-obs-metrics
        pass
```

---

## 4. Streaming Cache

### 4.1 Streaming Cache Handler

```python
class StreamingCacheHandler:
    """
    Handles caching for streaming (SSE) responses.
    Reconstructs SSE streams from cached complete responses.
    """
    
    def __init__(self, backend: CacheBackend, key_builder: CacheKeyBuilder):
        self.backend = backend
        self.key_builder = key_builder
        self._active_streams: Dict[str, List[Dict]] = {}  # request_id -> chunks
    
    async def handle_streaming_request(
        self,
        request_data: Dict[str, Any],
        cache_options: CacheOptions
    ) -> Optional[AsyncIterator[str]]:
        """
        Handle cache lookup for streaming request.
        Returns reconstructed SSE stream if cache hit, None if miss.
        """
        if cache_options.control == CacheControl.NO_CACHE:
            return None
        
        cache_key = self.key_builder.build_key(
            request_data,
            cache_options.custom_key
        )
        
        entry = await self.backend.get(cache_key)
        if not entry:
            return None
        
        # Reconstruct SSE stream from cached response
        return self._reconstruct_sse_stream(entry.response)
    
    def _reconstruct_sse_stream(
        self, 
        response: Dict[str, Any]
    ) -> AsyncIterator[str]:
        """
        Reconstruct SSE stream from complete response.
        Simulates chunk-by-chunk delivery.
        """
        async def generator():
            # Build chunks that mimic real streaming
            chunks = self._response_to_chunks(response)
            
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n\n"
            
            # End marker
            yield "data: [DONE]\n\n"
        
        return generator()
    
    def _response_to_chunks(self, response: Dict[str, Any]) -> List[Dict]:
        """
        Convert complete response to simulated stream chunks.
        """
        chunks = []
        
        # Get content from choices
        choices = response.get('choices', [])
        if not choices:
            return chunks
        
        message = choices[0].get('message', {})
        content = message.get('content', '')
        
        # Split content into word-sized chunks
        words = content.split(' ')
        
        for i, word in enumerate(words):
            chunk = {
                'id': response.get('id', f"chatcmpl-{uuid.uuid4().hex}"),
                'object': 'chat.completion.chunk',
                'created': response.get('created', int(time.time())),
                'model': response.get('model', 'unknown'),
                'choices': [{
                    'index': 0,
                    'delta': {
                        'content': word + (' ' if i < len(words) - 1 else '')
                    },
                    'finish_reason': None
                }]
            }
            chunks.append(chunk)
        
        # Final chunk with finish_reason
        if chunks:
            chunks[-1]['choices'][0]['finish_reason'] = choices[0].get('finish_reason', 'stop')
        
        return chunks
    
    async def accumulate_stream_chunks(
        self,
        request_id: str,
        chunk: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Accumulate streaming chunks for potential caching.
        Returns complete response when stream finishes.
        """
        if request_id not in self._active_streams:
            self._active_streams[request_id] = []
        
        # Store chunk
        self._active_streams[request_id].append(chunk)
        
        # Check if this is the final chunk
        choices = chunk.get('choices', [])
        if choices and choices[0].get('finish_reason'):
            # Stream complete - assemble response
            complete_response = self._assemble_response(
                self._active_streams[request_id]
            )
            del self._active_streams[request_id]
            return complete_response
        
        return None
    
    def _assemble_response(self, chunks: List[Dict]) -> Dict[str, Any]:
        """Assemble complete response from stream chunks"""
        if not chunks:
            return {}
        
        # Use first chunk as base
        response = {
            'id': chunks[0].get('id'),
            'object': 'chat.completion',
            'created': chunks[0].get('created'),
            'model': chunks[0].get('model'),
            'choices': [{
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': ''
                },
                'finish_reason': None
            }],
            'usage': {
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0
            }
        }
        
        # Accumulate content
        content_parts = []
        for chunk in chunks:
            delta = chunk.get('choices', [{}])[0].get('delta', {})
            if 'content' in delta:
                content_parts.append(delta['content'])
            
            # Capture finish_reason from final chunk
            finish_reason = chunk.get('choices', [{}])[0].get('finish_reason')
            if finish_reason:
                response['choices'][0]['finish_reason'] = finish_reason
        
        response['choices'][0]['message']['content'] = ''.join(content_parts)
        
        return response
    
    def discard_stream(self, request_id: str):
        """Discard accumulated chunks (client disconnected)"""
        self._active_streams.pop(request_id, None)
```

---

## 5. Cache Metrics

### 5.1 Metrics Collection

```python
from typing import Callable, Dict, Any
from enum import Enum

class CacheEventType(Enum):
    HIT = "cache_hit"
    MISS = "cache_miss"
    WRITE = "cache_write"
    EVICT = "cache_evict"
    ERROR = "cache_error"

class CacheMetricsCollector:
    """Collect and emit cache metrics"""
    
    def __init__(self):
        self._callbacks: List[Callable] = []
    
    def register_callback(self, callback: Callable):
        """Register a callback for cache events"""
        self._callbacks.append(callback)
    
    async def emit_event(
        self,
        event_type: CacheEventType,
        cache_key: str,
        model: str,
        ttl: int = 0,
        token_count: int = 0,
        latency_ms: float = 0,
        error: str = None
    ):
        """Emit cache event to all registered callbacks"""
        event = {
            'event_type': event_type.value,
            'cache_key': cache_key,
            'model': model,
            'ttl': ttl,
            'token_count': token_count,
            'latency_ms': latency_ms,
            'timestamp': time.time(),
            'error': error
        }
        
        for callback in self._callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.warning(f"Cache metrics callback failed: {e}")

# Prometheus metrics integration
class PrometheusCacheMetrics:
    """Prometheus metrics for cache operations"""
    
    def __init__(self):
        from prometheus_client import Counter, Histogram
        
        self.cache_hits = Counter(
            'litellm_cache_hit_total',
            'Total cache hits',
            ['model']
        )
        self.cache_misses = Counter(
            'litellm_cache_miss_total',
            'Total cache misses',
            ['model']
        )
        self.cache_writes = Counter(
            'litellm_cache_write_total',
            'Total cache writes',
            ['model']
        )
        self.cache_latency = Histogram(
            'litellm_cache_operation_duration_seconds',
            'Cache operation latency',
            ['operation']  # get, set, delete
        )
    
    async def handle_event(self, event: Dict[str, Any]):
        """Process cache event for Prometheus"""
        model = event.get('model', 'unknown')
        
        if event['event_type'] == 'cache_hit':
            self.cache_hits.labels(model=model).inc()
        elif event['event_type'] == 'cache_miss':
            self.cache_misses.labels(model=model).inc()
        elif event['event_type'] == 'cache_write':
            self.cache_writes.labels(model=model).inc()
```

### 5.2 Response Headers

```python
def add_cache_headers(
    response: Response,
    cache_hit: bool,
    cache_key: str = None,
    cached_at: float = None
) -> Response:
    """
    Add cache-related headers to response.
    
    Headers:
    - x-litellm-cache-hit: "true" | "false"
    - x-litellm-cache-key: <cache_key> (optional, for debugging)
    """
    response.headers['x-litellm-cache-hit'] = 'true' if cache_hit else 'false'
    
    if cache_key and response.headers.get('x-litellm-debug') == 'true':
        response.headers['x-litellm-cache-key'] = cache_key
    
    return response
```

---

## 6. Guardrail Framework

### 6.1 Base Class Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

class GuardrailMode(Enum):
    """Enforcement mode"""
    PRE_CALL = "pre_call"       # Before LLM call
    POST_CALL = "post_call"     # After LLM response
    DURING_CALL = "during_call" # During streaming (P2)

class GuardrailAction(Enum):
    """Action when violation detected"""
    BLOCK = "block"     # Raise exception, block request/response
    LOG = "log"         # Log violation, allow to proceed

@dataclass
class GuardrailResult:
    """Result of guardrail check"""
    passed: bool
    action: GuardrailAction
    violation_type: Optional[str] = None
    message: Optional[str] = None
    modified_data: Optional[Dict[str, Any]] = None  # For PII masking

class CustomGuardrail(ABC):
    """
    Base class for custom guardrails.
    
    Implement one or more hook methods. Unimplemented hooks are no-ops.
    """
    
    def __init__(
        self,
        name: str,
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK
    ):
        self.name = name
        self.mode = mode
        self.default_on = default_on
        self.action = action
    
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Dict[str, Any],
        cache: Any,  # Cache backend for state storage
        data: Dict[str, Any],  # Request data
        call_type: str  # "completion", "embedding", etc.
    ) -> Optional[Dict[str, Any]]:
        """
        Called before LLM provider call.
        
        Args:
            user_api_key_dict: Authenticated key info
            cache: Cache backend for stateful guardrails
            data: Request payload (mutable)
            call_type: Type of LLM call
            
        Returns:
            Modified data dict, or None if no modification.
            Raise GuardrailViolationError to block.
        """
        return None
    
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],  # Request data
        user_api_key_dict: Dict[str, Any],
        response: Dict[str, Any]  # LLM response
    ) -> None:
        """
        Called after successful LLM response.
        
        Raises:
            GuardrailViolationError: To block response delivery
        """
        pass
    
    async def async_post_call_failure_hook(
        self,
        request_data: Dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Dict[str, Any]
    ) -> None:
        """
        Called after failed LLM call.
        
        For logging/analyzing failures.
        """
        pass
    
    async def async_moderation_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Dict[str, Any],
        call_type: str
    ) -> GuardrailResult:
        """
        Moderation-specific hook.
        
        Returns:
            GuardrailResult with pass/fail and optional modified data
        """
        return GuardrailResult(passed=True, action=self.action)

class GuardrailViolationError(Exception):
    """Raised when guardrail blocks request/response"""
    
    def __init__(
        self,
        guardrail_name: str,
        message: str,
        violation_type: str = None,
        status_code: int = 400
    ):
        self.guardrail_name = guardrail_name
        self.message = message
        self.violation_type = violation_type
        self.status_code = status_code
        super().__init__(f"{guardrail_name}: {message}")
```

### 6.2 Guardrail Registry

```python
class GuardrailRegistry:
    """Registry for guardrail instances"""
    
    def __init__(self):
        self._guardrails: Dict[str, CustomGuardrail] = {}
        self._by_mode: Dict[GuardrailMode, List[CustomGuardrail]] = {
            mode: [] for mode in GuardrailMode
        }
    
    def register(self, guardrail: CustomGuardrail) -> None:
        """Register a guardrail instance"""
        self._guardrails[guardrail.name] = guardrail
        self._by_mode[guardrail.mode].append(guardrail)
    
    def unregister(self, name: str) -> None:
        """Remove a guardrail"""
        if name in self._guardrails:
            guardrail = self._guardrails.pop(name)
            self._by_mode[guardrail.mode].remove(guardrail)
    
    def get_for_mode(self, mode: GuardrailMode) -> List[CustomGuardrail]:
        """Get all guardrails for a given mode"""
        return self._by_mode[mode].copy()
    
    def get(self, name: str) -> Optional[CustomGuardrail]:
        """Get guardrail by name"""
        return self._guardrails.get(name)
    
    def get_default_guardrails(self) -> List[CustomGuardrail]:
        """Get guardrails with default_on=True"""
        return [g for g in self._guardrails.values() if g.default_on]
    
    def get_for_key(self, key_data: Dict[str, Any]) -> List[CustomGuardrail]:
        """
        Get guardrails applicable to a key.
        
        Uses key's guardrails list, or defaults if not specified.
        """
        key_guardrails = key_data.get('guardrails', [])
        
        if key_guardrails:
            # Use key-specific guardrails
            return [
                self._guardrails[name] 
                for name in key_guardrails 
                if name in self._guardrails
            ]
        else:
            # Use defaults
            return self.get_default_guardrails()

# Global registry instance
guardrail_registry = GuardrailRegistry()
```

### 6.3 Guardrail Loader

```python
import importlib

class GuardrailLoader:
    """Load guardrails from YAML config or module paths"""
    
    @staticmethod
    def load_from_config(config: List[Dict[str, Any]]) -> None:
        """Load guardrails from YAML configuration"""
        for guardrail_config in config:
            name = guardrail_config['guardrail_name']
            params = guardrail_config.get('litellm_params', {})
            
            # Get class path
            class_path = params.get('guardrail')
            if not class_path:
                raise ValueError(f"Guardrail {name} missing 'guardrail' param")
            
            # Import and instantiate
            guardrail_class = GuardrailLoader._import_class(class_path)
            
            # Parse mode
            mode_str = params.get('mode', 'pre_call')
            mode = GuardrailMode(mode_str)
            
            # Parse action
            action_str = params.get('default_action', 'block')
            action = GuardrailAction(action_str)
            
            # Instantiate
            instance = guardrail_class(
                name=name,
                mode=mode,
                default_on=params.get('default_on', True),
                action=action
            )
            
            guardrail_registry.register(instance)
    
    @staticmethod
    def _import_class(class_path: str) -> type:
        """Import class from module path string"""
        module_path, class_name = class_path.rsplit('.', 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
```

---

## 7. Built-in Guardrails

### 7.1 Presidio Integration (P1)

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

class PresidioGuardrail(CustomGuardrail):
    """
    Microsoft Presidio PII detection and anonymization.
    Runs in both pre_call (anonymize input) and post_call (detect output PII).
    """
    
    SUPPORTED_ENTITIES = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", 
        "CREDIT_CARD", "US_SSN", "US_PASSPORT",
        "IBAN", "IP_ADDRESS", "LOCATION"
    ]
    
    def __init__(
        self,
        name: str = "presidio",
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK,
        anonymize: bool = True,  # If True, mask PII; if False, just detect
        entities: List[str] = None,
        language: str = "en",
        threshold: float = 0.5
    ):
        super().__init__(name, mode, default_on, action)
        self.anonymize = anonymize
        self.entities = entities or self.SUPPORTED_ENTITIES
        self.language = language
        self.threshold = threshold
        
        # Initialize engines
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
    
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Dict[str, Any],
        cache: Any,
        data: Dict[str, Any],
        call_type: str
    ) -> Optional[Dict[str, Any]]:
        """Analyze and optionally anonymize input messages"""
        messages = data.get('messages', [])
        
        modified_messages = []
        for msg in messages:
            content = msg.get('content', '')
            if not isinstance(content, str):
                modified_messages.append(msg)
                continue
            
            # Analyze for PII
            results = self.analyzer.analyze(
                text=content,
                entities=self.entities,
                language=self.language
            )
            
            # Filter by threshold
            detected = [r for r in results if r.score >= self.threshold]
            
            if detected:
                if self.anonymize:
                    # Anonymize PII
                    anonymized = self.anonymizer.anonymize(
                        text=content,
                        analyzer_results=detected,
                        operators={
                            entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
                            for entity in self.entities
                        }
                    )
                    modified_msg = msg.copy()
                    modified_msg['content'] = anonymized.text
                    modified_messages.append(modified_msg)
                else:
                    # Just detect - raise violation
                    entities_found = [r.entity_type for r in detected]
                    if self.action == GuardrailAction.BLOCK:
                        raise GuardrailViolationError(
                            guardrail_name=self.name,
                            message=f"PII detected: {', '.join(entities_found)}",
                            violation_type="pii_detected",
                            status_code=400
                        )
            else:
                modified_messages.append(msg)
        
        # Return modified data if changed
        if modified_messages != messages:
            modified_data = data.copy()
            modified_data['messages'] = modified_messages
            return modified_data
        
        return None
    
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Dict[str, Any],
        response: Dict[str, Any]
    ) -> None:
        """Detect PII in LLM output"""
        content = response.get('choices', [{}])[0].get('message', {}).get('content', '')
        
        results = self.analyzer.analyze(
            text=content,
            entities=self.entities,
            language=self.language
        )
        
        detected = [r for r in results if r.score >= self.threshold]
        
        if detected and self.action == GuardrailAction.BLOCK:
            entities_found = [r.entity_type for r in detected]
            raise GuardrailViolationError(
                guardrail_name=self.name,
                message=f"PII detected in output: {', '.join(entities_found)}",
                violation_type="pii_in_output",
                status_code=400
            )
```

### 7.2 Lakera Guard Integration (P1)

```python
import aiohttp

class LakeraGuardrail(CustomGuardrail):
    """
    Lakera Guard for prompt injection detection.
    Pre-call only (input validation).
    """
    
    LAKERA_API_URL = "https://api.lakera.ai/v1/prompt_injection"
    
    def __init__(
        self,
        name: str = "lakera",
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK,
        api_key: str = None,
        threshold: float = 0.5,
        fail_open: bool = False  # If True, allow on Lakera API error
    ):
        super().__init__(name, mode, default_on, action)
        self.api_key = api_key
        self.threshold = threshold
        self.fail_open = fail_open
    
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Dict[str, Any],
        cache: Any,
        data: Dict[str, Any],
        call_type: str
    ) -> Optional[Dict[str, Any]]:
        """Check for prompt injection via Lakera API"""
        
        # Extract text to check
        messages = data.get('messages', [])
        text_to_check = self._extract_text(messages)
        
        if not text_to_check:
            return None
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.LAKERA_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={"input": text_to_check}
                ) as response:
                    if response.status != 200:
                        raise Exception(f"Lakera API error: {response.status}")
                    
                    result = await response.json()
                    
                    # Check prompt injection score
                    injection_score = result.get('results', [{}])[0].get('categories', {}).get('prompt_injection', 0)
                    
                    if injection_score >= self.threshold:
                        if self.action == GuardrailAction.BLOCK:
                            raise GuardrailViolationError(
                                guardrail_name=self.name,
                                message=f"Prompt injection detected (score: {injection_score:.2f})",
                                violation_type="prompt_injection",
                                status_code=400
                            )
                        # LOG mode - violation recorded but allowed
                    
        except GuardrailViolationError:
            raise
        except Exception as e:
            logger.error(f"Lakera API error: {e}")
            if not self.fail_open:
                raise GuardrailViolationError(
                    guardrail_name=self.name,
                    message="Guardrail check failed",
                    violation_type="guardrail_error",
                    status_code=503
                )
        
        return None
    
    def _extract_text(self, messages: List[Dict]) -> str:
        """Extract text content from messages for checking"""
        texts = []
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                texts.append(content)
        return "\n".join(texts)
```

### 7.3 Guardrail Middleware Integration

```python
class GuardrailMiddleware(BaseHTTPMiddleware):
    """Middleware executing guardrails at appropriate lifecycle points"""
    
    def __init__(
        self,
        app,
        registry: GuardrailRegistry,
        cache_backend: Any
    ):
        super().__init__(app)
        self.registry = registry
        self.cache = cache_backend
    
    async def dispatch(self, request: Request, call_next):
        """Execute guardrails during request lifecycle"""
        
        # Get request data
        request_data = await self._get_request_data(request)
        
        # Get auth context (set by auth middleware)
        user_api_key_dict = request.state.auth_context
        
        # Determine call type from path
        call_type = self._get_call_type(request.url.path)
        
        # Get applicable guardrails for this key
        guardrails = self.registry.get_for_key(user_api_key_dict)
        
        # === PRE-CALL GUARDRAILS ===
        pre_call_guardrails = [
            g for g in guardrails 
            if g.mode == GuardrailMode.PRE_CALL
        ]
        
        modified_data = request_data
        for guardrail in pre_call_guardrails:
            try:
                result = await guardrail.async_pre_call_hook(
                    user_api_key_dict,
                    self.cache,
                    modified_data,
                    call_type
                )
                if result is not None:
                    modified_data = result
            except GuardrailViolationError as e:
                return self._build_violation_response(e)
        
        # Update request with modified data
        if modified_data != request_data:
            request = self._update_request_data(request, modified_data)
        
        # === PROCEED TO NEXT MIDDLEWARE (cache, routing, provider) ===
        response = await call_next(request)
        
        # === POST-CALL GUARDRAILS ===
        if response.status_code == 200:
            post_call_guardrails = [
                g for g in guardrails 
                if g.mode == GuardrailMode.POST_CALL
            ]
            
            response_data = json.loads(response.body)
            
            for guardrail in post_call_guardrails:
                try:
                    await guardrail.async_post_call_success_hook(
                        request_data,
                        user_api_key_dict,
                        response_data
                    )
                except GuardrailViolationError as e:
                    return self._build_violation_response(e)
            
            # Re-build response (may have been modified)
            response = Response(
                content=json.dumps(response_data),
                status_code=response.status_code,
                headers=dict(response.headers)
            )
        
        return response
    
    def _build_violation_response(self, error: GuardrailViolationError) -> Response:
        """Build HTTP response for guardrail violation"""
        return Response(
            content=json.dumps({
                "error": {
                    "message": error.message,
                    "type": "guardrail_violation",
                    "param": error.violation_type,
                    "code": "content_policy_violation",
                    "guardrail": error.guardrail_name
                }
            }),
            status_code=error.status_code,
            media_type="application/json"
        )
```

---

## 8. Integration with Phase 1/2

### 8.1 Request Lifecycle with Cache & Guardrails

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         REQUEST LIFECYCLE                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. Auth/Key Validation (Phase 1)                                       │
│     └── Sets request.state.auth_context                                 │
│                                                                         │
│  2. Rate Limit Check (Phase 1)                                          │
│                                                                         │
│  3. PRE-CALL GUARDRAILS (Phase 3)  ◄── NEW                              │
│     ├── Presidio: PII detection/anonymization                           │
│     ├── Lakera: Prompt injection detection                              │
│     └── Can modify request or raise GuardrailViolationError             │
│                                                                         │
│  4. CACHE LOOKUP (Phase 3)  ◄── NEW                                     │
│     ├── Build cache key from request fields                             │
│     ├── Check backend (Redis/Memory)                                    │
│     │   ├── HIT ──► Return cached response (skip 5-7)                   │
│     │   └── MISS ──► Continue                                           │
│     └── Streaming: Reconstruct SSE from cached response                 │
│                                                                         │
│  5. Router: Select Deployment (Phase 2)                                 │
│     └── Uses routing strategy, health checks, cooldowns                 │
│                                                                         │
│  6. Provider Call with Failover (Phase 2)                               │
│     └── Retries, fallback chains, timeout handling                      │
│                                                                         │
│  7. POST-CALL GUARDRAILS (Phase 3)  ◄── NEW                             │
│     ├── Presidio: PII detection in output                               │
│     └── Output content filtering                                        │
│                                                                         │
│  8. CACHE WRITE (Phase 3)  ◄── NEW                                      │
│     └── Store response if not no-store                                  │
│                                                                         │
│  9. Usage/Spend Logging (Phase 1)                                       │
│     └── Async write to DB                                               │
│                                                                         │
│  10. Client Response                                                    │
│      └── Headers: x-litellm-cache-hit, x-litellm-call-id                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Integration Points

| Phase | Component | Integration | Data Flow |
|-------|-----------|-------------|-----------|
| 1 | AuthMiddleware | GuardrailMiddleware reads `auth_context` | User/key info for guardrails |
| 1 | Request handler | CacheMiddleware wraps endpoint | Cache lookup/write around handler |
| 2 | Router | Cache hit skips routing | Returns cached, no deployment needed |
| 2 | FailoverManager | Post-call guardrails run after success | Output validation before caching |
| 3 | CacheBackend | Health checks (optional) | Cache health in `/health` endpoint |
| 3 | GuardrailRegistry | Key creation adds guardrail list | Per-key guardrail assignment |

### 8.3 Middleware Ordering

```python
# FastAPI middleware stack (order matters)
app = FastAPI()

# Outer layer - first to receive request, last to send response
app.add_middleware(LoggingMiddleware)
app.add_middleware(MetricsMiddleware)

# Auth and limits
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)

# Guardrails (before cache - can modify request)
app.add_middleware(GuardrailMiddleware, registry=guardrail_registry)

# Cache (after guardrails - modified request is what's cached)
app.add_middleware(CacheMiddleware, backend=cache_backend)

# Core handler
app.include_router(api_router)
```

---

## 9. Configuration Schema

### 9.1 YAML Configuration

```yaml
# Cache configuration
litellm_settings:
  # Enable caching
  cache: true
  
  cache_params:
    # Backend type: local, redis, redis-cluster, redis-sentinel, s3, disk
    type: "redis"
    
    # For Redis single instance
    host: "localhost"
    port: 6379
    password: "os.environ/REDIS_PASSWORD"
    
    # For Redis Cluster (P1)
    # redis_cluster_nodes:
    #   - "redis-node-1:6379"
    #   - "redis-node-2:6379"
    #   - "redis-node-3:6379"
    
    # For Redis Sentinel (P1)
    # redis_sentinel_nodes:
    #   - "sentinel-1:26379"
    #   - "sentinel-2:26379"
    # redis_sentinel_master_name: "mymaster"
    
    # SSL/TLS
    redis_ssl: false
    
    # Default TTL (seconds)
    ttl: 3600
    
    # For S3 backend (P1)
    # s3_bucket: "my-cache-bucket"
    # s3_region: "us-east-1"
    # s3_prefix: "deltallm-cache/"
    
    # For semantic cache (P2)
    # similarity_threshold: 0.8
    # qdrant_api_base: "http://localhost:6333"
  
  # Guardrail configurations
  guardrails:
    # Presidio PII detection
    - guardrail_name: "presidio-pii"
      litellm_params:
        guardrail: "deltallm.guardrails.presidio.PresidioGuardrail"
        mode: "pre_call"
        default_on: true
        default_action: "block"
        # Custom params passed to guardrail constructor
        anonymize: true
        threshold: 0.5
        entities:
          - PERSON
          - EMAIL_ADDRESS
          - PHONE_NUMBER
          - CREDIT_CARD
    
    # Lakera prompt injection
    - guardrail_name: "lakera-prompt-injection"
      litellm_params:
        guardrail: "deltallm.guardrails.lakera.LakeraGuardrail"
        mode: "pre_call"
        default_on: true
        default_action: "block"
        api_key: "os.environ/LAKERA_API_KEY"
        threshold: 0.5
        fail_open: false
    
    # Output content filter (example custom)
    - guardrail_name: "output-filter"
      litellm_params:
        guardrail: "myapp.guardrails.ContentFilter"
        mode: "post_call"
        default_on: false  # Must be explicitly assigned to keys
        default_action: "block"

# General settings
general_settings:
  # Feature flags
  enable_cache: true
  enable_guardrails: true
  
  # Cache key configuration
  caching_groups:
    # Custom field composition for specific models
    "gpt-4":
      - model
      - messages
      - temperature
      - max_tokens
```

### 9.2 Configuration Models

```python
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal

class CacheParams(BaseModel):
    """Cache backend parameters"""
    type: Literal[
        "local", "redis", "redis-cluster", 
        "redis-sentinel", "s3", "disk", 
        "qdrant-semantic"
    ] = "redis"
    
    # Common
    ttl: int = Field(default=3600, ge=0)
    
    # Redis
    host: Optional[str] = None
    port: int = 6379
    password: Optional[str] = None
    redis_url: Optional[str] = None
    redis_ssl: bool = False
    
    # Redis Cluster/Sentinel
    redis_cluster_nodes: Optional[List[str]] = None
    redis_sentinel_nodes: Optional[List[str]] = None
    redis_sentinel_master_name: Optional[str] = None
    
    # S3
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_prefix: str = "deltallm-cache/"
    
    # Semantic cache
    similarity_threshold: float = Field(default=0.8, ge=0, le=1)
    qdrant_api_base: Optional[str] = None
    
    @validator('type')
    def validate_type(cls, v, values):
        if v in ('redis-cluster', 'redis-sentinel') and not (
            values.get('redis_cluster_nodes') or 
            values.get('redis_sentinel_nodes')
        ):
            raise ValueError(f"{v} requires node configuration")
        return v

class GuardrailConfig(BaseModel):
    """Guardrail configuration"""
    guardrail_name: str
    litellm_params: Dict[str, Any]
    
    @validator('litellm_params')
    def validate_params(cls, v):
        if 'guardrail' not in v:
            raise ValueError("litellm_params must include 'guardrail' class path")
        if v.get('mode') not in ('pre_call', 'post_call', 'during_call'):
            raise ValueError("mode must be pre_call, post_call, or during_call")
        return v

class LiteLLMSettings(BaseModel):
    """litellm_settings section"""
    cache: bool = False
    cache_params: Optional[CacheParams] = None
    guardrails: List[GuardrailConfig] = Field(default_factory=list)
```

---

## 10. Worktree Breakdown

### 10.1 worktree-cache

**Scope:** All caching functionality - backends, key composition, middleware, streaming

**Inputs:**
- Cache configuration from config.yaml
- Request/response data from worktree-core-proxy
- Redis connection (shared with other worktrees)

**Deliverables:**
1. `src/cache/backends/base.py` - CacheBackend ABC
2. `src/cache/backends/memory.py` - InMemoryCacheBackend
3. `src/cache/backends/redis.py` - RedisCacheBackend + Cluster/Sentinel
4. `src/cache/backends/s3.py` - S3CacheBackend (P1)
5. `src/cache/key_builder.py` - CacheKeyBuilder with field selection
6. `src/cache/middleware.py` - CacheMiddleware for request/response
7. `src/cache/streaming.py` - StreamingCacheHandler for SSE
8. `src/cache/metrics.py` - CacheMetricsCollector + Prometheus integration
9. `tests/cache/test_backends.py` - Backend tests
10. `tests/cache/test_key_builder.py` - Key composition tests
11. `tests/cache/test_middleware.py` - Integration tests

**Integration Points:**
- Called by: `middleware.stack` (CacheMiddleware)
- Calls: Redis (for shared cache)
- Calls: `metrics.collector` (cache events)

**Acceptance Criteria:**
- Cache key SHA256 hash includes all deterministic fields
- Redis backend supports Cluster and Sentinel (P1)
- Streaming responses reconstruct proper SSE from cache
- `no-cache` skips read, `no-store` skips write
- Headers: `x-litellm-cache-hit: true/false`
- Graceful degradation when backend unavailable

---

### 10.2 worktree-guardrails

**Scope:** Guardrail framework, built-in integrations (Presidio, Lakera)

**Inputs:**
- Guardrail configuration from config.yaml
- Request/response data from middleware
- Auth context from worktree-core-auth
- Cache backend for stateful guardrails

**Deliverables:**
1. `src/guardrails/base.py` - CustomGuardrail ABC, GuardrailResult
2. `src/guardrails/registry.py` - GuardrailRegistry
3. `src/guardrails/loader.py` - GuardrailLoader from YAML
4. `src/guardrails/middleware.py` - GuardrailMiddleware
5. `src/guardrails/builtins/presidio.py` - PresidioGuardrail
6. `src/guardrails/builtins/lakera.py` - LakeraGuardrail
7. `src/guardrails/errors.py` - GuardrailViolationError
8. `tests/guardrails/test_framework.py` - Framework tests
9. `tests/guardrails/test_presidio.py` - Presidio integration tests
10. `tests/guardrails/test_lakera.py` - Lakera integration tests (mocked)

**Integration Points:**
- Called by: `middleware.stack` (GuardrailMiddleware)
- Calls: `cache.backend` (for state storage)
- Called by: `keys.generate` (per-key guardrail assignment)

**Acceptance Criteria:**
- Pre-call hooks can modify request data (PII anonymization)
- Post-call hooks can block responses
- Block mode returns 400 with guardrail name
- Log mode allows request and logs violation
- Presidio detects/anonymizes configured entities
- Lakera detects prompt injection above threshold
- Per-key guardrail assignment works
- Default guardrails apply when no key-specific assignment

---

## 11. Cross-Module Dependencies

```
┌─────────────────────────────────────────────────────────────────┐
│                    worktree-core-proxy                          │
│              (Phase 1 - request lifecycle)                      │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        v                         v                         v
┌───────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ worktree-cache│      │ worktree-guard-  │      │ worktree-core-   │
│ (backends,    │◄────►│ rails            │◄────►│ auth             │
│  middleware)  │      │ (framework,      │      │ (auth_context)   │
└───────┬───────┘      │  Presidio,       │      └──────────────────┘
        │              │  Lakera)         │
        v              └──────────────────┘
┌───────────────┐                     │
│ Redis Backend │                     │
│ (shared)      │                     │
└───────────────┘                     │
                                      │
                         ┌────────────┴────────────┐
                         │                         │
                         v                         v
              ┌──────────────────┐     ┌──────────────────┐
              │ Presidio Service │     │ Lakera API       │
              │ (local/analyzer) │     │ (external SaaS)  │
              └──────────────────┘     └──────────────────┘
```

---

## 12. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Cache stale data | TTL-based expiration; document no manual invalidation |
| Redis cache unavailable | Graceful degradation: disable cache, continue serving |
| Streaming cache memory pressure | Limit max accumulated chunks; discard on disconnect |
| Guardrail latency overhead | Async hooks; timeout on external APIs (Lakera) |
| External guardrail unavailability | `fail_open` configuration option |
| False positive PII detection | Configurable threshold; Log mode for monitoring |
| PII masking degrades quality | Document trade-off; allow per-key override |
| Guardrail ordering issues | Deterministic registration order (YAML order) |

---

## 13. Open Questions

1. **Cache invalidation API**: Should we expose admin endpoint to purge cache keys?
2. **Streaming chunk size**: Fixed word boundaries vs character count for reconstruction?
3. **Guardrail chaining**: Should multiple pre_call guardrails see each other's modifications?
4. **Lakera fallback**: Should we cache Lakera results to reduce API calls?
5. **Semantic cache**: Which embedding model for Qdrant similarity search?
