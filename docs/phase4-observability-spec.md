# Phase 4: Observability, Metrics & Billing Technical Specification

> **Source:** Master PRD §7.1-7.12  
> **Phase:** 4 - Observability & Billing  
> **Status:** Draft

---

## 1. Callback System Architecture

### 1.1 CustomLogger Base Class

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime

class CustomLogger(ABC):
    """
    Base class for custom logging integrations.
    
    Implementations can override sync or async methods.
    Async methods are preferred for non-blocking operation.
    """
    
    # ========================================================================
    # Synchronous Hooks (for simple integrations)
    # ========================================================================
    
    def log_pre_api_call(self, model: str, messages: list, kwargs: Dict[str, Any]) -> None:
        """Called before LLM API call is made"""
        pass
    
    def log_post_api_call(self, kwargs: Dict[str, Any], response_obj: Any, 
                          start_time: datetime, end_time: datetime) -> None:
        """Called after LLM API call completes (before response returned)"""
        pass
    
    def log_success_event(self, kwargs: Dict[str, Any], response_obj: Any,
                          start_time: datetime, end_time: datetime) -> None:
        """Called on successful LLM call"""
        pass
    
    def log_failure_event(self, kwargs: Dict[str, Any], exception: Exception,
                          start_time: datetime, end_time: datetime) -> None:
        """Called on failed LLM call"""
        pass
    
    # ========================================================================
    # Asynchronous Hooks (preferred for I/O operations)
    # ========================================================================
    
    async def async_log_success_event(self, kwargs: Dict[str, Any], response_obj: Any,
                                      start_time: datetime, end_time: datetime) -> None:
        """Async version of log_success_event"""
        # Default: call sync version in thread pool
        await asyncio.to_thread(self.log_success_event, kwargs, response_obj, start_time, end_time)
    
    async def async_log_failure_event(self, kwargs: Dict[str, Any], exception: Exception,
                                      start_time: datetime, end_time: datetime) -> None:
        """Async version of log_failure_event"""
        await asyncio.to_thread(self.log_failure_event, kwargs, exception, start_time, end_time)
    
    async def async_log_stream_event(self, kwargs: Dict[str, Any], response_obj: Any,
                                     start_time: datetime, end_time: datetime) -> None:
        """Called for each streaming chunk (if implemented)"""
        pass
    
    # ========================================================================
    # Lifecycle Hooks (for guardrail-like behavior)
    # ========================================================================
    
    async def async_pre_call_hook(self, user_api_key_dict: Dict[str, Any], 
                                  cache: Any, data: Dict[str, Any], 
                                  call_type: str) -> Optional[Dict[str, Any]]:
        """
        Called before request processing. Can modify request or block.
        
        Returns:
            Modified data dict, or raises exception to block
        """
        pass
    
    async def async_post_call_success_hook(self, data: Dict[str, Any], 
                                           user_api_key_dict: Dict[str, Any],
                                           response: Any) -> None:
        """Called after successful response, before returning to client"""
        pass
    
    async def async_post_call_failure_hook(self, request_data: Dict[str, Any],
                                           original_exception: Exception,
                                           user_api_key_dict: Dict[str, Any]) -> None:
        """Called after failed call for error handling"""
        pass
```

### 1.2 Standard Logging Payload Schema

```python
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import datetime

class TokenUsage(BaseModel):
    """Token usage statistics"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    
    # Extended for cached tokens
    prompt_tokens_cached: Optional[int] = None
    completion_tokens_cached: Optional[int] = None

class StandardLoggingPayload(BaseModel):
    """
    Standard payload passed to all callback handlers.
    
    This schema is guaranteed to be stable across versions.
    """
    # Request identification
    litellm_call_id: str           # Unique call ID (UUID)
    request_id: str                # Proxy request ID
    call_type: str                 # "completion", "embedding", etc.
    
    # Request data
    model: str                     # Requested model name (client-facing)
    deployment_model: Optional[str]  # Actual deployed model ID
    messages: Optional[List[Dict[str, Any]]]  # Input messages
    response_obj: Optional[Dict[str, Any]]   # Full response object
    
    # Usage and cost
    usage: TokenUsage
    response_cost: float           # Calculated cost in USD
    
    # Request parameters
    stream: bool
    temperature: Optional[float]
    max_tokens: Optional[int]
    top_p: Optional[float]
    
    # Attribution
    api_key: str                   # Hashed API key
    user: Optional[str]            # End-user ID
    team_id: Optional[str]
    organization_id: Optional[str]
    
    # Metadata
    metadata: Dict[str, Any]
    tags: List[str]
    
    # Cache status
    cache_hit: bool
    cache_key: Optional[str]
    
    # Timing
    start_time: datetime
    end_time: datetime
    total_latency_ms: float
    api_latency_ms: Optional[float]  # Provider API time only
    
    # Provider info
    api_provider: str              # "openai", "anthropic", etc.
    api_base: Optional[str]
    
    # Error info (for failure callbacks)
    error_info: Optional[Dict[str, Any]] = None
```

### 1.3 Callback Registration System

```python
from typing import List, Union, Type, Dict, Any
import importlib

# Global callback registry
_callback_registry: Dict[str, "CustomLogger"] = {}

class CallbackManager:
    """Manages callback registration and execution"""
    
    def __init__(self):
        self.success_callbacks: List[CustomLogger] = []
        self.failure_callbacks: List[CustomLogger] = []
        self.pre_call_hooks: List[CustomLogger] = []
        self.post_call_hooks: List[CustomLogger] = []
    
    def register_callback(
        self,
        callback: Union[str, CustomLogger, Type[CustomLogger]],
        callback_type: str = "success"  # "success", "failure", "both"
    ) -> None:
        """
        Register a callback handler.
        
        Args:
            callback: Can be:
                - String: Built-in callback name ("prometheus", "langfuse", etc.)
                - CustomLogger instance: Pre-configured handler
                - CustomLogger class: Will be instantiated
            callback_type: When to trigger the callback
        """
        handler = self._resolve_callback(callback)
        
        if callback_type in ("success", "both"):
            self.success_callbacks.append(handler)
        if callback_type in ("failure", "both"):
            self.failure_callbacks.append(handler)
        
        # Register lifecycle hooks if implemented
        if hasattr(handler, 'async_pre_call_hook'):
            self.pre_call_hooks.append(handler)
        if hasattr(handler, 'async_post_call_success_hook'):
            self.post_call_hooks.append(handler)
    
    def _resolve_callback(
        self,
        callback: Union[str, CustomLogger, Type[CustomLogger]]
    ) -> CustomLogger:
        """Resolve callback to handler instance"""
        
        if isinstance(callback, CustomLogger):
            return callback
        
        if isinstance(callback, type) and issubclass(callback, CustomLogger):
            return callback()
        
        if isinstance(callback, str):
            # Built-in callback
            return self._load_builtin_callback(callback)
        
        raise ValueError(f"Invalid callback type: {type(callback)}")
    
    def _load_builtin_callback(self, name: str) -> CustomLogger:
        """Load built-in callback by name"""
        
        builtins = {
            "prometheus": "observability.integrations.prometheus.PrometheusCallback",
            "langfuse": "observability.integrations.langfuse.LangfuseCallback",
            "otel": "observability.integrations.opentelemetry.OpenTelemetryCallback",
            "s3": "observability.integrations.s3.S3Callback",
            "gcs_bucket": "observability.integrations.gcs.GCSCallback",
            "datadog": "observability.integrations.datadog.DatadogCallback",
            "sentry": "observability.integrations.sentry.SentryCallback",
        }
        
        if name not in builtins:
            raise ValueError(f"Unknown built-in callback: {name}")
        
        module_path, class_name = builtins[name].rsplit('.', 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls()
    
    async def execute_success_callbacks(self, payload: StandardLoggingPayload) -> None:
        """Execute all success callbacks (fire-and-forget)"""
        for handler in self.success_callbacks:
            try:
                await handler.async_log_success_event(
                    kwargs=payload.model_dump(),
                    response_obj=payload.response_obj,
                    start_time=payload.start_time,
                    end_time=payload.end_time
                )
            except Exception as e:
                logger.warning(f"Callback {handler.__class__.__name__} failed: {e}")
    
    async def execute_failure_callbacks(
        self,
        payload: StandardLoggingPayload,
        exception: Exception
    ) -> None:
        """Execute all failure callbacks"""
        for handler in self.failure_callbacks:
            try:
                await handler.async_log_failure_event(
                    kwargs=payload.model_dump(),
                    exception=exception,
                    start_time=payload.start_time,
                    end_time=payload.end_time
                )
            except Exception as e:
                logger.warning(f"Callback {handler.__class__.__name__} failed: {e}")
```

### 1.4 YAML Configuration

```yaml
# Callback configuration in litellm_settings
litellm_settings:
  # Success callbacks - triggered on successful requests
  success_callback: ["prometheus", "langfuse", "s3"]
  
  # Failure callbacks - triggered on failed requests
  failure_callback: ["langfuse", "sentry"]
  
  # General callbacks - triggered on both success and failure
  callbacks: 
    - "custom_module.MyCustomLogger"
  
  # Callback-specific settings
  callback_settings:
    langfuse:
      public_key: "os.environ/LANGFUSE_PUBLIC_KEY"
      secret_key: "os.environ/LANGFUSE_SECRET_KEY"
      host: "https://cloud.langfuse.com"
    
    s3:
      bucket: "my-litellm-logs"
      region: "us-east-1"
      prefix: "logs/"
    
    datadog:
      api_key: "os.environ/DATADOG_API_KEY"
      site: "datadoghq.com"
  
  # Disable message content logging (PII protection)
  turn_off_message_logging: false
```

---

## 2. Prometheus Metrics

### 2.1 Metrics Registry

```python
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest
from typing import Dict, List, Optional

class PrometheusMetrics:
    """Prometheus metrics exporter for LiteLLM"""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        self._init_metrics()
    
    def _init_metrics(self):
        """Initialize all Prometheus metrics"""
        
        # ========================================================================
        # Request Metrics
        # ========================================================================
        
        self.requests_total = Counter(
            'litellm_requests_total',
            'Total LLM API requests',
            ['model', 'api_provider', 'api_key', 'user', 'team', 'status_code'],
            registry=self.registry
        )
        
        self.request_total_latency = Histogram(
            'litellm_request_total_latency_seconds',
            'End-to-end request latency',
            ['model', 'api_provider', 'status_code'],
            buckets=[.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0],
            registry=self.registry
        )
        
        self.llm_api_latency = Histogram(
            'litellm_llm_api_latency_seconds',
            'Provider API latency only',
            ['model', 'api_provider'],
            buckets=[.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0],
            registry=self.registry
        )
        
        self.request_failures = Counter(
            'litellm_request_failures_total',
            'Total failed requests',
            ['model', 'api_provider', 'error_type'],
            registry=self.registry
        )
        
        # ========================================================================
        # Token Metrics
        # ========================================================================
        
        self.input_tokens = Counter(
            'litellm_input_tokens_total',
            'Total input tokens',
            ['model', 'api_provider', 'api_key', 'user', 'team'],
            registry=self.registry
        )
        
        self.output_tokens = Counter(
            'litellm_output_tokens_total',
            'Total output tokens',
            ['model', 'api_provider', 'api_key', 'user', 'team'],
            registry=self.registry
        )
        
        self.cached_input_tokens = Counter(
            'litellm_cached_input_tokens_total',
            'Total cached input tokens',
            ['model', 'api_provider'],
            registry=self.registry
        )
        
        # ========================================================================
        # Cost Metrics
        # ========================================================================
        
        self.spend_total = Counter(
            'litellm_spend_total',
            'Total spend in USD',
            ['model', 'api_provider', 'api_key', 'user', 'team'],
            registry=self.registry
        )
        
        # ========================================================================
        # Cache Metrics
        # ========================================================================
        
        self.cache_hits = Counter(
            'litellm_cache_hit_total',
            'Total cache hits',
            ['model', 'cache_type'],
            registry=self.registry
        )
        
        self.cache_misses = Counter(
            'litellm_cache_miss_total',
            'Total cache misses',
            ['model', 'cache_type'],
            registry=self.registry
        )
        
        # ========================================================================
        # Deployment Health Metrics
        # ========================================================================
        
        self.deployment_state = Gauge(
            'litellm_deployment_state',
            'Deployment health state (0=healthy, 1=partial, 2=degraded)',
            ['deployment_id', 'model'],
            registry=self.registry
        )
        
        self.deployment_latency_per_token = Gauge(
            'litellm_deployment_latency_per_output_token_ms',
            'Average latency per output token',
            ['deployment_id', 'model'],
            registry=self.registry
        )
        
        self.deployment_active_requests = Gauge(
            'litellm_deployment_active_requests',
            'Current in-flight requests per deployment',
            ['deployment_id', 'model'],
            registry=self.registry
        )
        
        self.deployment_cooldown = Gauge(
            'litellm_deployment_cooldown',
            'Whether deployment is in cooldown (0/1)',
            ['deployment_id', 'model'],
            registry=self.registry
        )
        
        # ========================================================================
        # Budget Metrics
        # ========================================================================
        
        self.remaining_key_budget = Gauge(
            'litellm_remaining_api_key_budget',
            'Remaining budget per API key',
            ['api_key', 'key_alias'],
            registry=self.registry
        )
        
        self.remaining_team_budget = Gauge(
            'litellm_remaining_team_budget',
            'Remaining budget per team',
            ['team_id'],
            registry=self.registry
        )
        
        self.remaining_user_budget = Gauge(
            'litellm_remaining_user_budget',
            'Remaining budget per user',
            ['user_id'],
            registry=self.registry
        )
```

### 2.2 Prometheus Callback Handler

```python
from prometheus_client import CONTENT_TYPE_LATEST
from fastapi import Response

class PrometheusCallback(CustomLogger):
    """
    Prometheus metrics callback handler.
    
    Emits metrics on success/failure events.
    Provides /metrics endpoint for scraping.
    """
    
    _instance: Optional["PrometheusCallback"] = None
    _metrics: Optional[PrometheusMetrics] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._metrics = PrometheusMetrics()
        return cls._instance
    
    @classmethod
    def get_metrics(cls) -> PrometheusMetrics:
        """Get the singleton metrics instance"""
        if cls._metrics is None:
            cls()
        return cls._metrics
    
    @classmethod
    def get_metrics_endpoint(cls) -> Response:
        """Generate metrics endpoint response"""
        metrics = cls.get_metrics()
        return Response(
            content=generate_latest(metrics.registry),
            media_type=CONTENT_TYPE_LATEST
        )
    
    async def async_log_success_event(self, kwargs: Dict[str, Any], 
                                      response_obj: Any,
                                      start_time: datetime, 
                                      end_time: datetime) -> None:
        """Emit Prometheus metrics on success"""
        
        m = self._metrics
        
        # Extract labels
        model = kwargs.get('model', 'unknown')
        api_provider = kwargs.get('api_provider', 'unknown')
        api_key = self._hash_key(kwargs.get('api_key', ''))
        user = kwargs.get('user', '') or 'anonymous'
        team = kwargs.get('team_id', '') or 'default'
        status_code = '200'
        
        # Request metrics
        m.requests_total.labels(
            model=model,
            api_provider=api_provider,
            api_key=api_key,
            user=user,
            team=team,
            status_code=status_code
        ).inc()
        
        # Latency metrics
        total_latency = (end_time - start_time).total_seconds()
        m.request_total_latency.labels(
            model=model,
            api_provider=api_provider,
            status_code=status_code
        ).observe(total_latency)
        
        # API latency (if available)
        api_latency_ms = kwargs.get('api_latency_ms')
        if api_latency_ms:
            m.llm_api_latency.labels(
                model=model,
                api_provider=api_provider
            ).observe(api_latency_ms / 1000)
        
        # Token metrics
        usage = kwargs.get('usage', {})
        prompt_tokens = usage.get('prompt_tokens', 0)
        completion_tokens = usage.get('completion_tokens', 0)
        
        m.input_tokens.labels(
            model=model, api_provider=api_provider,
            api_key=api_key, user=user, team=team
        ).inc(prompt_tokens)
        
        m.output_tokens.labels(
            model=model, api_provider=api_provider,
            api_key=api_key, user=user, team=team
        ).inc(completion_tokens)
        
        # Cached tokens
        cached_tokens = usage.get('prompt_tokens_cached', 0)
        if cached_tokens:
            m.cached_input_tokens.labels(
                model=model, api_provider=api_provider
            ).inc(cached_tokens)
        
        # Spend metric
        response_cost = kwargs.get('response_cost', 0.0)
        m.spend_total.labels(
            model=model, api_provider=api_provider,
            api_key=api_key, user=user, team=team
        ).inc(response_cost)
        
        # Cache metrics
        cache_hit = kwargs.get('cache_hit', False)
        if cache_hit:
            m.cache_hits.labels(model=model, cache_type='redis').inc()
        else:
            m.cache_misses.labels(model=model, cache_type='redis').inc()
    
    async def async_log_failure_event(self, kwargs: Dict[str, Any],
                                      exception: Exception,
                                      start_time: datetime,
                                      end_time: datetime) -> None:
        """Emit Prometheus metrics on failure"""
        
        m = self._metrics
        
        model = kwargs.get('model', 'unknown')
        api_provider = kwargs.get('api_provider', 'unknown')
        error_type = exception.__class__.__name__
        
        m.request_failures.labels(
            model=model,
            api_provider=api_provider,
            error_type=error_type
        ).inc()
    
    def _hash_key(self, key: str) -> str:
        """Hash API key for label safety"""
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### 2.3 Cardinality Controls

```python
from dataclasses import dataclass

@dataclass
class PrometheusLabelSettings:
    """Configuration for high-cardinality label control"""
    
    # Disable high-cardinality labels to prevent metric explosion
    disable_end_user_label: bool = False      # 'user' label
    disable_api_base_label: bool = True       # 'api_base' label (often unique)
    disable_team_label: bool = False
    disable_api_key_label: bool = False
    
    # Label value truncation
    max_label_value_length: int = 128
    
    # Label value sanitization
    sanitize_labels: bool = True

class CardinalityControlledMetrics:
    """Wrapper that applies cardinality controls"""
    
    def __init__(self, metrics: PrometheusMetrics, settings: PrometheusLabelSettings):
        self.metrics = metrics
        self.settings = settings
    
    def _sanitize_label(self, value: str) -> str:
        """Sanitize label value to prevent cardinality explosion"""
        if not value:
            return "unknown"
        
        # Truncate
        value = value[:self.settings.max_label_value_length]
        
        # Sanitize (remove newlines, control chars)
        if self.settings.sanitize_labels:
            value = value.replace('\n', ' ').replace('\r', '')
        
        return value
```

---

## 3. Built-in Integrations

### 3.1 Langfuse Integration

```python
import os
from typing import Optional

try:
    from langfuse import Langfuse
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False

class LangfuseCallback(CustomLogger):
    """Langfuse tracing integration for prompt management and cost tracking"""
    
    def __init__(
        self,
        public_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        host: str = "https://cloud.langfuse.com",
        release: Optional[str] = None
    ):
        if not LANGFUSE_AVAILABLE:
            raise ImportError("langfuse package required. Install: pip install langfuse")
        
        self.public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
        self.host = host
        self.release = release
        
        self._client: Optional[Langfuse] = None
    
    @property
    def client(self) -> Langfuse:
        """Lazy initialization of Langfuse client"""
        if self._client is None:
            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
                release=self.release
            )
        return self._client
    
    async def async_log_success_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime
    ) -> None:
        """Log successful request to Langfuse"""
        
        # Extract trace ID from metadata
        metadata = kwargs.get('metadata', {})
        trace_id = metadata.get('trace_id') or metadata.get('langfuse_trace_id')
        
        # Get or create trace
        trace = self.client.trace(
            id=trace_id,
            name=metadata.get('generation_name', kwargs.get('call_type', 'completion')),
            user_id=kwargs.get('user'),
            metadata={
                'model': kwargs.get('model'),
                'api_key': kwargs.get('api_key'),
                'team_id': kwargs.get('team_id'),
            }
        )
        
        # Create generation (the LLM call)
        generation = trace.generation(
            name=metadata.get('generation_name', 'completion'),
            model=kwargs.get('model'),
            input=kwargs.get('messages'),
            output=response_obj.get('choices', [{}])[0].get('message'),
            start_time=start_time,
            end_time=end_time,
            usage={
                'input': kwargs.get('usage', {}).get('prompt_tokens', 0),
                'output': kwargs.get('usage', {}).get('completion_tokens', 0),
                'total': kwargs.get('usage', {}).get('total_tokens', 0),
                'unit': 'TOKENS',
                'input_cost': kwargs.get('response_cost', 0),
                'output_cost': 0,  # Combined in total
                'total_cost': kwargs.get('response_cost', 0)
            },
            metadata={
                'cache_hit': kwargs.get('cache_hit', False),
                'api_provider': kwargs.get('api_provider'),
                'stream': kwargs.get('stream', False),
            }
        )
        
        # Flush to ensure delivery
        self.client.flush()
    
    async def async_log_failure_event(
        self,
        kwargs: Dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime
    ) -> None:
        """Log failed request to Langfuse"""
        
        metadata = kwargs.get('metadata', {})
        trace_id = metadata.get('trace_id')
        
        trace = self.client.trace(
            id=trace_id,
            name=metadata.get('generation_name', 'completion'),
            user_id=kwargs.get('user')
        )
        
        trace.generation(
            name='completion',
            model=kwargs.get('model'),
            input=kwargs.get('messages'),
            start_time=start_time,
            end_time=end_time,
            level='ERROR',
            status_message=str(exception),
            metadata={'error_type': exception.__class__.__name__}
        )
        
        self.client.flush()
```

### 3.2 OpenTelemetry Integration

```python
from typing import Dict, Any, Optional

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

class OpenTelemetryCallback(CustomLogger):
    """
    OpenTelemetry tracing integration.
    
    Exports traces via OTLP HTTP/gRPC.
    Supports W3C trace context propagation.
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        service_name: str = "deltallm"
    ):
        if not OPENTELEMETRY_AVAILABLE:
            raise ImportError("opentelemetry packages required")
        
        self.endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        self.headers = headers
        self.service_name = service_name
        
        self._tracer: Optional[trace.Tracer] = None
        self._propagator = TraceContextTextMapPropagator()
    
    @property
    def tracer(self) -> trace.Tracer:
        """Lazy initialization of tracer"""
        if self._tracer is None:
            provider = TracerProvider()
            
            if self.endpoint:
                exporter = OTLPSpanExporter(
                    endpoint=self.endpoint,
                    headers=self.headers
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
            
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self.service_name)
        
        return self._tracer
    
    async def async_log_success_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime
    ) -> None:
        """Create OpenTelemetry span for successful request"""
        
        # Extract parent context from metadata if present
        metadata = kwargs.get('metadata', {})
        parent_context = None
        if 'traceparent' in metadata:
            carrier = {'traceparent': metadata['traceparent']}
            parent_context = self._propagator.extract(carrier=carrier)
        
        with self.tracer.start_as_current_span(
            name=f"llm.{kwargs.get('call_type', 'completion')}",
            context=parent_context,
            start_time=start_time
        ) as span:
            # Set span attributes
            span.set_attribute("llm.model", kwargs.get('model'))
            span.set_attribute("llm.provider", kwargs.get('api_provider'))
            span.set_attribute("llm.request.type", kwargs.get('call_type'))
            
            # Token usage
            usage = kwargs.get('usage', {})
            span.set_attribute("llm.usage.prompt_tokens", usage.get('prompt_tokens', 0))
            span.set_attribute("llm.usage.completion_tokens", usage.get('completion_tokens', 0))
            span.set_attribute("llm.usage.total_tokens", usage.get('total_tokens', 0))
            
            # Cost
            span.set_attribute("llm.cost", kwargs.get('response_cost', 0.0))
            
            # Attribution
            span.set_attribute("llm.user.id", kwargs.get('user', 'unknown'))
            span.set_attribute("llm.team.id", kwargs.get('team_id', 'unknown'))
            
            # Cache status
            span.set_attribute("llm.cache.hit", kwargs.get('cache_hit', False))
            
            # Set status
            span.set_status(trace.Status(trace.StatusCode.OK))
            
            # End time
            span.end(end_time=end_time)
```

### 3.3 S3 Logging Integration

```python
import json
import boto3
from datetime import datetime
from typing import Optional
from botocore.exceptions import ClientError

class S3Callback(CustomLogger):
    """S3 bucket logging integration for request/response storage"""
    
    def __init__(
        self,
        bucket: Optional[str] = None,
        region: str = "us-east-1",
        prefix: str = "litellm-logs/",
        compression: Optional[str] = None  # "gzip" or None
    ):
        self.bucket = bucket or os.environ.get("LITELLM_S3_BUCKET")
        self.region = region
        self.prefix = prefix
        self.compression = compression
        
        self._s3: Optional[boto3.client] = None
    
    @property
    def s3(self) -> boto3.client:
        """Lazy initialization of S3 client"""
        if self._s3 is None:
            self._s3 = boto3.client('s3', region_name=self.region)
        return self._s3
    
    def _generate_key(self, kwargs: Dict[str, Any]) -> str:
        """Generate S3 object key from request metadata"""
        
        now = datetime.utcnow()
        request_id = kwargs.get('litellm_call_id', 'unknown')
        
        # Partition by date for efficient querying
        key = (
            f"{self.prefix}"
            f"year={now.year}/"
            f"month={now.month:02d}/"
            f"day={now.day:02d}/"
            f"{request_id}.json"
        )
        
        if self.compression == "gzip":
            key += ".gz"
        
        return key
    
    async def async_log_success_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime
    ) -> None:
        """Upload request log to S3"""
        
        # Build log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": kwargs.get('litellm_call_id'),
            "call_type": kwargs.get('call_type'),
            "model": kwargs.get('model'),
            "api_provider": kwargs.get('api_provider'),
            "user": kwargs.get('user'),
            "team_id": kwargs.get('team_id'),
            "api_key": kwargs.get('api_key'),
            "usage": kwargs.get('usage'),
            "response_cost": kwargs.get('response_cost'),
            "cache_hit": kwargs.get('cache_hit'),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "latency_ms": (end_time - start_time).total_seconds() * 1000,
            "metadata": kwargs.get('metadata'),
            "tags": kwargs.get('tags', []),
        }
        
        # Conditionally include messages/responses
        if not kwargs.get('redacted', False):
            log_entry["messages"] = kwargs.get('messages')
            log_entry["response"] = response_obj
        else:
            log_entry["redacted"] = True
        
        # Serialize
        data = json.dumps(log_entry, default=str)
        
        # Compress if configured
        if self.compression == "gzip":
            import gzip
            body = gzip.compress(data.encode('utf-8'))
        else:
            body = data.encode('utf-8')
        
        # Upload
        key = self._generate_key(kwargs)
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType='application/json',
                ContentEncoding='gzip' if self.compression == "gzip" else None,
                Metadata={
                    'model': kwargs.get('model', 'unknown'),
                    'user': kwargs.get('user', 'unknown'),
                    'team': kwargs.get('team_id', 'unknown')
                }
            )
        except ClientError as e:
            logger.warning(f"S3 upload failed: {e}")
```

---

## 4. Cost Calculation

### 4.1 Model Cost Map

```python
from typing import Dict, Optional
from dataclasses import dataclass

@dataclass
class ModelPricing:
    """Pricing configuration for a model"""
    
    # Per-token costs (in USD)
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    
    # Cached token pricing (often discounted)
    input_cost_per_token_cache_hit: Optional[float] = None
    output_cost_per_token_cache_hit: Optional[float] = None
    
    # Per-request costs
    cost_per_request: float = 0.0
    
    # Model capabilities
    context_window: int = 8192
    max_output_tokens: Optional[int] = None

# Default model cost map
DEFAULT_MODEL_COST_MAP: Dict[str, ModelPricing] = {
    # OpenAI models
    "gpt-4": ModelPricing(
        input_cost_per_token=0.00003,
        output_cost_per_token=0.00006,
        context_window=8192
    ),
    "gpt-4-32k": ModelPricing(
        input_cost_per_token=0.00006,
        output_cost_per_token=0.00012,
        context_window=32768
    ),
    "gpt-4-turbo": ModelPricing(
        input_cost_per_token=0.00001,
        output_cost_per_token=0.00003,
        input_cost_per_token_cache_hit=0.000005,
        context_window=128000
    ),
    "gpt-4o": ModelPricing(
        input_cost_per_token=0.000005,
        output_cost_per_token=0.000015,
        input_cost_per_token_cache_hit=0.0000025,
        context_window=128000
    ),
    "gpt-3.5-turbo": ModelPricing(
        input_cost_per_token=0.0000005,
        output_cost_per_token=0.0000015,
        context_window=16385
    ),
    
    # Anthropic models
    "claude-3-opus": ModelPricing(
        input_cost_per_token=0.000015,
        output_cost_per_token=0.000075,
        context_window=200000
    ),
    "claude-3-sonnet": ModelPricing(
        input_cost_per_token=0.000003,
        output_cost_per_token=0.000015,
        context_window=200000
    ),
    "claude-3-haiku": ModelPricing(
        input_cost_per_token=0.00000025,
        output_cost_per_token=0.00000125,
        context_window=200000
    ),
}

class CostMapManager:
    """Manages model pricing with override support"""
    
    def __init__(self):
        self._cost_map: Dict[str, ModelPricing] = dict(DEFAULT_MODEL_COST_MAP)
        self._custom_models: Dict[str, ModelPricing] = {}
    
    def register_model(
        self,
        model: str,
        pricing: ModelPricing,
        is_custom: bool = True
    ) -> None:
        """Register or override model pricing"""
        self._cost_map[model] = pricing
        if is_custom:
            self._custom_models[model] = pricing
    
    def get_pricing(self, model: str) -> Optional[ModelPricing]:
        """Get pricing for a model"""
        # Try exact match
        if model in self._cost_map:
            return self._cost_map[model]
        
        # Try prefix matching (e.g., "gpt-4-" matches "gpt-4")
        for prefix in sorted(self._cost_map.keys(), key=len, reverse=True):
            if model.startswith(prefix):
                return self._cost_map[prefix]
        
        return None
    
    def load_from_config(self, model_list: List[Dict[str, Any]]) -> None:
        """Load custom pricing from model_list config"""
        for entry in model_list:
            model_name = entry.get('model_name')
            model_info = entry.get('model_info', {})
            
            if 'input_cost_per_token' in model_info or 'output_cost_per_token' in model_info:
                pricing = ModelPricing(
                    input_cost_per_token=model_info.get('input_cost_per_token', 0),
                    output_cost_per_token=model_info.get('output_cost_per_token', 0),
                    input_cost_per_token_cache_hit=model_info.get('input_cost_per_token_cache_hit'),
                    output_cost_per_token_cache_hit=model_info.get('output_cost_per_token_cache_hit')
                )
                self.register_model(model_name, pricing)
```

### 4.2 Completion Cost Function

```python
from typing import Dict, Any, Optional

def completion_cost(
    model: str,
    usage: Dict[str, int],
    cost_map: CostMapManager,
    cache_hit: bool = False,
    custom_pricing: Optional[ModelPricing] = None
) -> float:
    """
    Calculate the cost of a completion request.
    
    Args:
        model: The model name
        usage: Token usage dict with prompt_tokens, completion_tokens
        cost_map: Cost map manager
        cache_hit: Whether this was a cache hit
        custom_pricing: Optional override pricing
        
    Returns:
        Total cost in USD
    """
    # Get pricing
    pricing = custom_pricing or cost_map.get_pricing(model)
    if pricing is None:
        logger.warning(f"No pricing found for model: {model}")
        return 0.0
    
    # Extract token counts
    prompt_tokens = usage.get('prompt_tokens', 0)
    completion_tokens = usage.get('completion_tokens', 0)
    
    # Handle cached tokens
    cached_tokens = usage.get('prompt_tokens_cached', 0)
    uncached_prompt_tokens = prompt_tokens - cached_tokens
    
    # Calculate costs
    if cache_hit and pricing.input_cost_per_token_cache_hit is not None:
        # Use discounted cache pricing
        prompt_cost = (
            cached_tokens * pricing.input_cost_per_token_cache_hit +
            uncached_prompt_tokens * pricing.input_cost_per_token
        )
    else:
        prompt_cost = prompt_tokens * pricing.input_cost_per_token
    
    if cache_hit and pricing.output_cost_per_token_cache_hit is not None:
        completion_cost = completion_tokens * pricing.output_cost_per_token_cache_hit
    else:
        completion_cost = completion_tokens * pricing.output_cost_per_token
    
    total_cost = prompt_cost + completion_cost + pricing.cost_per_request
    
    return round(total_cost, 10)
```

### 4.3 Cost Calculation Service

```python
class CostCalculationService:
    """Service for calculating and tracking costs"""
    
    def __init__(self, cost_map: CostMapManager):
        self.cost_map = cost_map
    
    def calculate_request_cost(
        self,
        model: str,
        usage: Dict[str, int],
        deployment_config: Optional[Dict[str, Any]] = None,
        cache_hit: bool = False
    ) -> float:
        """
        Calculate cost for a request.
        
        Checks for custom pricing in deployment config first.
        """
        # Check for deployment-specific pricing
        custom_pricing = None
        if deployment_config:
            model_info = deployment_config.get('model_info', {})
            if 'input_cost_per_token' in model_info:
                custom_pricing = ModelPricing(
                    input_cost_per_token=model_info['input_cost_per_token'],
                    output_cost_per_token=model_info['output_cost_per_token'],
                    input_cost_per_token_cache_hit=model_info.get('input_cost_per_token_cache_hit')
                )
        
        return completion_cost(
            model=model,
            usage=usage,
            cost_map=self.cost_map,
            cache_hit=cache_hit,
            custom_pricing=custom_pricing
        )
    
    async def update_cumulative_spend(
        self,
        entity_type: str,
        entity_id: str,
        cost: float
    ) -> None:
        """
        Update cumulative spend for an entity.
        
        Args:
            entity_type: "key", "user", "team", "org"
            entity_id: Entity identifier
            cost: Cost to add
        """
        # This will be implemented in the spend tracking service
        pass
```

---

## 5. Spend Tracking

### 5.1 Database Schema

```prisma
// Spend Logs - append-only time-series data
model LiteLLM_SpendLogs {
  id                String   @id @default(uuid())
  request_id        String
  call_type         String   // "completion", "embedding", etc.
  api_key           String   // hashed key
  spend             Float
  total_tokens      Int
  prompt_tokens     Int
  completion_tokens Int
  start_time        DateTime
  end_time          DateTime
  model             String
  api_base          String?
  user              String?  // end-user ID from request
  team_id           String?
  end_user          String?
  metadata          Json?
  cache_hit         Boolean  @default(false)
  cache_key         String?
  request_tags      String[]
  
  // Relations
  key               LiteLLM_VerificationToken? @relation(fields: [api_key], references: [token])
  
  @@index([api_key])
  @@index([team_id])
  @@index([user])
  @@index([start_time])
  @@index([model])
  @@index([request_tags])
  @@map("litellm_spendlogs")
}

// API Keys with cumulative spend
model LiteLLM_VerificationToken {
  id                    String   @id @default(uuid())
  token                 String   @unique  // SHA-256 hash
  key_name              String?
  user_id               String?
  team_id               String?
  models                String[]
  max_budget            Float?
  soft_budget           Float?   // Alert threshold
  spend                 Float    @default(0)
  budget_duration       String?  // "1h", "1d", "30d"
  budget_reset_at       DateTime?
  rpm_limit             Int?
  tpm_limit             Int?
  max_parallel_requests Int?
  expires               DateTime?
  permissions           Json?
  metadata              Json?
  created_at            DateTime @default(now())
  updated_at            DateTime @updatedAt
  
  // Relations
  user                  LiteLLM_UserTable? @relation(fields: [user_id], references: [user_id])
  team                  LiteLLM_TeamTable? @relation(fields: [team_id], references: [team_id])
  spend_logs            LiteLLM_SpendLogs[]
  
  @@index([token])
  @@index([user_id])
  @@index([team_id])
  @@map("litellm_verificationtoken")
}

// Users with cumulative spend
model LiteLLM_UserTable {
  user_id           String   @id
  user_email        String?  @unique
  user_role         String   @default("internal_user")
  max_budget        Float?
  soft_budget       Float?
  spend             Float    @default(0)
  budget_duration   String?
  budget_reset_at   DateTime?
  models            String[]
  tpm_limit         Int?
  rpm_limit         Int?
  team_id           String?
  metadata          Json?
  created_at        DateTime @default(now())
  updated_at        DateTime @updatedAt
  
  team              LiteLLM_TeamTable? @relation(fields: [team_id], references: [team_id])
  keys              LiteLLM_VerificationToken[]
  
  @@index([team_id])
  @@map("litellm_usertable")
}

// Teams with cumulative spend
model LiteLLM_TeamTable {
  team_id           String   @id @default(uuid())
  team_alias        String?
  organization_id   String?
  max_budget        Float?
  soft_budget       Float?
  spend             Float    @default(0)
  budget_duration   String?
  budget_reset_at   DateTime?
  model_max_budget  Json?    // {"gpt-4": 100.0, "gpt-3.5": 50.0}
  tpm_limit         Int?
  rpm_limit         Int?
  models            String[]
  blocked           Boolean  @default(false)
  metadata          Json?
  created_at        DateTime @default(now())
  updated_at        DateTime @updatedAt
  
  members           LiteLLM_UserTable[]
  keys              LiteLLM_VerificationToken[]
  
  @@index([organization_id])
  @@map("litellm_teamtable")
}

// Organizations (for multi-tenancy)
model LiteLLM_OrganizationTable {
  id              String   @id @default(uuid())
  organization_id String   @unique
  organization_name String?
  max_budget      Float?
  spend           Float    @default(0)
  budget_duration String?
  budget_reset_at DateTime?
  metadata        Json?
  created_at      DateTime @default(now())
  updated_at      DateTime @updatedAt
  
  @@map("litellm_organizationtable")
}

// End-user spend tracking
model LiteLLM_EndUserTable {
  id          String   @id @default(uuid())
  user_id     String   @unique
  spend       Float    @default(0)
  budget      Float?
  budget_duration String?
  budget_reset_at DateTime?
  metadata    Json?
  created_at  DateTime @default(now())
  updated_at  DateTime @updatedAt
  
  @@map("litellm_endusertable")
}

// Reusable budget configurations
model LiteLLM_BudgetTable {
  id              String   @id @default(uuid())
  budget_id       String   @unique
  budget_name     String?
  max_budget      Float?
  soft_budget     Float?
  budget_duration String?
  metadata        Json?
  created_at      DateTime @default(now())
  updated_at      DateTime @updatedAt
  
  @@map("litellm_budgettable")
}
```

### 5.2 Spend Write Path

```python
from typing import Dict, Any, Optional
import asyncio

class SpendTrackingService:
    """Service for logging spend and updating cumulative counters"""
    
    def __init__(self, db_client, redis_client=None):
        self.db = db_client
        self.redis = redis_client
        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._batch_size = 100
        self._flush_interval = 5  # seconds
    
    async def log_spend(
        self,
        request_id: str,
        api_key: str,
        user_id: Optional[str],
        team_id: Optional[str],
        end_user_id: Optional[str],
        model: str,
        call_type: str,
        usage: Dict[str, int],
        cost: float,
        metadata: Dict[str, Any],
        cache_hit: bool = False,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> None:
        """
        Log spend for a request.
        
        This is the main entry point for spend tracking.
        """
        now = datetime.utcnow()
        
        # Create spend log entry
        spend_log = {
            "request_id": request_id,
            "call_type": call_type,
            "api_key": api_key,
            "spend": cost,
            "total_tokens": usage.get('total_tokens', 0),
            "prompt_tokens": usage.get('prompt_tokens', 0),
            "completion_tokens": usage.get('completion_tokens', 0),
            "start_time": start_time or now,
            "end_time": end_time or now,
            "model": model,
            "api_base": metadata.get('api_base'),
            "user": user_id,
            "team_id": team_id,
            "end_user": end_user_id,
            "metadata": metadata,
            "cache_hit": cache_hit,
            "cache_key": metadata.get('cache_key'),
            "request_tags": metadata.get('tags', [])
        }
        
        # Write to spend logs table
        await self._write_spend_log(spend_log)
        
        # Update cumulative spend (async, non-blocking)
        await self._update_cumulative_spend(api_key, user_id, team_id, cost)
    
    async def _write_spend_log(self, log_entry: Dict[str, Any]) -> None:
        """Write spend log to database"""
        try:
            await self.db.litellm_spendlogs.create(data=log_entry)
        except Exception as e:
            logger.error(f"Failed to write spend log: {e}")
            # TODO: Queue for retry or write to fallback
    
    async def _update_cumulative_spend(
        self,
        api_key: str,
        user_id: Optional[str],
        team_id: Optional[str],
        cost: float
    ) -> None:
        """
        Update cumulative spend counters.
        
        Uses atomic increments where possible.
        """
        updates = []
        
        # Update API key spend
        if api_key:
            updates.append(
                self.db.litellm_verificationtoken.update(
                    where={"token": api_key},
                    data={"spend": {"increment": cost}}
                )
            )
        
        # Update user spend
        if user_id:
            updates.append(
                self.db.litellm_usertable.update(
                    where={"user_id": user_id},
                    data={"spend": {"increment": cost}}
                )
            )
        
        # Update team spend
        if team_id:
            updates.append(
                self.db.litellm_teamtable.update(
                    where={"team_id": team_id},
                    data={"spend": {"increment": cost}}
                )
            )
        
        # Execute all updates concurrently
        if updates:
            try:
                await asyncio.gather(*updates, return_exceptions=True)
            except Exception as e:
                logger.error(f"Failed to update cumulative spend: {e}")
```

---

## 6. Budget Enforcement

### 6.1 Budget Checking Service

```python
from enum import Enum
from typing import Optional, Dict, Any

class BudgetType(str, Enum):
    HARD = "hard"    # Block requests when exceeded
    SOFT = "soft"    # Alert only

class BudgetExceeded(Exception):
    """Raised when budget is exceeded"""
    def __init__(self, entity_type: str, entity_id: str, 
                 spend: float, max_budget: float):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.spend = spend
        self.max_budget = max_budget
        super().__init__(
            f"{entity_type} {entity_id} budget exceeded: "
            f"${spend:.2f} / ${max_budget:.2f}"
        )

class BudgetEnforcementService:
    """Service for checking and enforcing budgets"""
    
    def __init__(self, db_client, alert_service=None):
        self.db = db_client
        self.alerts = alert_service
    
    async def check_budgets(
        self,
        api_key: str,
        user_id: Optional[str],
        team_id: Optional[str],
        organization_id: Optional[str],
        model: Optional[str] = None
    ) -> None:
        """
        Check all applicable budgets.
        
        Raises BudgetExceeded if any hard budget is exceeded.
        """
        # Check key budget
        await self._check_entity_budget("key", api_key, model)
        
        # Check user budget
        if user_id:
            await self._check_entity_budget("user", user_id, model)
        
        # Check team budget
        if team_id:
            await self._check_entity_budget("team", team_id, model)
            # Check team model-specific budget
            if model:
                await self._check_team_model_budget(team_id, model)
        
        # Check organization budget
        if organization_id:
            await self._check_entity_budget("org", organization_id, model)
    
    async def _check_entity_budget(
        self,
        entity_type: str,
        entity_id: str,
        model: Optional[str] = None
    ) -> None:
        """Check budget for a single entity"""
        
        # Get entity data
        entity = await self._get_entity(entity_type, entity_id)
        if not entity:
            return
        
        # Check for budget reset
        await self._check_budget_reset(entity_type, entity)
        
        max_budget = entity.get('max_budget')
        soft_budget = entity.get('soft_budget')
        current_spend = entity.get('spend', 0)
        
        # Hard budget check
        if max_budget and current_spend >= max_budget:
            raise BudgetExceeded(entity_type, entity_id, current_spend, max_budget)
        
        # Soft budget check (trigger alert)
        if soft_budget and current_spend >= soft_budget:
            if self.alerts:
                await self.alerts.send_budget_alert(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    current_spend=current_spend,
                    soft_budget=soft_budget,
                    hard_budget=max_budget
                )
    
    async def _check_team_model_budget(self, team_id: str, model: str) -> None:
        """Check per-model budget within a team"""
        team = await self.db.litellm_teamtable.find_unique(
            where={"team_id": team_id}
        )
        
        if not team or not team.model_max_budget:
            return
        
        model_budgets = team.model_max_budget
        if isinstance(model_budgets, str):
            import json
            model_budgets = json.loads(model_budgets)
        
        # Check if this model has a budget
        if model not in model_budgets:
            return
        
        max_budget = model_budgets[model]
        
        # Calculate spend for this model in this team
        # This requires aggregation query
        model_spend = await self._calculate_team_model_spend(team_id, model)
        
        if model_spend >= max_budget:
            raise BudgetExceeded(
                f"team_model",
                f"{team_id}/{model}",
                model_spend,
                max_budget
            )
    
    async def _check_budget_reset(self, entity_type: str, entity: Dict[str, Any]) -> None:
        """Check if budget needs reset based on duration"""
        
        budget_duration = entity.get('budget_duration')
        budget_reset_at = entity.get('budget_reset_at')
        
        if not budget_duration or not budget_reset_at:
            return
        
        now = datetime.utcnow()
        
        if budget_reset_at <= now:
            # Reset budget
            await self._reset_entity_budget(entity_type, entity['id'], budget_duration)
    
    async def _reset_entity_budget(self, entity_type: str, entity_id: str, duration: str) -> None:
        """Reset spend to 0 and calculate next reset time"""
        
        next_reset = self._calculate_next_reset(duration)
        
        update_data = {
            "spend": 0,
            "budget_reset_at": next_reset
        }
        
        # Update appropriate table
        if entity_type == "key":
            await self.db.litellm_verificationtoken.update(
                where={"id": entity_id},
                data=update_data
            )
        elif entity_type == "user":
            await self.db.litellm_usertable.update(
                where={"id": entity_id},
                data=update_data
            )
        elif entity_type == "team":
            await self.db.litellm_teamtable.update(
                where={"id": entity_id},
                data=update_data
            )
    
    def _calculate_next_reset(self, duration: str) -> datetime:
        """Calculate next reset time from duration string"""
        
        now = datetime.utcnow()
        
        if duration == "1h":
            return now + timedelta(hours=1)
        elif duration == "1d":
            return now + timedelta(days=1)
        elif duration == "7d":
            return now + timedelta(days=7)
        elif duration == "30d":
            return now + timedelta(days=30)
        elif duration == "1mo":
            # Next month, same day (or last day of month)
            from calendar import monthrange
            if now.month == 12:
                next_month = 1
                next_year = now.year + 1
            else:
                next_month = now.month + 1
                next_year = now.year
            
            # Handle month end
            last_day = monthrange(next_year, next_month)[1]
            next_day = min(now.day, last_day)
            
            return now.replace(year=next_year, month=next_month, day=next_day)
        else:
            # Default to 30 days
            return now + timedelta(days=30)
```

---

## 7. Alert System

### 7.1 Alert Configuration

```python
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class AlertType(str, Enum):
    BUDGET_ALERT = "budget_alerts"
    DAILY_REPORT = "daily_reports"
    LLM_EXCEPTION = "llm_exceptions"
    LLM_TOO_SLOW = "llm_too_slow"
    COOLDOWN_DEPLOYMENT = "cooldown_deployment"
    OUTAGE_ALERT = "outage_alerts"
    DB_EXCEPTION = "db_exceptions"

@dataclass
class AlertConfig:
    """Alert system configuration"""
    
    # Alert destinations
    alerting: List[str]  # ["slack", "email", "webhook"]
    
    # Threshold percentage (e.g., 80% of budget)
    alerting_threshold: int = 80
    
    # Enabled alert types
    alert_types: List[AlertType] = None
    
    # Slack configuration
    slack_webhook_url: Optional[str] = None
    slack_token: Optional[str] = None
    slack_channel: Optional[str] = None
    
    # Email configuration
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_sender: Optional[str] = None
    
    # Webhook configuration
    webhook_url: Optional[str] = None
    webhook_headers: Optional[dict] = None
    
    # Rate limiting
    budget_alert_ttl: int = 86400  # Don't alert more than once per day
    daily_report_frequency: int = 86400  # Daily reports every 24 hours
    
    def __post_init__(self):
        if self.alert_types is None:
            self.alert_types = [
                AlertType.BUDGET_ALERT,
                AlertType.LLM_EXCEPTION
            ]
```

### 7.2 Alert Service

```python
class AlertService:
    """Service for sending alerts to various destinations"""
    
    def __init__(self, config: AlertConfig, redis_client=None):
        self.config = config
        self.redis = redis_client
        
        # Initialize destination handlers
        self._handlers: Dict[str, AlertHandler] = {}
        
        if "slack" in config.alerting:
            self._handlers["slack"] = SlackAlertHandler(
                webhook_url=config.slack_webhook_url,
                token=config.slack_token,
                channel=config.slack_channel
            )
        
        if "email" in config.alerting:
            self._handlers["email"] = EmailAlertHandler(
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                username=config.smtp_username,
                password=config.smtp_password,
                sender=config.smtp_sender
            )
        
        if "webhook" in config.alerting:
            self._handlers["webhook"] = WebhookAlertHandler(
                url=config.webhook_url,
                headers=config.webhook_headers
            )
    
    async def send_budget_alert(
        self,
        entity_type: str,
        entity_id: str,
        current_spend: float,
        soft_budget: Optional[float],
        hard_budget: Optional[float]
    ) -> None:
        """Send budget threshold alert"""
        
        if AlertType.BUDGET_ALERT not in self.config.alert_types:
            return
        
        # Check rate limit
        if not await self._check_alert_rate_limit("budget", entity_id):
            return
        
        # Build message
        percentage = (current_spend / hard_budget * 100) if hard_budget else 0
        
        message = (
            f"🚨 Budget Alert: {entity_type} `{entity_id}`\n"
            f"Current Spend: ${current_spend:.2f}\n"
            f"Hard Budget: ${hard_budget:.2f if hard_budget else 'N/A'}\n"
            f"Soft Budget: ${soft_budget:.2f if soft_budget else 'N/A'}\n"
            f"Percentage Used: {percentage:.1f}%"
        )
        
        await self._send_alert(
            alert_type=AlertType.BUDGET_ALERT,
            title=f"Budget Alert - {entity_type}",
            message=message,
            severity="warning" if soft_budget and current_spend < hard_budget else "critical",
            metadata={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "current_spend": current_spend,
                "hard_budget": hard_budget,
                "soft_budget": soft_budget,
                "percentage": percentage
            }
        )
    
    async def send_daily_report(
        self,
        spend_data: Dict[str, Any],
        usage_data: Dict[str, Any]
    ) -> None:
        """Send daily spend/usage report"""
        
        if AlertType.DAILY_REPORT not in self.config.alert_types:
            return
        
        message = (
            f"📊 Daily Report - {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"Total Spend: ${spend_data.get('total', 0):.2f}\n"
            f"Total Requests: {usage_data.get('requests', 0)}\n"
            f"Total Tokens: {usage_data.get('tokens', 0)}\n"
            f"Cache Hit Rate: {usage_data.get('cache_hit_rate', 0):.1f}%"
        )
        
        await self._send_alert(
            alert_type=AlertType.DAILY_REPORT,
            title="Daily Usage Report",
            message=message,
            severity="info",
            metadata={"spend": spend_data, "usage": usage_data}
        )
    
    async def send_deployment_cooldown_alert(
        self,
        deployment_id: str,
        model: str,
        error: str
    ) -> None:
        """Send deployment cooldown alert"""
        
        if AlertType.COOLDOWN_DEPLOYMENT not in self.config.alert_types:
            return
        
        message = (
            f"⚠️ Deployment Cooldown: `{deployment_id}`\n"
            f"Model: {model}\n"
            f"Error: {error}"
        )
        
        await self._send_alert(
            alert_type=AlertType.COOLDOWN_DEPLOYMENT,
            title="Deployment Entered Cooldown",
            message=message,
            severity="warning",
            metadata={"deployment_id": deployment_id, "model": model, "error": error}
        )
    
    async def _check_alert_rate_limit(self, alert_type: str, entity_id: str) -> bool:
        """Check if alert should be rate limited"""
        
        if not self.redis:
            return True
        
        key = f"alert:{alert_type}:{entity_id}"
        ttl = self.config.budget_alert_ttl
        
        # Check if key exists
        if await self.redis.exists(key):
            return False
        
        # Set key for rate limiting
        await self.redis.setex(key, ttl, "1")
        return True
    
    async def _send_alert(
        self,
        alert_type: AlertType,
        title: str,
        message: str,
        severity: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Send alert to all configured handlers"""
        
        alert = {
            "type": alert_type.value,
            "title": title,
            "message": message,
            "severity": severity,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata
        }
        
        # Send to all handlers
        for handler in self._handlers.values():
            try:
                await handler.send(alert)
            except Exception as e:
                logger.error(f"Alert handler {handler.__class__.__name__} failed: {e}")


class SlackAlertHandler:
    """Slack webhook/alert handler"""
    
    def __init__(self, webhook_url: Optional[str] = None, 
                 token: Optional[str] = None,
                 channel: Optional[str] = None):
        self.webhook_url = webhook_url
        self.token = token
        self.channel = channel
    
    async def send(self, alert: Dict[str, Any]) -> None:
        """Send alert to Slack"""
        
        if self.webhook_url:
            # Webhook-based
            async with httpx.AsyncClient() as client:
                payload = {
                    "text": alert["message"],
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": alert["title"]
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": alert["message"]
                            }
                        }
                    ]
                }
                await client.post(self.webhook_url, json=payload)


class EmailAlertHandler:
    """Email SMTP alert handler"""
    
    def __init__(self, smtp_host: str, smtp_port: int,
                 username: Optional[str], password: Optional[str],
                 sender: Optional[str]):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.sender = sender
    
    async def send(self, alert: Dict[str, Any]) -> None:
        """Send email alert"""
        # Implementation using aiosmtplib
        pass


class WebhookAlertHandler:
    """Generic webhook alert handler"""
    
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = url
        self.headers = headers or {}
    
    async def send(self, alert: Dict[str, Any]) -> None:
        """Send alert to webhook"""
        
        async with httpx.AsyncClient() as client:
            await client.post(
                self.url,
                json=alert,
                headers=self.headers
            )
```

---

## 8. Spend Query APIs

### 8.1 API Endpoints

```python
from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from datetime import date, datetime

spend_router = APIRouter(prefix="/spend", tags=["Spend"])

@spend_router.get("/logs")
async def get_spend_logs(
    api_key: Optional[str] = Query(None, description="Filter by API key"),
    user_id: Optional[str] = Query(None, description="Filter by user"),
    team_id: Optional[str] = Query(None, description="Filter by team"),
    model: Optional[str] = Query(None, description="Filter by model"),
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date (YYYY-MM-DD)"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> Dict[str, Any]:
    """
    Query spend logs with filters.
    
    Returns paginated list of spend log entries.
    """
    
    # Build where clause
    where = {}
    
    if api_key:
        where["api_key"] = api_key
    if user_id:
        where["user"] = user_id
    if team_id:
        where["team_id"] = team_id
    if model:
        where["model"] = model
    if tags:
        where["request_tags"] = {"hasEvery": tags}
    
    # Date range
    if start_date or end_date:
        where["start_time"] = {}
        if start_date:
            where["start_time"]["gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            where["start_time"]["lte"] = datetime.combine(end_date, datetime.max.time())
    
    # Query logs
    logs = await db.litellm_spendlogs.find_many(
        where=where,
        take=limit,
        skip=offset,
        order={"start_time": "desc"}
    )
    
    # Get total count
    total = await db.litellm_spendlogs.count(where=where)
    
    return {
        "logs": [serialize_log(log) for log in logs],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total
        }
    }


@spend_router.get("/tags")
async def get_spend_by_tags(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> Dict[str, Any]:
    """Get spend grouped by request tags"""
    
    # Raw query for tag aggregation
    query = """
    SELECT 
        UNNEST(request_tags) as tag,
        SUM(spend) as total_spend,
        COUNT(*) as request_count,
        SUM(total_tokens) as total_tokens
    FROM litellm_spendlogs
    WHERE ($1::timestamp IS NULL OR start_time >= $1)
      AND ($2::timestamp IS NULL OR start_time <= $2)
    GROUP BY tag
    ORDER BY total_spend DESC
    """
    
    start_dt = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end_dt = datetime.combine(end_date, datetime.max.time()) if end_date else None
    
    results = await db.query_raw(query, start_dt, end_dt)
    
    return {"tags": results}


# Global spend endpoints
global_router = APIRouter(prefix="/global", tags=["Global Spend"])

@global_router.get("/spend")
async def get_global_spend(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> Dict[str, float]:
    """Get global spend summary"""
    
    where = {}
    if start_date or end_date:
        where["start_time"] = {}
        if start_date:
            where["start_time"]["gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            where["start_time"]["lte"] = datetime.combine(end_date, datetime.max.time())
    
    result = await db.litellm_spendlogs.aggregate(
        where=where,
        _sum={"spend": True, "total_tokens": True, "prompt_tokens": True, "completion_tokens": True},
        _count={"_all": True}
    )
    
    return {
        "total_spend": result._sum.spend or 0,
        "total_tokens": result._sum.total_tokens or 0,
        "prompt_tokens": result._sum.prompt_tokens or 0,
        "completion_tokens": result._sum.completion_tokens or 0,
        "total_requests": result._count._all
    }


@global_router.get("/spend/report")
async def get_spend_report(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    group_by: str = Query("model", regex="^(model|provider|day|user|team)$"),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> Dict[str, Any]:
    """Get detailed spend report with breakdown"""
    
    group_column = {
        "model": "model",
        "provider": "api_base",
        "day": "DATE(start_time)",
        "user": "user",
        "team": "team_id"
    }.get(group_by, "model")
    
    query = f"""
    SELECT 
        {group_column} as group_key,
        SUM(spend) as total_spend,
        COUNT(*) as request_count,
        SUM(total_tokens) as total_tokens,
        AVG(spend) as avg_spend_per_request
    FROM litellm_spendlogs
    WHERE ($1::timestamp IS NULL OR start_time >= $1)
      AND ($2::timestamp IS NULL OR start_time <= $2)
    GROUP BY {group_column}
    ORDER BY total_spend DESC
    """
    
    start_dt = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end_dt = datetime.combine(end_date, datetime.max.time()) if end_date else None
    
    results = await db.query_raw(query, start_dt, end_dt)
    
    return {
        "group_by": group_by,
        "breakdown": results
    }


@global_router.get("/spend/keys")
async def get_spend_per_key(
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> List[Dict[str, Any]]:
    """Get spend per API key"""
    
    keys = await db.litellm_verificationtoken.find_many(
        select={
            "token": True,
            "key_name": True,
            "spend": True,
            "max_budget": True,
            "user_id": True,
            "team_id": True
        }
    )
    
    return [serialize_key_spend(k) for c in keys]


@global_router.get("/spend/teams")
async def get_spend_per_team(
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> List[Dict[str, Any]]:
    """Get spend per team"""
    
    teams = await db.litellm_teamtable.find_many(
        select={
            "team_id": True,
            "team_alias": True,
            "spend": True,
            "max_budget": True
        }
    )
    
    return [serialize_team_spend(t) for t in teams]


@global_router.get("/spend/end_users")
async def get_spend_per_end_user(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> List[Dict[str, Any]]:
    """Get spend per end user"""
    
    where = {}
    if start_date or end_date:
        where["start_time"] = {}
        if start_date:
            where["start_time"]["gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            where["start_time"]["lte"] = datetime.combine(end_date, datetime.max.time())
    
    # Aggregate by end_user
    query = """
    SELECT 
        COALESCE(end_user, user, 'anonymous') as end_user_id,
        SUM(spend) as total_spend,
        COUNT(*) as request_count
    FROM litellm_spendlogs
    WHERE ($1::timestamp IS NULL OR start_time >= $1)
      AND ($2::timestamp IS NULL OR start_time <= $2)
    GROUP BY end_user_id
    ORDER BY total_spend DESC
    """
    
    start_dt = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end_dt = datetime.combine(end_date, datetime.max.time()) if end_date else None
    
    results = await db.query_raw(query, start_dt, end_dt)
    
    return results


@global_router.get("/spend/models")
async def get_spend_per_model(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> List[Dict[str, Any]]:
    """Get spend per model"""
    
    where = {}
    if start_date or end_date:
        where["start_time"] = {}
        if start_date:
            where["start_time"]["gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            where["start_time"]["lte"] = datetime.combine(end_date, datetime.max.time())
    
    # Aggregate by model
    result = await db.litellm_spendlogs.group_by(
        by=["model"],
        where=where,
        _sum={"spend": True, "total_tokens": True},
        _count={"_all": True}
    )
    
    return [
        {
            "model": r.model,
            "total_spend": r._sum.spend or 0,
            "total_tokens": r._sum.total_tokens or 0,
            "request_count": r._count._all
        }
        for r in result
    ]


@global_router.get("/activity")
async def get_global_activity(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: PrismaClient = Depends(get_db),
    _: str = Depends(require_master_key)
) -> Dict[str, Any]:
    """Get activity metrics (request counts, tokens)"""
    
    where = {}
    if start_date or end_date:
        where["start_time"] = {}
        if start_date:
            where["start_time"]["gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            where["start_time"]["lte"] = datetime.combine(end_date, datetime.max.time())
    
    result = await db.litellm_spendlogs.aggregate(
        where=where,
        _sum={"total_tokens": True, "prompt_tokens": True, "completion_tokens": True},
        _count={"_all": True}
    )
    
    # Get cache hit rate
    cache_hits = await db.litellm_spendlogs.count(
        where={**where, "cache_hit": True}
    )
    
    total = result._count._all
    
    return {
        "total_requests": total,
        "total_tokens": result._sum.total_tokens or 0,
        "prompt_tokens": result._sum.prompt_tokens or 0,
        "completion_tokens": result._sum.completion_tokens or 0,
        "cache_hits": cache_hits,
        "cache_misses": total - cache_hits,
        "cache_hit_rate": (cache_hits / total * 100) if total > 0 else 0
    }
```

### 8.2 Response Schemas

```python
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class SpendLogEntry(BaseModel):
    """Single spend log entry"""
    id: str
    request_id: str
    call_type: str
    model: str
    api_provider: str
    api_key: str
    spend: float
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    start_time: datetime
    end_time: datetime
    user: Optional[str]
    team_id: Optional[str]
    cache_hit: bool
    request_tags: List[str]

class SpendSummary(BaseModel):
    """Spend summary response"""
    total_spend: float
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    total_requests: int

class SpendBreakdownItem(BaseModel):
    """Item in spend breakdown"""
    group_key: str
    total_spend: float
    request_count: int
    total_tokens: int
    avg_spend_per_request: Optional[float]

class SpendReport(BaseModel):
    """Detailed spend report"""
    group_by: str
    breakdown: List[SpendBreakdownItem]

class ActivityMetrics(BaseModel):
    """Activity metrics response"""
    total_requests: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float
```

---

## 9. Integration with Phase 1-3

### 9.1 Request Lifecycle with Callbacks

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    REQUEST LIFECYCLE WITH CALLBACKS                         │
└─────────────────────────────────────────────────────────────────────────────┘

1. CLIENT REQUEST
   POST /v1/chat/completions
   Headers: Authorization: Bearer sk-...
   Body: {"model": "gpt-4", "messages": [...]}
   
         ↓
         
2. PRE-CALL HOOKS
   ┌─────────────────────────────────────────────────────────┐
   │ Execute async_pre_call_hook on all registered           │
   │ callbacks (guardrails, custom validation)               │
   │ Can modify request or raise to block                    │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
3. AUTH / RATE LIMIT / ROUTING (Phase 1-2)
   ┌─────────────────────────────────────────────────────────┐
   │ • Virtual key validation                                │
   │ • Rate limit checks (RPM/TPM/parallel)                  │
   │ • Budget enforcement (hard budget check)                │
   │ • Routing (simple-shuffle, least-busy, etc.)            │
   │ • Cache lookup (Phase 3)                                │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
4. PROVIDER EXECUTION
   ┌─────────────────────────────────────────────────────────┐
   │ • Transform request to provider format                  │
   │ • Execute with retry/failover logic                     │
   │ • Record API latency                                    │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
5. POST-CALL HOOKS
   ┌─────────────────────────────────────────────────────────┐
   │ Execute async_post_call_success_hook on callbacks       │
   │ (output guardrails, content filtering)                  │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
6. COST CALCULATION & SPEND LOGGING
   ┌─────────────────────────────────────────────────────────┐
   │ • Calculate token usage                                 │
   │ • Calculate cost via completion_cost()                  │
   │ • Write to LiteLLM_SpendLogs                            │
   │ • Update cumulative spend (key/user/team)               │
   │ • Check soft budget thresholds                          │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
7. CALLBACK EXECUTION (async, non-blocking)
   ┌─────────────────────────────────────────────────────────┐
   │ • Prometheus metrics (counters, histograms)             │
   │ • Langfuse trace logging                                │
   │ • OpenTelemetry span export                             │
   │ • S3/GCS log storage                                    │
   │ • Custom success_callback handlers                      │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
8. CLIENT RESPONSE
   Return ChatCompletionResponse with headers:
   • x-litellm-call-id: {request_id}
   • x-litellm-model-id: {deployment_id}
   • x-litellm-cache-hit: true/false
```

### 9.2 Error Path with Callbacks

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ERROR PATH WITH CALLBACKS                               │
└─────────────────────────────────────────────────────────────────────────────┘

When error occurs at any stage:

1. CATCH EXCEPTION
         ↓
         
2. POST-CALL FAILURE HOOK
   ┌─────────────────────────────────────────────────────────┐
   │ Execute async_post_call_failure_hook on callbacks       │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
3. FAILURE CALLBACK EXECUTION
   ┌─────────────────────────────────────────────────────────┐
   │ • Prometheus request_failures counter                   │
   │ • Langfuse error trace                                  │
   │ • Sentry error reporting                                │
   │ • Custom failure_callback handlers                      │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
4. ERROR RESPONSE TO CLIENT
   Return standardized error with:
   • error.type
   • error.message
   • error.code
   • x-litellm-call-id header
```

### 9.3 Integration Points

```python
# ============================================================================
# Middleware Chain Integration
# ============================================================================

class ObservabilityMiddleware:
    """Middleware that orchestrates callbacks and spend tracking"""
    
    def __init__(
        self,
        callback_manager: CallbackManager,
        spend_service: SpendTrackingService,
        cost_service: CostCalculationService,
        budget_service: BudgetEnforcementService,
        metrics: PrometheusMetrics
    ):
        self.callbacks = callback_manager
        self.spend = spend_service
        self.cost = cost_service
        self.budget = budget_service
        self.metrics = metrics
    
    async def process_request(self, request: Request) -> Response:
        """Process request with full observability"""
        
        start_time = datetime.utcnow()
        request_id = generate_request_id()
        
        # Attach to request state
        request.state.start_time = start_time
        request.state.request_id = request_id
        
        try:
            # Pre-call hooks
            await self.callbacks.execute_pre_call_hooks(request)
            
            # Execute request (through rest of middleware chain)
            response = await self.call_next(request)
            
            # Post-call success hooks
            await self.callbacks.execute_post_call_hooks(request, response)
            
            # Calculate cost
            usage = extract_usage(response)
            cost = self.cost.calculate_request_cost(
                model=request.state.model,
                usage=usage,
                deployment_config=request.state.deployment,
                cache_hit=getattr(request.state, 'cache_hit', False)
            )
            
            # Log spend
            await self.spend.log_spend(
                request_id=request_id,
                api_key=request.state.user_api_key.api_key,
                user_id=request.state.user_api_key.user_id,
                team_id=request.state.user_api_key.team_id,
                end_user_id=request.state.end_user_id,
                model=request.state.model,
                call_type="completion",
                usage=usage,
                cost=cost,
                metadata=build_metadata(request, response),
                cache_hit=getattr(request.state, 'cache_hit', False),
                start_time=start_time,
                end_time=datetime.utcnow()
            )
            
            # Execute success callbacks (async, fire-and-forget)
            asyncio.create_task(
                self.callbacks.execute_success_callbacks(
                    build_logging_payload(request, response, cost)
                )
            )
            
            # Add response headers
            response.headers["x-litellm-call-id"] = request_id
            response.headers["x-litellm-cache-hit"] = str(
                getattr(request.state, 'cache_hit', False)
            ).lower()
            
            return response
            
        except Exception as e:
            # Post-call failure hooks
            await self.callbacks.execute_failure_hooks(request, e)
            
            # Execute failure callbacks
            asyncio.create_task(
                self.callbacks.execute_failure_callbacks(
                    build_logging_payload(request, None, 0),
                    e
                )
            )
            
            raise
```

---

## 10. Worktree Breakdown

### 10.1 Phase 4 Worktrees

| Worktree | Scope | Dependencies | Estimated LOC |
|----------|-------|--------------|---------------|
| `callbacks` | CustomLogger base, registration, built-in integrations | core-api | 1200 |
| `metrics` | Prometheus metrics, cost calculation | callbacks | 800 |
| `billing` | Spend tracking, budgets, alerts, query APIs | metrics, core-db | 1000 |

### 10.2 Worktree: callbacks

```yaml
Name: worktree-callbacks
Scope: Callback system and built-in integrations
Inputs:
  - Request context from core-api
  - Configuration from litellm_settings
Outputs:
  - src/observability/callbacks/
      - base.py              # CustomLogger base class
      - manager.py           # Callback registration and execution
      - payload.py           # StandardLoggingPayload schema
  - src/observability/integrations/
      - prometheus.py        # Prometheus callback handler
      - langfuse.py          # Langfuse integration
      - opentelemetry.py     # OpenTelemetry integration
      - s3.py                # S3 logging integration
      - gcs.py               # GCS logging integration
      - datadog.py           # Datadog integration (P1)
      - sentry.py            # Sentry error tracking (P1)
Acceptance Criteria:
  - CustomLogger can be extended for custom integrations
  - All built-in callbacks execute without blocking
  - Standard payload contains all required fields
  - Callback failures don't affect client response
Integration Points:
  - Called by: ObservabilityMiddleware in request lifecycle
  - Configuration: litellm_settings.success_callback, failure_callback
```

### 10.3 Worktree: metrics

```yaml
Name: worktree-metrics
Scope: Prometheus metrics and cost calculation
Inputs:
  - Token usage from provider responses
  - Model pricing from cost map
  - Deployment config from model_list
Outputs:
  - src/observability/metrics/
      - registry.py          # Prometheus metrics definitions
      - prometheus.py        # Prometheus callback handler
      - cardinality.py       # Cardinality controls
  - src/billing/cost/
      - map.py               # Model cost map and overrides
      - calculation.py       # completion_cost() function
      - service.py           # CostCalculationService
Acceptance Criteria:
  - /metrics endpoint returns valid Prometheus format
  - All counters/histograms increment correctly
  - Cost calculation accurate for all supported models
  - Custom pricing overrides work via model_list config
  - Cardinality controls prevent label explosion
Integration Points:
  - Uses: Model registry from core-router
  - Called by: ObservabilityMiddleware
  - Provides: Metrics for Prometheus scraping
```

### 10.4 Worktree: billing

```yaml
Name: worktree-billing
Scope: Spend tracking, budget enforcement, alerts, query APIs
Inputs:
  - Cost from metrics worktree
  - Database schema from core-db
  - Alert configuration from general_settings
Outputs:
  - src/billing/tracking/
      - service.py           # SpendTrackingService
      - repository.py        # Spend log repository
  - src/billing/enforcement/
      - budgets.py           # BudgetEnforcementService
      - reset.py             # Budget reset scheduler
  - src/billing/alerts/
      - service.py           # AlertService
      - handlers.py          # Slack/email/webhook handlers
  - src/api/routes/
      - spend.py             # Spend query API endpoints
Acceptance Criteria:
  - Every request creates a spend log entry
  - Cumulative spend updates atomically
  - Hard budgets block requests deterministically
  - Soft budgets trigger alerts without blocking
  - Budget reset occurs at configured intervals
  - All spend query endpoints return accurate data
  - Alerts respect rate limiting (no spam)
Integration Points:
  - Uses: Database from core-db
  - Uses: Redis for alert rate limiting
  - Called by: ObservabilityMiddleware
  - Provides: Admin API endpoints for spend queries
```

### 10.5 Integration Checkpoints

| Checkpoint | Description | Verification |
|------------|-------------|--------------|
| CP1 | Callback system basic | Custom callback receives success events |
| CP2 | Prometheus metrics | /metrics shows request counts |
| CP3 | Cost calculation | Known usage produces expected cost |
| CP4 | Spend logging | Every request creates DB entry |
| CP5 | Budget enforcement | Hard budget blocks requests |
| CP6 | Alert system | Budget threshold triggers notification |
| CP7 | Spend APIs | Query endpoints return accurate data |
| CP8 | Full integration | End-to-end request with all observability |

---

## Appendix A: Database Migrations

### A.1 Required Indexes

```sql
-- For spend log queries
CREATE INDEX CONCURRENTLY idx_spendlogs_api_key_time 
ON litellm_spendlogs(api_key, start_time DESC);

CREATE INDEX CONCURRENTLY idx_spendlogs_team_time 
ON litellm_spendlogs(team_id, start_time DESC);

CREATE INDEX CONCURRENTLY idx_spendlogs_user_time 
ON litellm_spendlogs(user, start_time DESC);

CREATE INDEX CONCURRENTLY idx_spendlogs_model_time 
ON litellm_spendlogs(model, start_time DESC);

-- For tag-based queries (GIN index for array)
CREATE INDEX CONCURRENTLY idx_spendlogs_tags 
ON litellm_spendlogs USING GIN(request_tags);

-- For budget reset queries
CREATE INDEX CONCURRENTLY idx_keys_budget_reset 
ON litellm_verificationtoken(budget_reset_at) 
WHERE budget_reset_at IS NOT NULL;

CREATE INDEX CONCURRENTLY idx_users_budget_reset 
ON litellm_usertable(budget_reset_at) 
WHERE budget_reset_at IS NOT NULL;

CREATE INDEX CONCURRENTLY idx_teams_budget_reset 
ON litellm_teamtable(budget_reset_at) 
WHERE budget_reset_at IS NOT NULL;
```

### A.2 Partitioning Strategy (Future)

```sql
-- For high-volume deployments, partition spend logs by time
CREATE TABLE litellm_spendlogs_partitioned (
    LIKE litellm_spendlogs INCLUDING ALL
) PARTITION BY RANGE (start_time);

-- Create monthly partitions
CREATE TABLE litellm_spendlogs_y2024m01 
PARTITION OF litellm_spendlogs_partitioned
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

---

## Appendix B: Configuration Reference

### B.1 Complete Observability Configuration

```yaml
litellm_settings:
  # Callback configuration
  success_callback: ["prometheus", "langfuse", "s3"]
  failure_callback: ["langfuse", "sentry"]
  callbacks: []
  
  # PII protection
  turn_off_message_logging: false
  
  # Prometheus cardinality controls
  prometheus_label_settings:
    disable_end_user_label: false
    disable_api_base_label: true
    max_label_value_length: 128
  
  # Integration settings
  callback_settings:
    langfuse:
      public_key: "os.environ/LANGFUSE_PUBLIC_KEY"
      secret_key: "os.environ/LANGFUSE_SECRET_KEY"
      host: "https://cloud.langfuse.com"
    
    s3:
      bucket: "my-litellm-logs"
      region: "us-east-1"
      prefix: "logs/"
      compression: "gzip"
    
    opentelemetry:
      endpoint: "http://otel-collector:4317"
      service_name: "deltallm"

general_settings:
  # Alert configuration
  alerting: ["slack", "email"]
  alerting_threshold: 80
  alert_types:
    - budget_alerts
    - daily_reports
    - llm_exceptions
    - llm_too_slow
    - cooldown_deployment
  
  alerting_args:
    slack_webhook_url: "https://hooks.slack.com/..."
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    smtp_username: "os.environ/SMTP_USERNAME"
    smtp_password: "os.environ/SMTP_PASSWORD"
    smtp_sender: "alerts@company.com"
    daily_report_frequency: 86400
    budget_alert_ttl: 86400
```

---

*End of Phase 4 Observability/Metrics/Billing Technical Specification*
