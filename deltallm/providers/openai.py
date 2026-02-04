"""OpenAI provider adapter."""

import time
import uuid
from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    CompletionChoice,
    StreamChunk,
    StreamChoice,
    DeltaMessage,
    Usage,
    EmbeddingRequest,
    EmbeddingResponse,
    Embedding,
)
from deltallm.exceptions import (
    APIError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
    map_http_status_to_error,
)
from deltallm.utils.pricing import get_model_info

from .base import BaseProvider, ModelInfo, ProviderCapabilities
from .registry import register_provider


@register_provider("openai", models=[
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4-turbo-preview",
    "gpt-4",
    "gpt-4-32k",
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-16k",
    "o1",
    "o1-preview",
    "o1-mini",
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
])
class OpenAIProvider(BaseProvider):
    """OpenAI API provider adapter."""
    
    provider_name = "openai"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=True,
        images=True,
        audio=True,
        tools=True,
        vision=True,
        json_mode=True,
        supported_model_types=[
            "chat",
            "embedding",
            "image_generation",
            "audio_transcription",
            "audio_speech",
            "moderation",
        ],
    )
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        api_base: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.organization = organization
        self.api_base = api_base or "https://api.openai.com/v1"
    
    def _get_client(self, api_key: Optional[str] = None) -> httpx.AsyncClient:
        """Get configured HTTP client."""
        key = api_key or self.api_key
        if not key:
            raise AuthenticationError("OpenAI API key is required")
        
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        
        return httpx.AsyncClient(
            base_url=self.api_base,
            headers=headers,
            timeout=60.0,
        )
    
    def _handle_error(self, exc: httpx.HTTPStatusError) -> None:
        """Handle HTTP errors."""
        status_code = exc.response.status_code
        
        try:
            body = exc.response.json()
            message = body.get("error", {}).get("message", str(exc))
        except Exception:
            body = None
            message = str(exc)
        
        # Check for rate limit with retry-after
        if status_code == 429:
            retry_after = exc.response.headers.get("retry-after")
            raise RateLimitError(
                message, 
                retry_after=int(retry_after) if retry_after else None,
                body=body
            )
        
        error = map_http_status_to_error(status_code, message, body)
        raise error
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Execute a chat completion request."""
        client = self._get_client(api_key)
        
        # Update base URL if provided
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        try:
            response = await client.post(
                "/chat/completions",
                json=self.transform_request(request),
            )
            response.raise_for_status()
            data = response.json()
            
            # Add cost calculation
            result = self.transform_response(data, request.model)
            if result._hidden_params is None:
                result._hidden_params = {}
            
            result._hidden_params["response_cost"] = self._calculate_cost(result)
            
            return result
            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.TimeoutException as e:
            raise APITimeoutError(str(e))
        except httpx.ConnectError as e:
            raise APIConnectionError(str(e))
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
        
        req_data = self.transform_request(request)
        req_data["stream"] = True
        
        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=req_data,
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if not line or line.strip() == "":
                        continue
                    
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        
                        if data.strip() == "[DONE]":
                            break
                        
                        try:
                            import json
                            chunk_data = json.loads(data)
                            chunk = self.transform_stream_chunk(chunk_data, request.model)
                            if chunk:
                                yield chunk
                        except json.JSONDecodeError:
                            continue
                            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.TimeoutException as e:
            raise APITimeoutError(str(e))
        except httpx.ConnectError as e:
            raise APIConnectionError(str(e))
        finally:
            await client.aclose()
    
    async def embedding(
        self,
        request: EmbeddingRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> EmbeddingResponse:
        """Execute an embedding request."""
        client = self._get_client(api_key)
        
        if api_base:
            client.base_url = httpx.URL(api_base)
        
        try:
            response = await client.post(
                "/embeddings",
                json={
                    "input": request.input,
                    "model": request.model,
                    "encoding_format": request.encoding_format,
                    "dimensions": request.dimensions,
                    "user": request.user,
                },
            )
            response.raise_for_status()
            data = response.json()
            
            return EmbeddingResponse(
                data=[
                    Embedding(
                        embedding=item["embedding"],
                        index=item["index"],
                    )
                    for item in data["data"]
                ],
                model=data["model"],
                usage=Usage(
                    prompt_tokens=data["usage"]["prompt_tokens"],
                    completion_tokens=0,
                    total_tokens=data["usage"]["total_tokens"],
                ),
            )
            
        except httpx.HTTPStatusError as e:
            self._handle_error(e)
        except httpx.TimeoutException as e:
            raise APITimeoutError(str(e))
        except httpx.ConnectError as e:
            raise APIConnectionError(str(e))
        finally:
            await client.aclose()
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform to OpenAI request format (already OpenAI format)."""
        data: dict[str, Any] = {
            "model": request.model,
            "messages": [
                msg.model_dump(exclude_none=True) for msg in request.messages
            ],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "n": request.n,
            "stream": request.stream,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
        }
        
        if request.max_tokens is not None:
            data["max_tokens"] = request.max_tokens
        
        if request.max_completion_tokens is not None:
            data["max_completion_tokens"] = request.max_completion_tokens
        
        if request.stop is not None:
            data["stop"] = request.stop
        
        if request.logit_bias is not None:
            data["logit_bias"] = request.logit_bias
        
        if request.user is not None:
            data["user"] = request.user
        
        if request.response_format is not None:
            data["response_format"] = request.response_format.model_dump(exclude_none=True)
        
        if request.seed is not None:
            data["seed"] = request.seed
        
        if request.tools is not None:
            data["tools"] = [tool.model_dump(exclude_none=True) for tool in request.tools]
        
        if request.tool_choice is not None:
            data["tool_choice"] = request.tool_choice
        
        if request.parallel_tool_calls is not None:
            data["parallel_tool_calls"] = request.parallel_tool_calls
        
        # Remove None values
        return {k: v for k, v in data.items() if v is not None}
    
    def transform_response(self, response: dict[str, Any], model: str) -> CompletionResponse:
        """Transform OpenAI response (already in OpenAI format)."""
        return CompletionResponse(
            id=response["id"],
            object="chat.completion",
            created=response["created"],
            model=response["model"],
            choices=[
                CompletionChoice(
                    index=choice["index"],
                    message=choice["message"],
                    finish_reason=choice.get("finish_reason"),
                    logprobs=choice.get("logprobs"),
                )
                for choice in response["choices"]
            ],
            usage=Usage(
                prompt_tokens=response["usage"]["prompt_tokens"],
                completion_tokens=response["usage"]["completion_tokens"],
                total_tokens=response["usage"]["total_tokens"],
            ),
            system_fingerprint=response.get("system_fingerprint"),
        )
    
    def transform_stream_chunk(self, chunk: dict[str, Any], model: str) -> Optional[StreamChunk]:
        """Transform OpenAI stream chunk (already in OpenAI format)."""
        if not chunk.get("choices"):
            return None
        
        choices = []
        for choice in chunk["choices"]:
            delta = choice.get("delta", {})
            choices.append(StreamChoice(
                index=choice["index"],
                delta=DeltaMessage(
                    role=delta.get("role"),
                    content=delta.get("content"),
                    tool_calls=delta.get("tool_calls"),
                    function_call=delta.get("function_call"),
                ),
                finish_reason=choice.get("finish_reason"),
                logprobs=choice.get("logprobs"),
            ))
        
        usage = None
        if "usage" in chunk and chunk["usage"]:
            usage = Usage(
                prompt_tokens=chunk["usage"].get("prompt_tokens", 0),
                completion_tokens=chunk["usage"].get("completion_tokens", 0),
                total_tokens=chunk["usage"].get("total_tokens", 0),
            )
        
        return StreamChunk(
            id=chunk["id"],
            object="chat.completion.chunk",
            created=chunk["created"],
            model=chunk["model"],
            choices=choices,
            usage=usage,
            system_fingerprint=chunk.get("system_fingerprint"),
        )
    
    def get_model_info(self, model: str) -> ModelInfo:
        """Get information about an OpenAI model."""
        info = get_model_info(model)
        
        return ModelInfo(
            id=model,
            name=model,
            max_tokens=int(info.get("max_tokens", 8192)),
            max_input_tokens=int(info.get("max_tokens", 8192)),
            max_output_tokens=int(info.get("max_output_tokens", 4096)),
            input_cost_per_token=info.get("input_cost_per_token", 0.0),
            output_cost_per_token=info.get("output_cost_per_token", 0.0),
            supports_vision="vision" in model or model in ["gpt-4o", "gpt-4o-mini"],
            supports_tools=True,
            supports_streaming=True,
            provider="openai",
        )
    
    def _calculate_cost(self, response: CompletionResponse) -> "Decimal":
        """Calculate the cost of the response.

        Returns:
            Cost in USD as Decimal for precision
        """
        from decimal import Decimal
        from deltallm.utils.pricing import calculate_cost

        return calculate_cost(
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
