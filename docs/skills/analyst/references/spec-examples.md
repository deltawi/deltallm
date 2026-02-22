# Technical Specification Examples

## API Contract Example

```python
# Pydantic Request Model
class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[Union[str, ToolChoice]] = None
    response_format: Optional[ResponseFormat] = None
    user: Optional[str] = None  # end-user ID
    metadata: Optional[Dict[str, Any]] = None

# Response Model
class ChatCompletionResponse(BaseModel):
    id: str  # chatcmpl-<uuid>
    object: Literal["chat.completion"] = "chat.completion"
    created: int  # unix timestamp
    model: str
    choices: List[Choice]
    usage: Usage
    system_fingerprint: Optional[str] = None
```

## Error Taxonomy Example

```python
class ProxyError(Exception):
    """Base proxy error with HTTP status mapping"""
    status_code: int = 500
    error_type: str = "server_error"

class AuthenticationError(ProxyError):
    status_code = 401
    error_type = "authentication_error"

class RateLimitError(ProxyError):
    status_code = 429
    error_type = "rate_limit_error"
    retry_after: Optional[int] = None

class BudgetExceededError(ProxyError):
    status_code = 400
    error_type = "budget_exceeded"
```

## Provider Adapter Interface Example

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, AsyncIterator

class ProviderAdapter(ABC):
    """Abstract base for LLM provider adapters"""
    
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
    def map_error(self, provider_error: Exception) -> ProxyError:
        """Map provider-specific errors to standard proxy errors"""
        pass
    
    @abstractmethod
    async def translate_stream(
        self,
        provider_stream: AsyncIterator[Any]
    ) -> AsyncIterator[str]:
        """Translate streaming chunks to SSE format"""
        pass
```

## Request Lifecycle Example

```
Client Request
  |
  v
1. AuthMiddleware.validate_key()
   - Check Redis cache (key_hash -> key_data)
   - Fallback to DB query on cache miss
   - Validate expiry, budget, model access
   - Attach UserAPIKeyAuth to request.state
  |
  v
2. RateLimitMiddleware.check_limits()
   - Increment RPM/TPM counters in Redis
   - Check against key/user/team limits
   - Raise RateLimitError if exceeded
  |
  v
3. Router.select_deployment()
   - Get healthy deployments for model group
   - Apply routing strategy (shuffle/least-busy/etc)
   - Return deployment config
  |
  v
4. ProviderAdapter.execute()
   - Translate request to provider format
   - Execute with retries/fallback
   - Stream or blocking response
  |
  v
5. PostProcessingMiddleware
   - Apply output guardrails
   - Calculate usage/cost
   - Enrich response metadata
  |
  v
6. AsyncLogging.enqueue()
   - Fire-and-forget to Redis queue
   - Background workers write to DB
  |
  v
Client Response
```

## Worktree Task Example

```markdown
### worktree-core-auth

**Scope**: Virtual key management, master key auth, rate limiting

**Inputs**:
- Database schema from worktree-core-db
- Redis connection config

**Deliverables**:
1. `src/auth/virtual_keys.py` - Key validation service
2. `src/auth/master_key.py` - Admin auth middleware
3. `src/rate_limit/counters.py` - Redis-backed limit counters
4. `tests/auth/test_virtual_keys.py` - Unit tests

**Integration Points**:
- Calls: `db.repositories.key_repository` (from core-db)
- Called by: `middleware.auth_middleware` (from core-api)

**Acceptance Criteria**:
- Virtual key validation < 10ms (cache hit)
- Rate limit enforcement accurate ±1%
- Master key required for all /admin/* routes
```
