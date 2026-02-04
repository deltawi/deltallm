"""vLLM provider adapter.

vLLM is a high-throughput and memory-efficient inference engine for LLMs.
It provides an OpenAI-compatible API server that can be self-hosted.

Key features:
- Self-hosted (local or remote)
- OpenAI-compatible API
- High throughput with PagedAttention
- Supports most popular open-source models
"""

from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
)
from deltallm.exceptions import (
    APIError,
    APIConnectionError,
    AuthenticationError,
)

from .base import BaseProvider, ModelInfo, ProviderCapabilities
from .registry import register_provider


@register_provider("vllm", models=[
    # Popular open-source models supported by vLLM
    "meta-llama/Llama-2-7b-chat-hf",
    "meta-llama/Llama-2-13b-chat-hf",
    "meta-llama/Llama-2-70b-chat-hf",
    "meta-llama/Llama-3-8b-chat-hf",
    "meta-llama/Llama-3-70b-chat-hf",
    "mistralai/Mistral-7B-Instruct-v0.1",
    "mistralai/Mistral-7B-Instruct-v0.2",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "mistralai/Mixtral-8x22B-Instruct-v0.1",
    "microsoft/Phi-3-mini-4k-instruct",
    "microsoft/Phi-3-small-8k-instruct",
    "microsoft/Phi-3-medium-4k-instruct",
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2-72B-Instruct",
    "google/gemma-2b-it",
    "google/gemma-7b-it",
    "google/gemma-2-9b-it",
    "google/gemma-2-27b-it",
])
class VLLMProvider(BaseProvider):
    """vLLM provider adapter.
    
    vLLM exposes an OpenAI-compatible API, so we can use similar
    request/response transformations as the OpenAI provider.
    
    Configuration:
        api_base: URL of the vLLM server (default: http://localhost:8000/v1)
        api_key: Optional API key (if vLLM is configured with auth)
    """
    
    provider_name = "vllm"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=False,  # vLLM doesn't support embeddings by default
        images=False,
        audio=False,
        tools=True,  # Some vLLM versions support tools
        vision=False,  # Most vLLM setups don't support vision
        json_mode=True,
        supported_model_types=["chat"],
    )
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the vLLM provider.
        
        Args:
            api_key: Optional API key (if authentication is enabled)
            api_base: URL of the vLLM server (default: http://localhost:8000/v1)
        """
        super().__init__(api_key, api_base)
        self.api_base = api_base or "http://localhost:8000/v1"
    
    def _get_client(self, api_key: Optional[str] = None) -> httpx.AsyncClient:
        """Get configured HTTP client."""
        key = api_key or self.api_key
        
        headers = {
            "Content-Type": "application/json",
        }
        
        if key:
            headers["Authorization"] = f"Bearer {key}"
        
        return httpx.AsyncClient(
            base_url=self.api_base,
            headers=headers,
            timeout=300.0,  # vLLM can take longer for first request (model loading)
        )
    
    def _handle_error(self, exc: httpx.HTTPStatusError) -> None:
        """Handle HTTP errors."""
        try:
            body = exc.response.json()
            message = body.get("message", str(exc))
        except Exception:
            body = None
            message = str(exc)
        
        raise APIError(
            f"vLLM API error: {message}",
            body=body,
        )
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Execute a chat completion request."""
        client = self._get_client(api_key)
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        try:
            response = await client.post(
                "/chat/completions",
                json=self.transform_request(request),
            )
            response.raise_for_status()
            data = response.json()
            
            return self.transform_response(data, request.model)
            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.ConnectError as e:
            raise APIConnectionError(
                f"Failed to connect to vLLM server at {client.base_url}. "
                f"Make sure vLLM is running: {e}"
            )
        except Exception as e:
            raise APIError(f"vLLM request failed: {e}")
        finally:
            await client.aclose()
    
    async def chat_completion_stream(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion request."""
        client = self._get_client(api_key)
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        req_body = self.transform_request(request)
        req_body["stream"] = True
        
        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=req_body,
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    
                    if line.startswith("data: "):
                        line = line[6:]
                    
                    if line == "[DONE]":
                        break
                    
                    try:
                        import json
                        chunk = json.loads(line)
                        transformed = self.transform_stream_chunk(chunk, request.model)
                        if transformed:
                            yield transformed
                    except json.JSONDecodeError:
                        continue
                        
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.ConnectError as e:
            raise APIConnectionError(
                f"Failed to connect to vLLM server at {client.base_url}: {e}"
            )
        finally:
            await client.aclose()
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform OpenAI-style request to vLLM format.
        
        vLLM is OpenAI-compatible, so this is mostly a pass-through.
        """
        body = {
            "model": request.model,
            "messages": [msg.model_dump(exclude_none=True) for msg in request.messages],
        }
        
        # Add optional parameters
        if request.temperature is not None:
            body["temperature"] = request.temperature
        
        if request.top_p is not None:
            body["top_p"] = request.top_p
        
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        elif request.max_completion_tokens is not None:
            body["max_tokens"] = request.max_completion_tokens
        
        if request.stop is not None:
            body["stop"] = request.stop
        
        if request.stream:
            body["stream"] = True
        
        if request.tools:
            body["tools"] = [tool.model_dump(exclude_none=True) for tool in request.tools]
        
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        
        if request.response_format is not None:
            body["response_format"] = request.response_format
        
        if request.presence_penalty is not None:
            body["presence_penalty"] = request.presence_penalty
        
        if request.frequency_penalty is not None:
            body["frequency_penalty"] = request.frequency_penalty
        
        return body
    
    def transform_response(self, response: dict[str, Any], model: str) -> CompletionResponse:
        """Transform vLLM response to OpenAI format.
        
        vLLM is OpenAI-compatible, so this is mostly a pass-through.
        """
        from deltallm.types import CompletionChoice, Usage
        
        choices = []
        for choice in response.get("choices", []):
            message = choice.get("message", {})
            choices.append(CompletionChoice(
                index=choice.get("index", 0),
                message=message,
                finish_reason=choice.get("finish_reason", "stop"),
            ))
        
        usage_data = response.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        
        return CompletionResponse(
            id=response.get("id", "vllm-unknown"),
            model=response.get("model", model),
            choices=choices,
            usage=usage,
            created=response.get("created"),
        )
    
    def transform_stream_chunk(self, chunk: dict[str, Any], model: str) -> Optional[StreamChunk]:
        """Transform vLLM stream chunk to OpenAI format."""
        import time
        
        if not chunk or not isinstance(chunk, dict):
            return None
        
        choices = chunk.get("choices", [])
        if not choices:
            return None
        
        choice = choices[0]
        delta = choice.get("delta", {})
        
        from deltallm.types import StreamChoice, DeltaMessage
        
        return StreamChunk(
            id=chunk.get("id", "vllm-chunk"),
            model=chunk.get("model", model),
            created=int(time.time()),
            choices=[StreamChoice(
                index=choice.get("index", 0),
                delta=DeltaMessage(
                    role=delta.get("role"),
                    content=delta.get("content"),
                    tool_calls=delta.get("tool_calls"),
                ),
                finish_reason=choice.get("finish_reason"),
            )],
        )
    
    def get_model_info(self, model: str) -> ModelInfo:
        """Get information about a model."""
        # Common vLLM model configurations
        model_configs = {
            "meta-llama/Llama-2-7b-chat-hf": {
                "max_tokens": 4096,
                "max_input_tokens": 4096,
                "max_output_tokens": 4096,
            },
            "meta-llama/Llama-2-13b-chat-hf": {
                "max_tokens": 4096,
                "max_input_tokens": 4096,
                "max_output_tokens": 4096,
            },
            "meta-llama/Llama-2-70b-chat-hf": {
                "max_tokens": 4096,
                "max_input_tokens": 4096,
                "max_output_tokens": 4096,
            },
            "meta-llama/Llama-3-8b-chat-hf": {
                "max_tokens": 8192,
                "max_input_tokens": 8192,
                "max_output_tokens": 8192,
            },
            "meta-llama/Llama-3-70b-chat-hf": {
                "max_tokens": 8192,
                "max_input_tokens": 8192,
                "max_output_tokens": 8192,
            },
            "mistralai/Mistral-7B-Instruct-v0.1": {
                "max_tokens": 8192,
                "max_input_tokens": 8192,
                "max_output_tokens": 8192,
            },
            "mistralai/Mistral-7B-Instruct-v0.2": {
                "max_tokens": 32768,
                "max_input_tokens": 32768,
                "max_output_tokens": 32768,
            },
            "mistralai/Mixtral-8x7B-Instruct-v0.1": {
                "max_tokens": 32768,
                "max_input_tokens": 32768,
                "max_output_tokens": 32768,
            },
            "microsoft/Phi-3-mini-4k-instruct": {
                "max_tokens": 4096,
                "max_input_tokens": 4096,
                "max_output_tokens": 4096,
            },
            "Qwen/Qwen2-7B-Instruct": {
                "max_tokens": 32768,
                "max_input_tokens": 32768,
                "max_output_tokens": 32768,
            },
            "google/gemma-2b-it": {
                "max_tokens": 8192,
                "max_input_tokens": 8192,
                "max_output_tokens": 8192,
            },
            "google/gemma-7b-it": {
                "max_tokens": 8192,
                "max_input_tokens": 8192,
                "max_output_tokens": 8192,
            },
        }
        
        config = model_configs.get(model, {
            "max_tokens": 4096,
            "max_input_tokens": 4096,
            "max_output_tokens": 4096,
        })
        
        # vLLM models are self-hosted, so cost is typically $0
        # (users pay for their own infrastructure)
        return ModelInfo(
            id=model,
            name=model.split("/")[-1] if "/" in model else model,
            max_tokens=config["max_tokens"],
            max_input_tokens=config["max_input_tokens"],
            max_output_tokens=config["max_output_tokens"],
            input_cost_per_token=0.0,  # Self-hosted = $0
            output_cost_per_token=0.0,  # Self-hosted = $0
            supports_vision=False,
            supports_tools="llama-3" in model.lower() or "mixtral" in model.lower(),
            supports_streaming=True,
            provider="vllm",
            mode="chat",
        )
