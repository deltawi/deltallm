"""Ollama provider adapter.

Ollama is a tool for running LLMs locally with a simple, self-hosted API.
It provides an easy way to run open-source models like Llama, Mistral, etc.

Key features:
- Local deployment (no cloud required)
- Simple REST API
- Easy model management (pull, list, delete)
- Supports most popular open-source models

API Reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    DeltaMessage,
)
from deltallm.exceptions import (
    APIError,
    APIConnectionError,
)

from .base import BaseProvider, ModelInfo, ProviderCapabilities
from .registry import register_provider


@register_provider("ollama", models=[
    # Models available in Ollama
    "llama2",
    "llama2:7b",
    "llama2:13b",
    "llama2:70b",
    "llama3",
    "llama3:8b",
    "llama3:70b",
    "llama3.1",
    "llama3.1:8b",
    "llama3.1:70b",
    "llama3.1:405b",
    "mistral",
    "mixtral",
    "phi3",
    "phi3:mini",
    "phi3:small",
    "phi3:medium",
    "gemma",
    "gemma:2b",
    "gemma:4b",
    "gemma2",
    "gemma2:2b",
    "gemma2:9b",
    "gemma2:27b",
    "qwen",
    "qwen:7b",
    "qwen:14b",
    "qwen:72b",
    "codellama",
    "codellama:7b",
    "codellama:13b",
    "codellama:34b",
    "deepseek-coder",
    "deepseek-coder:1.3b",
    "deepseek-coder:6.7b",
    "deepseek-coder:33b",
])
class OllamaProvider(BaseProvider):
    """Ollama provider adapter.
    
    Ollama provides a simple REST API for running LLMs locally.
    The API is not OpenAI-compatible, so we need to transform requests/responses.
    
    Configuration:
        api_base: URL of the Ollama server (default: http://localhost:11434)
        api_key: Not used (Ollama doesn't require authentication by default)
    
    Example:
        provider = OllamaProvider(api_base="http://localhost:11434")
    """
    
    provider_name = "ollama"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=True,  # Ollama supports embeddings via /api/embeddings
        images=False,  # Some models support vision, but API differs
        audio=False,
        tools=False,  # Limited tool support in Ollama
        vision=False,
        json_mode=False,
        supported_model_types=["chat", "embedding"],
    )
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the Ollama provider.
        
        Args:
            api_key: Not used (Ollama doesn't require authentication)
            api_base: URL of the Ollama server (default: http://localhost:11434)
        """
        super().__init__(api_key, api_base)
        self.api_base = api_base or "http://localhost:11434"
    
    def _get_client(self) -> httpx.AsyncClient:
        """Get configured HTTP client."""
        return httpx.AsyncClient(
            base_url=self.api_base,
            headers={"Content-Type": "application/json"},
            timeout=300.0,  # Ollama can take time for first request (model loading)
        )
    
    def _handle_error(self, exc: httpx.HTTPStatusError) -> None:
        """Handle HTTP errors."""
        try:
            body = exc.response.json()
            message = body.get("error", str(exc))
        except Exception:
            body = None
            message = str(exc)
        
        raise APIError(
            f"Ollama API error: {message}",
            body=body,
        )
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Execute a chat completion request."""
        client = self._get_client()
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        try:
            response = await client.post(
                "/api/chat",
                json=self.transform_request(request),
            )
            response.raise_for_status()
            data = response.json()
            
            return self.transform_response(data, request.model)
            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.ConnectError as e:
            raise APIConnectionError(
                f"Failed to connect to Ollama server at {client.base_url}. "
                f"Make sure Ollama is running: {e}"
            )
        except Exception as e:
            raise APIError(f"Ollama request failed: {e}")
        finally:
            await client.aclose()
    
    async def chat_completion_stream(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion request."""
        client = self._get_client()
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        req_body = self.transform_request(request)
        req_body["stream"] = True
        
        try:
            async with client.stream(
                "POST",
                "/api/chat",
                json=req_body,
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    
                    try:
                        import json
                        chunk = json.loads(line)
                        transformed = self.transform_stream_chunk(chunk, request.model)
                        if transformed:
                            yield transformed
                            
                            # Check if this is the final message
                            if chunk.get("done", False):
                                break
                    except json.JSONDecodeError:
                        continue
                        
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.ConnectError as e:
            raise APIConnectionError(
                f"Failed to connect to Ollama server at {client.base_url}: {e}"
            )
        finally:
            await client.aclose()
    
    async def embedding(
        self,
        request: Any,  # EmbeddingRequest
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> Any:  # EmbeddingResponse
        """Execute an embedding request."""
        client = self._get_client()
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        try:
            response = await client.post(
                "/api/embeddings",
                json={
                    "model": request.model,
                    "prompt": request.input if isinstance(request.input, str) else request.input[0],
                },
            )
            response.raise_for_status()
            data = response.json()
            
            from deltallm.types import EmbeddingResponse, Embedding
            
            return EmbeddingResponse(
                model=request.model,
                data=[
                    Embedding(
                        embedding=data.get("embedding", []),
                        index=0,
                        object="embedding",
                    )
                ],
            )
            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.ConnectError as e:
            raise APIConnectionError(
                f"Failed to connect to Ollama server: {e}"
            )
        finally:
            await client.aclose()
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform OpenAI-style request to Ollama format.
        
        Ollama API format:
        {
            "model": "llama3",
            "messages": [
                {"role": "user", "content": "Hello!"}
            ],
            "stream": false,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                ...
            }
        }
        """
        # Convert OpenAI messages to Ollama format
        ollama_messages = []
        for msg in request.messages:
            ollama_msg = {
                "role": msg.role,
                "content": msg.content or "",
            }
            # Ollama doesn't use 'name' field, so we ignore it
            ollama_messages.append(ollama_msg)
        
        body = {
            "model": request.model,
            "messages": ollama_messages,
        }
        
        # Add options (Ollama puts these in an "options" object)
        options = {}
        
        if request.temperature is not None:
            options["temperature"] = request.temperature
        
        if request.top_p is not None:
            options["top_p"] = request.top_p
        
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        elif request.max_completion_tokens is not None:
            options["num_predict"] = request.max_completion_tokens
        
        if request.stop is not None:
            if isinstance(request.stop, list):
                options["stop"] = request.stop
            else:
                options["stop"] = [request.stop]
        
        if request.presence_penalty is not None:
            options["presence_penalty"] = request.presence_penalty
        
        if request.frequency_penalty is not None:
            options["frequency_penalty"] = request.frequency_penalty
        
        if options:
            body["options"] = options
        
        if request.stream:
            body["stream"] = True
        
        return body
    
    def transform_response(self, response: dict[str, Any], model: str) -> CompletionResponse:
        """Transform Ollama response to OpenAI format.
        
        Ollama response format:
        {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you today?"
            },
            "done": true,
            "total_duration": 1234567890,
            "load_duration": 1234567890,
            "prompt_eval_count": 10,
            "prompt_eval_duration": 1234567890,
            "eval_count": 20,
            "eval_duration": 1234567890
        }
        """
        import time
        from deltallm.types import CompletionChoice, Usage
        
        message = response.get("message", {})
        
        choices = [CompletionChoice(
            index=0,
            message={
                "role": message.get("role", "assistant"),
                "content": message.get("content", ""),
            },
            finish_reason="stop" if response.get("done", False) else None,
        )]
        
        # Calculate tokens from Ollama's eval counts
        prompt_tokens = response.get("prompt_eval_count", 0)
        completion_tokens = response.get("eval_count", 0)
        
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        
        return CompletionResponse(
            id=f"ollama-{response.get('created_at', 'unknown')}",
            model=response.get("model", model),
            created=int(time.time()),
            choices=choices,
            usage=usage,
        )
    
    def transform_stream_chunk(self, chunk: dict[str, Any], model: str) -> Optional[StreamChunk]:
        """Transform Ollama stream chunk to OpenAI format.
        
        Ollama streaming format:
        {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "Hello"
            },
            "done": false
        }
        """
        import time
        
        if not chunk or not isinstance(chunk, dict):
            return None
        
        message = chunk.get("message", {})
        
        from deltallm.types import StreamChoice
        
        return StreamChunk(
            id=f"ollama-{chunk.get('created_at', 'chunk')}",
            model=chunk.get("model", model),
            created=int(time.time()),
            choices=[StreamChoice(
                index=0,
                delta=DeltaMessage(
                    role=message.get("role"),
                    content=message.get("content"),
                ),
                finish_reason="stop" if chunk.get("done", False) else None,
            )],
        )
    
    def get_model_info(self, model: str) -> ModelInfo:
        """Get information about a model."""
        # Common Ollama model configurations
        model_configs = {
            "llama3": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "llama3:8b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "llama3:70b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "llama3.1": {"max_tokens": 131072, "max_input_tokens": 131072, "max_output_tokens": 131072},
            "llama3.1:8b": {"max_tokens": 131072, "max_input_tokens": 131072, "max_output_tokens": 131072},
            "llama3.1:70b": {"max_tokens": 131072, "max_input_tokens": 131072, "max_output_tokens": 131072},
            "llama3.1:405b": {"max_tokens": 131072, "max_input_tokens": 131072, "max_output_tokens": 131072},
            "llama2": {"max_tokens": 4096, "max_input_tokens": 4096, "max_output_tokens": 4096},
            "llama2:7b": {"max_tokens": 4096, "max_input_tokens": 4096, "max_output_tokens": 4096},
            "llama2:13b": {"max_tokens": 4096, "max_input_tokens": 4096, "max_output_tokens": 4096},
            "llama2:70b": {"max_tokens": 4096, "max_input_tokens": 4096, "max_output_tokens": 4096},
            "mistral": {"max_tokens": 32768, "max_input_tokens": 32768, "max_output_tokens": 32768},
            "mixtral": {"max_tokens": 32768, "max_input_tokens": 32768, "max_output_tokens": 32768},
            "phi3": {"max_tokens": 128000, "max_input_tokens": 128000, "max_output_tokens": 128000},
            "phi3:mini": {"max_tokens": 128000, "max_input_tokens": 128000, "max_output_tokens": 128000},
            "phi3:small": {"max_tokens": 128000, "max_input_tokens": 128000, "max_output_tokens": 128000},
            "phi3:medium": {"max_tokens": 128000, "max_input_tokens": 128000, "max_output_tokens": 128000},
            "gemma": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "gemma:2b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "gemma2": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "gemma2:2b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "gemma2:9b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "gemma2:27b": {"max_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
            "qwen": {"max_tokens": 32768, "max_input_tokens": 32768, "max_output_tokens": 32768},
            "codellama": {"max_tokens": 16384, "max_input_tokens": 16384, "max_output_tokens": 16384},
            "deepseek-coder": {"max_tokens": 16384, "max_input_tokens": 16384, "max_output_tokens": 16384},
        }
        
        config = model_configs.get(model, {
            "max_tokens": 4096,
            "max_input_tokens": 4096,
            "max_output_tokens": 4096,
        })
        
        # Ollama models are self-hosted, so cost is $0
        return ModelInfo(
            id=model,
            name=model,
            max_tokens=config["max_tokens"],
            max_input_tokens=config["max_input_tokens"],
            max_output_tokens=config["max_output_tokens"],
            input_cost_per_token=0.0,  # Self-hosted = $0
            output_cost_per_token=0.0,  # Self-hosted = $0
            supports_vision=False,
            supports_tools=False,
            supports_streaming=True,
            provider="ollama",
            mode="chat",
        )
