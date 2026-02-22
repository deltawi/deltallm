# Phase 1 Core Foundation - Technical Specification

**Document Control:**
- **Version:** 1.0
- **Date:** 2026-02-13
- **Owner:** Analyst-Core
- **Status:** Draft

**PRD Reference:** `docs/master-prd-outline.md` (Sections 5, 6, 7, 8, 9)

**Phase Scope:** Core Proxy foundation including auth, routing basics, virtual keys, rate limiting, and initial observability.

---

## Section 1: API Contract Definitions

### 1.1 Core Endpoints (P0)

| Endpoint | Method | Auth | Description | PRD Ref |
|----------|--------|------|-------------|---------|
| `/v1/chat/completions` | POST | Virtual Key | Chat completions | 9.2 |
| `/v1/embeddings` | POST | Virtual Key | Text embeddings | 9.2 |
| `/v1/models` | GET | Virtual Key | List available models | 9.2 |
| `/health` | GET | Optional | Deep health check | 8.3 |
| `/health/liveliness` | GET | None | K8s liveness probe | 8.3 |
| `/health/readiness` | GET | None | K8s readiness probe | 8.3 |
| `/key/generate` | POST | Master Key | Create virtual key | 9.2 |
| `/key/update` | POST | Master Key | Update key params | 9.2 |
| `/key/delete` | POST | Master Key | Revoke key | 9.2 |
| `/key/info` | GET | Master Key | Get key details | 9.2 |
| `/key/list` | GET | Master Key | List all keys | 9.2 |

### 1.2 Request/Response Schemas

```python
# Pydantic Models - Common Types
from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: Dict[str, Any]  # name, description, parameters

class ToolChoice(BaseModel):
    type: Literal["function"] = "function"
    function: Dict[str, str]  # {"name": "function_name"}

class ResponseFormat(BaseModel):
    type: Literal["text", "json_object", "json_schema"]
    json_schema: Optional[Dict[str, Any]] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    top_p: Optional[float] = Field(default=1.0, ge=0, le=1)
    n: Optional[int] = Field(default=1, ge=1, le=10)
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[Union[Literal["auto", "none", "required"], ToolChoice]] = "auto"
    response_format: Optional[ResponseFormat] = None
    user: Optional[str] = None  # end-user ID for tracking
    metadata: Optional[Dict[str, Any]] = None

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter"]] = None
    logprobs: Optional[Dict[str, Any]] = None

class ChatCompletionResponse(BaseModel):
    id: str  # chatcmpl-<uuid>
    object: Literal["chat.completion"] = "chat.completion"
    created: int  # unix timestamp
    model: str
    choices: List[Choice]
    usage: Usage
    system_fingerprint: Optional[str] = None

class EmbeddingRequest(BaseModel):
    model: str
    input: Union[str, List[str], List[int], List[List[int]]]
    encoding_format: Optional[Literal["float", "base64"]] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None

class EmbeddingData(BaseModel):
    object: Literal["embedding"] = "embedding"
    embedding: List[float]
    index: int

class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[EmbeddingData]
    model: str
    usage: Usage

class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str

class ModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[ModelInfo]
```

### 1.3 Virtual Key Management Schemas

```python
class GenerateKeyRequest(BaseModel):
    key_alias: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    models: Optional[List[str]] = None  # allowed models
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None  # "1h", "1d", "30d"
    tpm_limit: Optional[int] = None
    rpm_limit: Optional[int] = None
    max_parallel_requests: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    expires: Optional[str] = None  # ISO datetime
    permissions: Optional[Dict[str, bool]] = None

class GenerateKeyResponse(BaseModel):
    key: str  # the actual key (shown once)
    key_alias: Optional[str]
    user_id: Optional[str]
    team_id: Optional[str]
    models: List[str]
    max_budget: Optional[float]
    tpm_limit: Optional[int]
    rpm_limit: Optional[int]
    expires: Optional[str]

class KeyInfoResponse(BaseModel):
    token: str  # hashed key identifier
    key_name: Optional[str]
    user_id: Optional[str]
    team_id: Optional[str]
    models: List[str]
    max_budget: Optional[float]
    spend: float
    tpm_limit: Optional[int]
    rpm_limit: Optional[int]
    max_parallel_requests: Optional[int]
    expires: Optional[str]
    created_at: str
```

### 1.4 Error Taxonomy

```python
class ProxyError(Exception):
    """Base proxy error with HTTP status mapping"""
    status_code: int = 500
    error_type: str = "server_error"
    message: str = "Internal server error"
    param: Optional[str] = None
    code: Optional[str] = None

class AuthenticationError(ProxyError):
    status_code = 401
    error_type = "authentication_error"
    message = "Invalid API key"

class RateLimitError(ProxyError):
    status_code = 429
    error_type = "rate_limit_error"
    message = "Rate limit exceeded"
    retry_after: Optional[int] = None

class BudgetExceededError(ProxyError):
    status_code = 400
    error_type = "budget_exceeded"
    message = "Budget exceeded"

class ModelNotFoundError(ProxyError):
    status_code = 404
    error_type = "model_not_found"
    message = "Model not found"

class TimeoutError(ProxyError):
    status_code = 408
    error_type = "timeout_error"
    message = "Request timeout"

class InvalidRequestError(ProxyError):
    status_code = 400
    error_type = "invalid_request_error"

class PermissionDeniedError(ProxyError):
    status_code = 403
    error_type = "permission_denied"

class ServiceUnavailableError(ProxyError):
    status_code = 503
    error_type = "service_unavailable"

# Error Response JSON Schema
class ErrorResponse(BaseModel):
    error: Dict[str, Any] = Field(..., example={
        "message": "Invalid API key",
        "type": "authentication_error",
        "param": None,
        "code": "invalid_api_key"
    })
```

### 1.5 Streaming Format (SSE)

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

# First chunk
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gpt-4","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

# Content chunks
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gpt-4","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gpt-4","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}

# Final chunk
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gpt-4","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

# Terminator
data: [DONE]
```

---

## Section 2: Database Schema

### 2.1 Prisma Schema

```prisma
generator client {
  provider = "prisma-client-py"
  interface = "asyncio"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

// Virtual Keys / API Keys
model LiteLLM_VerificationToken {
  id                    String   @id @default(uuid())
  token                 String   @unique  // SHA-256 hash of key
  key_name              String?
  user_id               String?
  team_id               String?
  models                String[]  // allowed model names
  max_budget            Float?
  spend                 Float    @default(0)
  budget_duration       String?   // "1h", "1d", "30d"
  budget_reset_at       DateTime?
  rpm_limit             Int?
  tpm_limit             Int?
  max_parallel_requests Int?
  expires               DateTime?
  permissions           Json?     // {"allow_model_list": true, ...}
  metadata              Json?
  created_at            DateTime @default(now())
  updated_at            DateTime @updatedAt
  
  user                  LiteLLM_UserTable? @relation(fields: [user_id], references: [user_id])
  team                  LiteLLM_TeamTable? @relation(fields: [team_id], references: [team_id])
  spend_logs            LiteLLM_SpendLogs[]
  
  @@index([token])
  @@index([user_id])
  @@index([team_id])
  @@index([expires])
  @@map("litellm_verificationtoken")
}

// Users
model LiteLLM_UserTable {
  user_id           String   @id
  user_email        String?  @unique
  user_role         String   @default("internal_user")  // proxy_admin, team_admin, etc
  max_budget        Float?
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

// Teams
model LiteLLM_TeamTable {
  team_id           String   @id @default(uuid())
  team_alias        String?
  organization_id   String?
  max_budget        Float?
  spend             Float    @default(0)
  budget_duration   String?
  budget_reset_at   DateTime?
  model_max_budget  Json?     // {"gpt-4": 100.0, "gpt-3.5": 50.0}
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

// Spend Logs (append-only, time-series)
model LiteLLM_SpendLogs {
  id                String   @id @default(uuid())
  request_id        String
  call_type         String   // "completion", "embedding", etc
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
  
  key               LiteLLM_VerificationToken? @relation(fields: [api_key], references: [token])
  
  @@index([api_key])
  @@index([team_id])
  @@index([user])
  @@index([start_time])
  @@index([model])
  @@index([request_tags])
  @@map("litellm_spendlogs")
}

// Dynamic Config (hot-reload support)
model LiteLLM_Config {
  id          String   @id @default(uuid())
  config_name String   @unique
  config_value Json
  updated_at  DateTime @updatedAt
  updated_by  String?
  
  @@map("litellm_config")
}

// Model Deployments Registry
model LiteLLM_ModelTable {
  id            String   @id @default(uuid())
  model_name    String   // public model group name
  model_info    Json     // litellm_params: provider, api_base, etc
  litellm_model_name String // actual provider model identifier
  model_provider String
  created_at    DateTime @default(now())
  updated_at    DateTime @updatedAt
  
  @@index([model_name])
  @@map("litellm_modeltable")
}
```

### 2.2 Redis Key Patterns

```
# Rate Limiting - Sliding window counters
ratelimit:key:{key_hash}:{window}          -> counter
ratelimit:user:{user_id}:{window}          -> counter
ratelimit:team:{team_id}:{window}          -> counter

# API Key Cache - TTL based on key expiry
key:{key_hash}                             -> JSON(key_data)

# Routing State
deployment:cooldown:{deployment_id}        -> TTL timestamp
deployment:active_requests:{deployment_id} -> counter
deployment:latency:{deployment_id}         -> sorted set (timestamp, latency_ms)

# Response Cache (P1)
cache:{sha256_request_key}                 -> JSON(cached_response)

# Config Pub/Sub
channel:config_updates                     -> "{config_name: updated_at}"
```

---

## Section 3: Component Architecture

### 3.1 Core Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DeltaLLM Proxy (FastAPI)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  Auth        │  │  RateLimit   │  │  Router      │  │  Provider    │    │
│  │  Middleware  │→│  Middleware  │→│  Middleware  │→│  Adapter     │    │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │
│         ↓                 ↓                 ↓                 ↓             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                     Request Context / State                          │  │
│  │   user_api_key_dict, request_id, start_time, model_group, etc      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                           External Dependencies                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  PostgreSQL (Prisma)    │   Redis             │   LLM Providers            │
│  - keys/users/teams     │   - rate limits     │   - OpenAI                 │
│  - spend logs           │   - key cache       │   - Groq           │
│  - config               │   - cooldown state  │   - Anthropic              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Interface Definitions

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, AsyncIterator, Optional
from pydantic import BaseModel

# ============================================================================
# Provider Adapter Interface
# ============================================================================

class ProviderAdapter(ABC):
    """Abstract base for LLM provider adapters"""
    
    provider_name: str
    
    @abstractmethod
    async def translate_request(
        self, 
        canonical_request: ChatCompletionRequest,
        provider_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Convert OpenAI-compatible request to provider-specific format"""
        pass
    
    @abstractmethod
    async def translate_response(
        self,
        provider_response: Any,
        model_name: str
    ) -> ChatCompletionResponse:
        """Convert provider response to OpenAI-compatible format"""
        pass
    
    @abstractmethod
    async def translate_stream(
        self,
        provider_stream: AsyncIterator[Any]
    ) -> AsyncIterator[str]:
        """Translate streaming chunks to SSE format"""
        pass
    
    @abstractmethod
    def map_error(self, provider_error: Exception) -> ProxyError:
        """Map provider-specific errors to standard proxy errors"""
        pass
    
    @abstractmethod
    async def health_check(self, provider_config: Dict[str, Any]) -> bool:
        """Check if deployment is healthy"""
        pass


# ============================================================================
# Routing Strategy Interface
# ============================================================================

class RoutingStrategy(ABC):
    """Abstract base for load balancing strategies"""
    
    @abstractmethod
    async def select_deployment(
        self,
        model_group: str,
        healthy_deployments: List[Dict[str, Any]],
        request_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Select a deployment from the healthy pool"""
        pass


# ============================================================================
# Auth Handler Interface
# ============================================================================

class UserAPIKeyAuth(BaseModel):
    """Authenticated key context attached to requests"""
    api_key: str  # hashed
    user_id: Optional[str]
    team_id: Optional[str]
    user_role: Optional[str]
    models: List[str]
    max_budget: Optional[float]
    spend: float
    tpm_limit: Optional[int]
    rpm_limit: Optional[int]
    max_parallel_requests: Optional[int]
    metadata: Optional[Dict[str, Any]]
    expires: Optional[str]

class AuthHandler(ABC):
    """Abstract base for authentication handlers"""
    
    @abstractmethod
    async def authenticate(
        self, 
        api_key: str,
        request: Any  # FastAPI Request
    ) -> UserAPIKeyAuth:
        """Validate API key and return auth context"""
        pass


# ============================================================================
# Rate Limit Handler Interface
# ============================================================================

class RateLimitHandler(ABC):
    """Abstract base for rate limiting"""
    
    @abstractmethod
    async def check_rate_limit(
        self,
        scope: str,  # "key", "user", "team"
        entity_id: str,
        rpm_limit: Optional[int],
        tpm_limit: Optional[int]
    ) -> None:
        """Check and increment rate limits. Raise RateLimitError if exceeded."""
        pass
    
    @abstractmethod
    async def check_parallel_limit(
        self,
        scope: str,
        entity_id: str,
        max_parallel: Optional[int]
    ) -> None:
        """Check parallel request limits"""
        pass


# ============================================================================
# Callback Logger Interface
# ============================================================================

class CallbackLogger(ABC):
    """Abstract base for logging callbacks"""
    
    @abstractmethod
    async def log_success(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any
    ) -> None:
        pass
    
    @abstractmethod
    async def log_failure(
        self,
        kwargs: Dict[str, Any],
        exception: Exception,
        start_time: Any,
        end_time: Any
    ) -> None:
        pass
```

### 3.3 Core Services

```python
# ============================================================================
# Key Management Service
# ============================================================================

class KeyManagementService:
    """Service for virtual key CRUD operations"""
    
    async def generate_key(
        self,
        request: GenerateKeyRequest,
        created_by: Optional[str] = None
    ) -> GenerateKeyResponse:
        """Generate new virtual key with random token"""
        pass
    
    async def validate_key(self, key: str) -> UserAPIKeyAuth:
        """Validate key and return auth context (cache-first)"""
        pass
    
    async def revoke_key(self, key_hash: str) -> None:
        """Revoke a key"""
        pass
    
    async def update_key(
        self, 
        key_hash: str, 
        updates: Dict[str, Any]
    ) -> None:
        """Update key parameters"""
        pass
    
    async def list_keys(
        self,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[KeyInfoResponse]:
        """List keys with optional filtering"""
        pass


# ============================================================================
# Routing Service
# ============================================================================

class RoutingService:
    """Service for model group routing and deployment selection"""
    
    def __init__(
        self,
        strategy: RoutingStrategy,
        redis: Optional[Any] = None
    ):
        self.strategy = strategy
        self.redis = redis
    
    async def get_deployments(self, model_group: str) -> List[Dict[str, Any]]:
        """Get all deployments for a model group"""
        pass
    
    async def select_deployment(
        self,
        model_group: str,
        request_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Select healthy deployment using configured strategy"""
        pass
    
    async def mark_cooldown(
        self, 
        deployment_id: str, 
        duration_seconds: int
    ) -> None:
        """Mark deployment as cooling down"""
        pass
    
    async def is_healthy(self, deployment_id: str) -> bool:
        """Check if deployment is healthy (not in cooldown)"""
        pass


# ============================================================================
# Spend Tracking Service
# ============================================================================

class SpendTrackingService:
    """Service for spend calculation and tracking"""
    
    async def calculate_cost(
        self,
        model: str,
        usage: Usage,
        cache_hit: bool = False
    ) -> float:
        """Calculate cost from model pricing and token usage"""
        pass
    
    async def log_spend(
        self,
        request_id: str,
        api_key: str,
        model: str,
        usage: Usage,
        cost: float,
        metadata: Dict[str, Any]
    ) -> None:
        """Log spend to database (async, non-blocking)"""
        pass
    
    async def check_budget(
        self,
        entity_type: str,  # "key", "user", "team"
        entity_id: str,
        max_budget: Optional[float]
    ) -> None:
        """Check if budget exceeded. Raise BudgetExceededError if so."""
        pass
```

---

## Section 4: Data Flow & Lifecycle

### 4.1 Request Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        REQUEST LIFECYCLE FLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

1. CLIENT REQUEST
   POST /v1/chat/completions
   Headers: Authorization: Bearer sk-...
   Body: {"model": "gpt-4", "messages": [...]}
   
         ↓
         
2. AUTH MIDDLEWARE
   ┌─────────────────────────────────────────────────────────┐
   │ • Extract API key from Authorization header              │
   │ • Check Redis cache: key:{hash}                         │
   │   ├─ Cache HIT: Use cached UserAPIKeyAuth               │
   │   └─ Cache MISS: Query DB, populate cache               │
   │ • Validate key: expiry, budget, model access            │
   │ • Check budget enforcement                              │
   │ • Attach UserAPIKeyAuth to request.state                │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
3. RATE LIMIT MIDDLEWARE
   ┌─────────────────────────────────────────────────────────┐
   │ • Increment RPM counter in Redis (sliding window)       │
   │ • Increment TPM counter (estimate from request)         │
   │ • Check against key/user/team limits                    │
   │ • Check parallel request limits                         │
   │ • Raise RateLimitError if any limit exceeded            │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
4. ROUTING MIDDLEWARE
   ┌─────────────────────────────────────────────────────────┐
   │ • Resolve model group from request.model                │
   │ • Get healthy deployments for model group               │
   │ • Exclude deployments in cooldown                       │
   │ • Apply routing strategy (simple-shuffle/least-busy)    │
   │ • Select target deployment                              │
   │ • Attach deployment config to request.state             │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
5. PROVIDER ADAPTER
   ┌─────────────────────────────────────────────────────────┐
   │ • Get provider adapter for selected deployment          │
   │ • Translate canonical request → provider format         │
   │ • Execute request with retry logic                      │
   │ • Handle timeouts and connection errors                 │
   │ • On failure: mark cooldown, retry with fallback        │
   │ • Translate provider response → canonical format        │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
6. POST-PROCESSING
   ┌─────────────────────────────────────────────────────────┐
   │ • Calculate token usage and cost                        │
   │ • Emit Prometheus metrics                               │
   │ • Fire success callbacks (async, non-blocking)          │
   │ • Log spend to database (async)                         │
   │ • Update cumulative spend counters                      │
   └─────────────────────────────────────────────────────────┘
   
         ↓
         
7. CLIENT RESPONSE
   Return ChatCompletionResponse with headers:
   • x-litellm-call-id: {request_id}
   • x-litellm-model-id: {deployment_id}
```

### 4.2 Streaming Request Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      STREAMING REQUEST FLOW                                 │
└─────────────────────────────────────────────────────────────────────────────┘

1. Request received with stream: true

2. All middleware checks (auth, rate limit, routing) - same as non-streaming

3. Provider adapter executes streaming request
   
4. Response handling:
   ┌─────────────────────────────────────────────────────────┐
   │ Provider Stream → Adapter Translation → SSE Chunks      │
   │                                                         │
   │ • Each chunk translated to OpenAI SSE format            │
   │ • Chunks yielded immediately to client                  │
   │ • Full response assembled for logging (P1: caching)     │
   │ • Final chunk includes usage statistics                 │
   └─────────────────────────────────────────────────────────┘

5. Post-stream processing (async):
   • Calculate actual token usage (from provider or tiktoken)
   • Log spend and emit callbacks
```

### 4.3 Error Handling Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ERROR HANDLING FLOW                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Error Source              →  Error Type              →  Response
─────────────────────────────────────────────────────────────────────────────
Invalid API Key          →  AuthenticationError     →  401 Unauthorized
Key expired              →  AuthenticationError     →  401 Unauthorized
Budget exceeded          →  BudgetExceededError     →  400 Bad Request
Rate limit exceeded      →  RateLimitError          →  429 Too Many Requests
Model not in key allow   →  PermissionDeniedError   →  403 Forbidden
Model group not found    →  ModelNotFoundError      →  404 Not Found
Provider timeout         →  TimeoutError            →  408 Request Timeout
Provider 5xx             →  ServiceUnavailableError →  503 Service Unavailable
Provider 4xx             →  InvalidRequestError     →  400 Bad Request
Unknown error            →  ProxyError              →  500 Server Error

Retry Logic:
┌─────────────────────────────────────────────────────────┐
│ • Retry on: Timeout, 503, Connection errors             │
│ • No retry on: 400, 401, 403, 404, 429 (rate limit)     │
│ • Max retries: router_settings.num_retries              │
│ • Backoff: router_settings.retry_after seconds          │
│ • Fallback: Try next deployment in model group          │
└─────────────────────────────────────────────────────────┘
```

---

## Section 5: Configuration Schema

### 5.1 YAML Configuration Structure

```yaml
# =============================================================================
# DeltaLLM Configuration Schema (Phase 1)
# =============================================================================

# Model Registry - defines available models and their deployments
model_list:
  - model_name: gpt-4                    # Public model group name
    litellm_params:
      model: openai/gpt-4               # provider/model_id
      api_key: os.environ/OPENAI_API_KEY
      api_base: https://api.openai.com/v1  # optional override
      timeout: 300                      # per-deployment timeout
      rpm: 10000                        # deployment RPM capacity
      tpm: 1000000                      # deployment TPM capacity
      weight: 1                         # routing weight
      tags: ["premium", "us-region"]    # P1: tag-based routing
    model_info:
      input_cost_per_token: 0.00003
      output_cost_per_token: 0.00006
      
  - model_name: gpt-4                    # Same group, different deployment
    litellm_params:
      model: azure/gpt-4-deployment
      api_base: https://my-azure.openai.azure.com/
      api_key: os.environ/AZURE_API_KEY
      api_version: "2024-02-15-preview"
      
  - model_name: claude-3-sonnet
    litellm_params:
      model: anthropic/claude-3-sonnet-20240229
      api_key: os.environ/ANTHROPIC_API_KEY

# =============================================================================
# Router Settings
# =============================================================================
router_settings:
  routing_strategy: simple-shuffle       # simple-shuffle | least-busy
  num_retries: 3                         # retries per deployment
  retry_after: 1                         # seconds between retries
  timeout: 600                           # global timeout (seconds)
  cooldown_time: 60                      # seconds after failure
  allowed_fails: 0                       # failures before cooldown
  enable_pre_call_checks: false          # P1: check RPM/TPM before routing
  model_group_alias:                     # P1: model name aliases
    gpt4: gpt-4
    claude: claude-3-sonnet

# =============================================================================
# LiteLLM Settings
# =============================================================================
litellm_settings:
  drop_params: false                     # drop unsupported params vs error
  request_timeout: 600                   # default request timeout
  num_retries: null                      # default retries
  
  # Callbacks / Logging
  success_callback: ["prometheus"]       # prometheus, langfuse, etc
  failure_callback: []
  callbacks: []                          # custom callback classes
  
  # Budget & Spend
  budget_duration: "30d"                 # default budget reset period
  
  # Cache (P1)
  cache: false
  cache_params:
    type: redis                          # redis | local
    host: localhost
    port: 6379
    password: os.environ/REDIS_PASSWORD
    ttl: 3600
  
  # Fallbacks (P1)
  fallbacks:
    - gpt-4: [gpt-3.5-turbo]
  context_window_fallbacks:
    - gpt-4: [gpt-4-32k]
  
  # Message Logging (P1)
  turn_off_message_logging: false

# =============================================================================
# General Settings
# =============================================================================
general_settings:
  # Security
  master_key: os.environ/LITELLM_MASTER_KEY
  litellm_key_header_name: "Authorization"  # custom auth header
  salt_key: os.environ/LITELLM_SALT_KEY     # key hashing salt
  
  # Database
  database_url: os.environ/DATABASE_URL
  database_connection_pool_limit: 100
  database_connection_timeout: 60
  
  # Redis (for shared state)
  redis_host: localhost
  redis_port: 6379
  redis_password: os.environ/REDIS_PASSWORD
  redis_url: null                        # takes precedence if set
  
  # Alerting (P1)
  alerting: []                           # slack, email, webhook
  alerting_threshold: 300                # slow request threshold (sec)
  alert_types:
    - budget_alerts
    - llm_exceptions
  
  # Health Checks (P1)
  background_health_checks: false
  health_check_interval: 300
  health_check_model: null               # lightweight model for probes
  
  # Misc
  allowed_ips: []                        # IP allowlist
  allowed_origins: ["*"]                 # CORS origins
  public_routes: []                      # unauthenticated routes
  max_request_size_mb: null

# =============================================================================
# Environment Variables
# =============================================================================
environment_variables:
  # Provider API Keys (can also use os.environ/ in model_list)
  OPENAI_API_KEY: null
  ANTHROPIC_API_KEY: null
  AZURE_API_KEY: null
  
  # Database & Cache
  DATABASE_URL: null
  REDIS_PASSWORD: null
  
  # Security
  LITELLM_MASTER_KEY: null
  LITELLM_SALT_KEY: null
```

### 5.2 Configuration Validation Rules

| Rule | Level | Description |
|------|-------|-------------|
| `model_name` required | Error | Every model_list entry must have model_name |
| `litellm_params.model` required | Error | Must specify provider/model_id |
| Duplicate tokens | Error | Cannot have duplicate virtual keys |
| Invalid `routing_strategy` | Error | Must be one of: simple-shuffle, least-busy |
| Missing master_key | Warning | Admin APIs will be insecure |
| Missing database_url | Warning | Keys/users not persisted (in-memory only) |
| Missing Redis | Info | Rate limiting per-instance, no shared state |

---

## Section 6: Integration Points

### 6.1 External Service Interfaces

```python
# ============================================================================
# Provider Integration
# ============================================================================

# OpenAI Provider
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_HEADERS = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

# Groq Provider  
AZURE_BASE_URL = "https://{resource}.openai.azure.com/openai/deployments/{deployment}"
AZURE_HEADERS = {
    "api-key": api_key,
    "Content-Type": "application/json"
}
AZURE_QUERY_PARAMS = {"api-version": api_version}

# Anthropic Provider
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_HEADERS = {
    "x-api-key": api_key,
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json"
}

# ============================================================================
# Redis Integration
# ============================================================================

REDIS_OPERATIONS = {
    "get_key_cache": ["GET", "key:{hash}"],
    "set_key_cache": ["SETEX", "key:{hash}", "ttl", "json_data"],
    "incr_ratelimit": ["INCR", "ratelimit:{scope}:{id}:{window}"],
    "expire_ratelimit": ["EXPIRE", "ratelimit:{scope}:{id}:{window}", "window_secs"],
    "set_cooldown": ["SETEX", "deployment:cooldown:{id}", "duration", "1"],
    "get_cooldown": ["GET", "deployment:cooldown:{id}"],
    "incr_parallel": ["INCR", "parallel:{scope}:{id}"],
    "decr_parallel": ["DECR", "parallel:{scope}:{id}"],
}

# ============================================================================
# Prometheus Integration
# ============================================================================

PROMETHEUS_METRICS = {
    "requests_total": "Counter: litellm_requests_total",
    "request_latency": "Histogram: litellm_request_latency_seconds",
    "llm_api_latency": "Histogram: litellm_llm_api_latency_seconds", 
    "failures_total": "Counter: litellm_request_failures_total",
    "input_tokens": "Counter: litellm_input_tokens_total",
    "output_tokens": "Counter: litellm_output_tokens_total",
    "spend_total": "Counter: litellm_spend_total",
}

PROMETHEUS_LABELS = ["model", "api_provider", "user", "team", "api_key", "status_code"]
```

### 6.2 Internal Module Contracts

```python
# ============================================================================
# Request Context (passed through middleware chain)
# ============================================================================

class RequestContext:
    """Mutable context attached to FastAPI request.state"""
    request_id: str                    # UUID for tracing
    start_time: datetime              # Request start timestamp
    
    # Auth layer populates
    user_api_key: UserAPIKeyAuth      # Validated key data
    
    # Routing layer populates
    model_group: str                  # Resolved model group
    deployment: Dict[str, Any]        # Selected deployment config
    
    # Provider layer populates
    provider_response_time: float     # LLM API latency
    
    # Post-processing populates
    usage: Usage                      # Token usage
    cost: float                       # Calculated cost
    cache_hit: bool                   # Whether cache was used


# ============================================================================
# Callback Payload Schema
# ============================================================================

LOGGING_PAYLOAD = {
    "model": str,                     # Requested model
    "messages": List[Dict],           # Input messages
    "response_obj": Dict,             # Full response
    "response_cost": float,           # Calculated cost
    "usage": Dict,                    # Token counts
    "call_type": str,                 # "completion", "embedding"
    "stream": bool,                   # Was streaming
    "litellm_call_id": str,           # Unique call ID
    "request_id": str,                # Proxy request ID
    "api_key": str,                   # Hashed key
    "user": str,                      # User ID
    "team_id": str,                   # Team ID
    "metadata": Dict,                 # Request metadata
    "cache_hit": bool,                # Cache status
    "start_time": datetime,
    "end_time": datetime,
}
```

---

## Section 7: Worktree Breakdown

### 7.1 Phase 1 Worktrees

| Worktree | Scope | Dependencies | Estimated LOC |
|----------|-------|--------------|---------------|
| `core-db` | Database models, migrations, repositories | None | 500 |
| `core-auth` | Virtual key management, master key auth | core-db | 600 |
| `core-ratelimit` | RPM/TPM/budget enforcement | core-db, Redis | 500 |
| `core-router` | Model registry, routing strategies | core-db, Redis | 600 |
| `core-provider` | Provider adapters (OpenAI/Azure/Anthropic) | core-router | 800 |
| `core-api` | FastAPI app, middleware, endpoints | All above | 700 |
| `core-obs` | Prometheus metrics, basic callbacks | core-api | 400 |
| `core-cli` | CLI tool, config loading | core-api | 300 |

### 7.2 Worktree Specifications

#### worktree-core-db
```yaml
Scope: Database schema and data access layer
Inputs: Prisma schema definition
Outputs:
  - prisma/schema.prisma
  - src/db/client.py          # Prisma client singleton
  - src/db/repositories/
      - key_repository.py     # Key CRUD operations
      - user_repository.py    # User CRUD
      - team_repository.py    # Team CRUD
      - spend_repository.py   # Spend logging
  - migrations/
Acceptance Criteria:
  - All CRUD operations tested
  - Connection pooling configured
  - Migration scripts runnable
```

#### worktree-core-auth
```yaml
Scope: Authentication and virtual key management
Inputs:
  - Database schema from core-db
  - Redis connection config
Outputs:
  - src/auth/
      - virtual_keys.py       # Key validation service
      - master_key.py         # Admin auth middleware
      - models.py             # UserAPIKeyAuth Pydantic model
  - src/admin/
      - key_management.py     # Key CRUD endpoints
Acceptance Criteria:
  - Virtual key validation < 10ms (cache hit)
  - Key generation returns secure random token
  - Master key required for all /admin/* routes
  - Key hashing uses SHA-256 with salt
Integration Points:
  - Calls: db.repositories.key_repository
  - Called by: middleware.auth_middleware
```

#### worktree-core-ratelimit
```yaml
Scope: Rate limiting and budget enforcement
Inputs:
  - Redis connection config
  - UserAPIKeyAuth from core-auth
Outputs:
  - src/rate_limit/
      - counters.py           # Redis-backed counters
      - budget.py             # Budget checking service
      - middleware.py         # FastAPI rate limit middleware
Acceptance Criteria:
  - Rate limit enforcement accurate ±1%
  - Sliding window implementation
  - Budget checks block requests when exceeded
  - Parallel request tracking
Integration Points:
  - Uses: Redis client
  - Called by: middleware chain
```

#### worktree-core-router
```yaml
Scope: Model registry and deployment routing
Inputs:
  - Database models
  - Redis for cooldown state
Outputs:
  - src/router/
      - registry.py           # Model group registry
      - strategies.py         # Routing strategy implementations
      - cooldown.py           # Cooldown management
      - middleware.py         # Routing middleware
Acceptance Criteria:
  - Simple shuffle selects randomly from healthy deployments
  - Least-busy tracks active requests
  - Cooldown excludes deployments from selection
  - Fallback chains work across deployments
Integration Points:
  - Uses: Redis (cooldown state)
  - Called by: middleware chain
  - Calls: Provider adapter
```

#### worktree-core-provider
```yaml
Scope: Provider adapters and request translation
Inputs:
  - Provider configurations
Outputs:
  - src/providers/
      - base.py               # ProviderAdapter ABC
      - openai.py             # OpenAI adapter
      - azure.py              # Groq adapter
      - anthropic.py          # Anthropic adapter
      - registry.py           # Provider registry/factory
      - errors.py             # Error mapping
Acceptance Criteria:
  - All 3 providers translate requests correctly
  - Streaming supported for all providers
  - Error mapping to standard types
  - Retry logic with configurable attempts
Integration Points:
  - Called by: API handlers
  - Uses: httpx for HTTP requests
```

#### worktree-core-api
```yaml
Scope: FastAPI application, middleware chain, endpoints
Inputs:
  - All other core worktrees
Outputs:
  - src/api/
      - app.py                # FastAPI app factory
      - middleware.py         # Middleware chain setup
      - dependencies.py       # FastAPI dependencies
      - routes/
          - chat.py           # /v1/chat/completions
          - embeddings.py     # /v1/embeddings
          - models.py         # /v1/models
          - health.py         # Health endpoints
  - src/models/
      - requests.py           # Pydantic request models
      - responses.py          # Pydantic response models
Acceptance Criteria:
  - All P0 endpoints functional
  - Middleware chain executes in correct order
  - Request context propagated correctly
  - OpenAPI docs at /docs
Integration Points:
  - Integrates: auth, ratelimit, router, provider
```

#### worktree-core-obs
```yaml
Scope: Observability and metrics
Inputs:
  - Request context from core-api
Outputs:
  - src/observability/
      - prometheus.py         # Prometheus metrics exporter
      - callbacks.py          # Callback system base
      - logging.py            # Structured logging setup
Acceptance Criteria:
  - /metrics endpoint returns Prometheus format
  - All listed metrics emitted with correct labels
  - Callbacks fire asynchronously without blocking
Integration Points:
  - Called by: post-processing middleware
  - Integrates: prometheus-client
```

#### worktree-core-cli
```yaml
Scope: CLI tool and configuration management
Inputs:
  - Config schema definition
Outputs:
  - src/cli/
      - main.py               # CLI entry point
      - config.py             # Config loading/validation
  - deltallm.py               # Main executable
Acceptance Criteria:
  - deltallm --config starts server
  - Config validation fails fast with clear errors
  - Environment variable interpolation works
  - --test validates model connectivity
Integration Points:
  - Starts: FastAPI application
```

### 7.3 Integration Checkpoints

| Checkpoint | Description | Verification |
|------------|-------------|--------------|
| CP1 | Auth + DB integration | Key validation reads from DB, caches in memory |
| CP2 | Rate limit + Redis | Counter increments visible in Redis |
| CP3 | Router + Provider | Request routes to correct provider |
| CP4 | Full middleware chain | End-to-end request succeeds |
| CP5 | Metrics + Prometheus | /metrics shows request counts |

---

## Section 8: Tech Stack Recommendations

### 8.1 Core Dependencies

| Category | Technology | Version | Justification |
|----------|-----------|---------|---------------|
| Framework | FastAPI | ^0.109 | High performance, auto OpenAPI docs, async native |
| Server | Uvicorn | ^0.27 | ASGI server with HTTP/2 support |
| ORM | Prisma Client Python | ^0.12 | Type-safe, async, migration support |
| HTTP Client | httpx | ^0.26 | Async HTTP/2, streaming support |
| Validation | Pydantic | ^2.5 | Fast validation, OpenAPI generation |
| Caching | redis-py | ^5.0 | Async Redis client, cluster support |
| Metrics | prometheus-client | ^0.19 | Standard Prometheus metrics |
| CLI | typer | ^0.9 | Type-based CLI, great DX |
| Config | PyYAML | ^6.0 | YAML parsing with safe loader |
| Testing | pytest | ^7.4 | Async test support, fixtures |
| Linting | ruff | ^0.2 | Fast Python linter/formatter |

### 8.2 Project Structure

```
deltallm/
├── pyproject.toml              # Dependencies, scripts
├── README.md
├── config.yaml                 # Example configuration
├── prisma/
│   └── schema.prisma           # Database schema
├── src/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py              # FastAPI factory
│   │   ├── middleware.py       # Middleware chain
│   │   ├── dependencies.py     # FastAPI deps
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── chat.py
│   │       ├── embeddings.py
│   │       ├── models.py
│   │       ├── health.py
│   │       └── admin/
│   │           ├── __init__.py
│   │           ├── keys.py
│   │           ├── users.py
│   │           └── teams.py
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── virtual_keys.py
│   │   ├── master_key.py
│   │   └── models.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── keys.py
│   │       ├── users.py
│   │       ├── teams.py
│   │       └── spend.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── requests.py
│   │   ├── responses.py
│   │   └── errors.py
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── openai.py
│   │   ├── azure.py
│   │   ├── anthropic.py
│   │   ├── registry.py
│   │   └── errors.py
│   ├── rate_limit/
│   │   ├── __init__.py
│   │   ├── counters.py
│   │   ├── budget.py
│   │   └── middleware.py
│   ├── router/
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   ├── strategies.py
│   │   ├── cooldown.py
│   │   └── middleware.py
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── prometheus.py
│   │   ├── callbacks.py
│   │   └── logging.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   ├── validation.py
│   │   └── models.py
│   └── cli/
│       ├── __init__.py
│       └── main.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_rate_limit.py
│   │   ├── test_router.py
│   │   └── test_providers.py
│   ├── integration/
│   │   ├── test_api.py
│   │   └── test_end_to_end.py
│   └── fixtures/
│       └── config.yaml
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

### 8.3 Testing Strategy

| Level | Coverage Target | Tools |
|-------|-----------------|-------|
| Unit | 80%+ business logic | pytest, pytest-asyncio, pytest-cov |
| Integration | API endpoints, DB operations | pytest, testcontainers |
| E2E | Full request flow | pytest, Docker Compose |

**Test Patterns:**
- Mock external providers using `respx` or `pytest-httpx`
- Use testcontainers for PostgreSQL and Redis in integration tests
- Property-based testing for input validation using `hypothesis`

### 8.4 Deployment Configurations

**Single Instance (Development):**
```yaml
# Minimal config, no Redis/Postgres required
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: os.environ/OPENAI_API_KEY
```

**Multi-Instance (Production):**
```yaml
# Redis + PostgreSQL required
general_settings:
  database_url: postgresql://...
  redis_url: redis://...
router_settings:
  routing_strategy: least-busy
litellm_settings:
  cache: true
  success_callback: ["prometheus"]
```

---

## Appendix A: Phase 1 Feature Detail

### A.1 P0 Requirements (Must Have)

| Feature | Status | Owner Worktree | PRD Section |
|---------|--------|----------------|-------------|
| OpenAI-compatible endpoints | Planned | core-api | 5.1, 9.2 |
| Provider abstraction (3 providers) | Planned | core-provider | 5.2, 9.1 |
| Model registry + groups | Planned | core-router | 5.3 |
| Request lifecycle pipeline | Planned | core-api | 5.4 |
| Virtual key auth | Planned | core-auth | 5.5 |
| Rate limits (RPM/TPM) | Planned | core-ratelimit | 5.7 |
| Budget enforcement | Planned | core-ratelimit | 5.7 |
| Basic routing (simple-shuffle) | Planned | core-router | 6.1 |
| Failover/fallback chains | Planned | core-router | 6.1 |
| Cooldown mechanisms | Planned | core-router | 6.2 |
| Prometheus metrics | Planned | core-obs | 7.5 |
| Token cost calculation | Planned | core-obs | 7.6 |
| Spend tracking | Planned | core-db | 7.7 |
| CLI tool | Planned | core-cli | 9.5 |
| Docker deployment | Planned | - | 8.1 |

### A.2 P1 Requirements (Should Have)

| Feature | Status | Owner Worktree | PRD Section |
|---------|--------|----------------|-------------|
| Least-busy routing | Planned | core-router | 6.1 |
| Redis caching | Planned | core-router | 6.4 |
| Cache key composition | Planned | core-router | 6.5 |
| Background health checks | Planned | core-router | 6.3 |
| Custom guardrail framework | Planned | Phase 3 | 6.9 |
| Langfuse integration | Planned | core-obs | 7.2 |
| Budget alerts | Planned | core-obs | 7.9 |
| Spend query APIs | Planned | core-db | 7.10 |
| Model group aliases | Planned | core-router | 5.3 |
| Tag-based routing | Planned | core-router | 6.1 |

### A.3 Out of Scope (Phase 1)

- Semantic cache (Qdrant) → Phase 3
- Built-in guardrail integrations → Phase 3
- Advanced routing (latency/cost-based) → Phase 2
- SSO/JWT auth → Phase 5
- Admin UI dashboard → Phase 5
- Additional provider adapters (Bedrock, etc.) → Phase 6
- Pass-through endpoints → Phase 6
- Enterprise RBAC → Phase 6

---

## Appendix B: Acceptance Criteria Matrix

### B.1 Core Proxy

| Criterion | Test Method | Pass Condition |
|-----------|-------------|----------------|
| OpenAI SDK compatibility | Integration test | Python/JS SDK works with base_url change |
| Virtual key validation | Unit test | < 10ms with cache hit |
| Rate limit enforcement | Load test | Accurate to ±1% |
| Budget blocking | Integration test | Requests blocked when budget exceeded |
| Provider error mapping | Unit test | All provider errors map to standard types |

### B.2 Routing

| Criterion | Test Method | Pass Condition |
|-----------|-------------|----------------|
| Simple shuffle distribution | Unit test | Random distribution within 5% expected |
| Cooldown activation | Integration test | Failed deployment excluded for cooldown duration |
| Fallback chains | Integration test | Fallbacks tried in order on failure |
| Health check updates | Integration test | Unhealthy deployments excluded from routing |

### B.3 Observability

| Criterion | Test Method | Pass Condition |
|-----------|-------------|----------------|
| Prometheus metrics | Integration test | /metrics returns valid Prometheus format |
| Request correlation | Integration test | x-litellm-call-id header present |
| Cost calculation | Unit test | Matches expected cost for known usage |
| Spend logging | Integration test | Every request creates spend log entry |

---

*End of Phase 1 Core Foundation Technical Specification*
