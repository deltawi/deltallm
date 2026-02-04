"""Anthropic provider adapter."""

from typing import Any, AsyncIterator, Optional, Literal
import json
import time

import httpx

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    CompletionChoice,
    StreamChunk,
    StreamChoice,
    DeltaMessage,
    Usage,
    Message,
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
from deltallm.utils.vision import get_image_as_base64

from .base import BaseProvider, ModelInfo, ProviderCapabilities
from .registry import register_provider


@register_provider("anthropic", models=[
    # Claude 3.5 (with prompt caching support)
    "claude-3-5-sonnet",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku",
    "claude-3-5-haiku-20241022",
    # Claude 3 (with prompt caching support)
    "claude-3-opus",
    "claude-3-opus-20240229",
    "claude-3-sonnet",
    "claude-3-sonnet-20240229",
    "claude-3-haiku",
    "claude-3-haiku-20240307",
    # Claude 2 (legacy)
    "claude-2.1",
    "claude-2.0",
    "claude-instant-1.2",
])
class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider adapter."""
    
    provider_name = "anthropic"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=False,
        images=False,
        audio=False,
        tools=True,
        vision=True,
        json_mode=True,
        supported_model_types=["chat"],
    )
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        api_base: Optional[str] = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.api_base = api_base or "https://api.anthropic.com/v1"
    
    def _get_client(self, api_key: Optional[str] = None) -> httpx.AsyncClient:
        """Get configured HTTP client."""
        key = api_key or self.api_key
        if not key:
            raise AuthenticationError("Anthropic API key is required")
        
        return httpx.AsyncClient(
            base_url=self.api_base,
            headers={
                "x-api-key": key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            timeout=60.0,
        )
    
    def _handle_error(self, exc: httpx.HTTPStatusError) -> None:
        """Handle HTTP errors."""
        status_code = exc.response.status_code
        
        try:
            body = exc.response.json()
            error_obj = body.get("error", {})
            message = error_obj.get("message", str(exc))
            error_type = error_obj.get("type", "")
        except Exception:
            body = None
            message = str(exc)
            error_type = ""
        
        # Check for rate limit with retry-after
        if status_code == 429:
            retry_after = exc.response.headers.get("retry-after")
            raise RateLimitError(
                message, 
                retry_after=int(retry_after) if retry_after else None,
                body=body
            )
        
        # Map specific Anthropic errors
        if "overloaded" in error_type:
            raise ServiceUnavailableError(message, body=body)
        if "invalid_request" in error_type:
            raise BadRequestError(message, body=body)
        
        error = map_http_status_to_error(status_code, message, body)
        raise error
    
    async def _convert_messages_async(self, messages: list[Message]) -> tuple[Optional[str], list[dict]]:
        """Convert OpenAI messages to Anthropic format (async).
        
        Returns:
            Tuple of (system_message, anthropic_messages)
        """
        system_message = None
        anthropic_messages = []
        
        for msg in messages:
            if msg.role == "system":
                if isinstance(msg.content, str):
                    system_message = msg.content
                else:
                    # Handle system with content blocks
                    system_message = str(msg.content)
            elif msg.role == "user":
                anthropic_messages.append(await self._convert_user_message_async(msg))
            elif msg.role == "assistant":
                anthropic_messages.append(self._convert_assistant_message(msg))
            elif msg.role == "tool":
                # Tool results are added to the previous user message
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }]
                })
        
        return system_message, anthropic_messages
    
    async def _convert_user_message_async(self, msg: Message) -> dict:
        """Convert user message to Anthropic format (async for image downloads)."""
        if isinstance(msg.content, str):
            return {
                "role": "user",
                "content": [{"type": "text", "text": msg.content}]
            }
        
        # Handle multimodal content
        content = []
        for block in msg.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "image_url":
                url = block.image_url.get("url", "") if block.image_url else ""
                try:
                    # Try to get as base64 (handles both data URLs and remote URLs)
                    base64_data, media_type = await get_image_as_base64(url)
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_data,
                        }
                    })
                except Exception:
                    # If image download fails, add placeholder text
                    content.append({
                        "type": "text",
                        "text": f"[Image: {url}]"
                    })
        
        return {"role": "user", "content": content}
    
    def _convert_messages(self, messages: list[Message]) -> tuple[Optional[str], list[dict]]:
        """Convert OpenAI messages to Anthropic format (sync version for tests/compat).
        
        This sync version only handles data URLs, not remote image URLs.
        
        Returns:
            Tuple of (system_message, anthropic_messages)
        """
        system_message = None
        anthropic_messages = []
        
        for msg in messages:
            if msg.role == "system":
                if isinstance(msg.content, str):
                    system_message = msg.content
                else:
                    system_message = str(msg.content)
            elif msg.role == "user":
                anthropic_messages.append(self._convert_user_message(msg))
            elif msg.role == "assistant":
                anthropic_messages.append(self._convert_assistant_message(msg))
            elif msg.role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }]
                })
        
        return system_message, anthropic_messages
    
    def _convert_user_message(self, msg: Message) -> dict:
        """Convert user message to Anthropic format (sync version for tests)."""
        if isinstance(msg.content, str):
            return {
                "role": "user",
                "content": [{"type": "text", "text": msg.content}]
            }
        
        # Handle multimodal content
        content = []
        for block in msg.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "image_url":
                url = block.image_url.get("url", "") if block.image_url else ""
                if url.startswith("data:"):
                    # Handle data URLs synchronously
                    try:
                        media_type, data = url.split(";base64,")
                        media_type = media_type.replace("data:", "")
                        content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            }
                        })
                    except ValueError:
                        content.append({"type": "text", "text": f"[Image: {url}]"})
                else:
                    content.append({"type": "text", "text": f"[Image: {url}]"})
        
        return {"role": "user", "content": content}
    
    def _convert_assistant_message(self, msg: Message) -> dict:
        """Convert assistant message to Anthropic format."""
        content = []
        
        if isinstance(msg.content, str) and msg.content:
            content.append({"type": "text", "text": msg.content})
        
        if msg.tool_calls:
            for tool_call in msg.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": tool_call["function"]["name"],
                    "input": json.loads(tool_call["function"]["arguments"]),
                })
        
        return {"role": "assistant", "content": content}
    
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
            req_data = await self._transform_request_async(request)
            response = await client.post(
                "/messages",
                json=req_data,
            )
            response.raise_for_status()
            data = response.json()
            
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
        
        req_data = await self._transform_request_async(request)
        req_data["stream"] = True
        
        try:
            async with client.stream(
                "POST",
                "/messages",
                json=req_data,
            ) as response:
                response.raise_for_status()
                
                current_content = ""
                message_id = None
                model = request.model
                
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    
                    data = line[6:]
                    
                    try:
                        event = json.loads(data)
                        event_type = event.get("type")
                        
                        if event_type == "message_start":
                            message_id = event["message"]["id"]
                            model = event["message"].get("model", model)
                        
                        elif event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            if "text" in delta:
                                current_content += delta["text"]
                                chunk = StreamChunk(
                                    id=message_id or f"chunk-{int(time.time())}",
                                    object="chat.completion.chunk",
                                    created=int(time.time()),
                                    model=model,
                                    choices=[StreamChoice(
                                        index=0,
                                        delta=DeltaMessage(content=delta["text"]),
                                        finish_reason=None,
                                    )],
                                )
                                yield chunk
                        
                        elif event_type == "message_stop":
                            # Final chunk
                            chunk = StreamChunk(
                                id=message_id or f"chunk-{int(time.time())}",
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=model,
                                choices=[StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(),
                                    finish_reason="stop",
                                )],
                            )
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
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform OpenAI request to Anthropic format (sync version - no image support).
        
        Note: For full vision support including remote image URLs, use the async
        chat_completion method which handles async image downloads.
        """
        # Use sync conversion for simple cases (no async image downloads)
        system_message = None
        messages = []
        
        for msg in request.messages:
            if msg.role == "system":
                system_message = msg.content if isinstance(msg.content, str) else str(msg.content)
            elif msg.role == "user":
                # Simple sync conversion without async image handling
                if isinstance(msg.content, str):
                    messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": msg.content}]
                    })
                else:
                    # Content blocks - basic handling without async image downloads
                    content = []
                    for block in msg.content:
                        if block.type == "text":
                            content.append({"type": "text", "text": block.text})
                        elif block.type == "image_url":
                            # Try to handle data URLs synchronously
                            url = block.image_url.get("url", "") if block.image_url else ""
                            if url.startswith("data:"):
                                try:
                                    media_type, data = url.split(";base64,")
                                    media_type = media_type.replace("data:", "")
                                    content.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": data,
                                        }
                                    })
                                except ValueError:
                                    content.append({"type": "text", "text": f"[Image: {url}]"})
                            else:
                                content.append({"type": "text", "text": f"[Image: {url}]"})
                    messages.append({"role": "user", "content": content})
            elif msg.role == "assistant":
                messages.append(self._convert_assistant_message(msg))
            elif msg.role == "tool":
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }]
                })
        
        # Get the actual model name (strip anthropic/ prefix if present)
        model = request.model
        if "/" in model:
            model = model.split("/")[-1]
        
        data: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        
        if system_message:
            data["system"] = system_message
        
        if request.temperature is not None:
            data["temperature"] = request.temperature
        
        if request.top_p is not None:
            data["top_p"] = request.top_p
        
        if request.stop is not None:
            if isinstance(request.stop, str):
                data["stop_sequences"] = [request.stop]
            else:
                data["stop_sequences"] = request.stop
        
        if request.tools is not None:
            data["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {"type": "object"}),
                }
                for tool in request.tools
            ]
        
        if request.tool_choice is not None:
            if request.tool_choice == "auto":
                data["tool_choice"] = {"type": "auto"}
            elif request.tool_choice == "none":
                data["tool_choice"] = {"type": "none"}
            elif request.tool_choice == "required":
                data["tool_choice"] = {"type": "any"}
            elif isinstance(request.tool_choice, dict):
                # Specific tool
                data["tool_choice"] = {
                    "type": "tool",
                    "name": request.tool_choice.get("function", {}).get("name", ""),
                }
        
        # Handle response format for JSON mode
        if request.response_format and request.response_format.type == "json_object":
            # Add to system message
            if system_message:
                data["system"] = f"{system_message}\n\nRespond with valid JSON only."
            else:
                data["system"] = "Respond with valid JSON only."
        
        return data
    
    def transform_response(self, response: dict[str, Any], model: str) -> CompletionResponse:
        """Transform Anthropic response to OpenAI format."""
        content = ""
        tool_calls = None
        
        for block in response.get("content", []):
            if block["type"] == "text":
                content += block["text"]
            elif block["type"] == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block["input"]),
                    }
                })
        
        message = {"role": "assistant"}
        if content:
            message["content"] = content
        if tool_calls:
            message["tool_calls"] = tool_calls
        
        return CompletionResponse(
            id=response["id"],
            object="chat.completion",
            created=int(time.time()),
            model=f"anthropic/{model}",
            choices=[CompletionChoice(
                index=0,
                message=message,
                finish_reason=self._map_finish_reason(response.get("stop_reason")),
            )],
            usage=Usage(
                prompt_tokens=response["usage"]["input_tokens"],
                completion_tokens=response["usage"]["output_tokens"],
                total_tokens=response["usage"]["input_tokens"] + response["usage"]["output_tokens"],
                cache_creation_input_tokens=response["usage"].get("cache_creation_input_tokens"),
                cache_read_input_tokens=response["usage"].get("cache_read_input_tokens"),
            ),
        )
    
    def transform_stream_chunk(self, chunk: dict[str, Any], model: str) -> Optional[StreamChunk]:
        """Not used for Anthropic - streaming handled differently."""
        return None
    
    def _map_finish_reason(self, reason: Optional[str]) -> Optional[str]:
        """Map Anthropic finish reason to OpenAI format."""
        if reason is None:
            return None
        mapping = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }
        return mapping.get(reason, reason)
    
    def get_model_info(self, model: str) -> ModelInfo:
        """Get information about an Anthropic model."""
        info = get_model_info(model)
        
        return ModelInfo(
            id=model,
            name=model,
            max_tokens=int(info.get("max_tokens", 200000)),
            max_input_tokens=int(info.get("max_tokens", 200000)),
            max_output_tokens=int(info.get("max_output_tokens", 4096)),
            input_cost_per_token=info.get("input_cost_per_token", 0.0),
            output_cost_per_token=info.get("output_cost_per_token", 0.0),
            supports_vision="claude-3" in model,
            supports_tools="claude-3" in model,
            supports_streaming=True,
            provider="anthropic",
        )
    
    def _calculate_cost(self, response: CompletionResponse) -> "Decimal":
        """Calculate the cost of the response.

        Returns:
            Cost in USD as Decimal for precision
        """
        from decimal import Decimal
        from deltallm.utils.pricing import calculate_cost

        model = response.model
        if model.startswith("anthropic/"):
            model = model[10:]  # Remove prefix

        return calculate_cost(
            model=model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cached_tokens=response.usage.cache_read_input_tokens or 0,
        )
