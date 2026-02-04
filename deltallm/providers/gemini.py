"""Google Gemini / Vertex AI provider implementation."""

import json
import os
from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.providers.base import BaseProvider, ProviderCapabilities
from deltallm.providers.registry import register_provider
from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    Message,
    StreamChunk,
    CompletionChoice,
    Usage,
)
from deltallm.utils.vision import get_image_as_base64
from deltallm.exceptions import AuthenticationError, APIError, RateLimitError


@register_provider("gemini", models=[
    "gemini/*",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.0-pro",
    "gemini-pro",
    "gemini-pro-vision",
    "gemini-ultra",
])
class GeminiProvider(BaseProvider):
    """Google Gemini / Vertex AI provider.
    
    Supports Gemini Pro and Gemini Ultra models via Google AI Studio
    or Vertex AI.
    """
    
    provider_name = "gemini"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=True,
        images=False,
        audio=False,
        tools=True,
        vision=True,
        json_mode=True,
        supported_model_types=["chat", "embedding"],
    )
    supported_models = [
        # Gemini 1.5
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro-latest",
        "gemini-1.5-flash-latest",
        # Gemini 1.0
        "gemini-1.0-pro",
        "gemini-1.0-pro-latest",
        "gemini-1.0-pro-vision-latest",
        "gemini-pro",
        "gemini-pro-vision",
        # Ultra
        "gemini-ultra",
    ]
    
    def __init__(self, use_vertex: bool = False) -> None:
        """Initialize the Gemini provider.
        
        Args:
            use_vertex: Whether to use Vertex AI instead of AI Studio
        """
        self.timeout = 60.0
        self.use_vertex = use_vertex
        
        if use_vertex:
            self.base_url = "https://{location}-aiplatform.googleapis.com/v1"
        else:
            self.base_url = "https://generativelanguage.googleapis.com/v1"
    
    def _get_api_key(self) -> str:
        """Get the Google API key.
        
        Returns:
            API key
        """
        if self.use_vertex:
            # Vertex AI uses application default credentials
            return ""
        
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise AuthenticationError(
                "Google API key not found. "
                "Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable."
            )
        return api_key
    
    def _get_model_name(self, model: str) -> str:
        """Get the full Gemini model name.
        
        Args:
            model: Model name
            
        Returns:
            Full model name
        """
        # Remove gemini/ prefix if present
        if model.startswith("gemini/"):
            model = model[7:]
        
        # Remove vertex/ prefix if present
        if model.startswith("vertex/"):
            model = model[7:]
        
        return model
    
    async def _convert_message_async(self, message: Message) -> dict[str, Any]:
        """Convert a message to Gemini format (async for image downloads).
        
        Args:
            message: Message to convert
            
        Returns:
            Gemini format message
        """
        role_map = {
            "system": "user",  # Gemini doesn't have system role, use user
            "user": "user",
            "assistant": "model",
            "tool": "user",
        }
        
        role = role_map.get(message.role, "user")
        
        # Handle content - Gemini expects parts array
        if isinstance(message.content, str):
            parts = [{"text": message.content}]
        elif isinstance(message.content, list):
            parts = []
            for item in message.content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        # Handle image
                        image_url = item.get("image_url", {}).get("url", "")
                        try:
                            # Try to get as base64 (handles both data URLs and remote URLs)
                            base64_data, mime_type = await get_image_as_base64(image_url)
                            parts.append({
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": base64_data,
                                }
                            })
                        except Exception:
                            # If image download fails, add placeholder text
                            parts.append({"text": f"[Image: {image_url}]"})
        else:
            parts = [{"text": str(message.content)}]
        
        return {
            "role": role,
            "parts": parts,
        }
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Gemini (sync version).
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        return {
            "_needs_async_transform": True,
            "model": request.model,
        }  # Actual conversion happens in _transform_request_async
        
    async def _transform_request_async(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Gemini (async version)."""
        body: dict[str, Any] = {
            "contents": [],
        }
        
        # Handle system instruction (Gemini specific)
        system_message = None
        messages = []
        
        for msg in request.messages:
            if msg.role == "system":
                system_message = msg.content
            else:
                messages.append(msg)
        
        if system_message:
            body["systemInstruction"] = {
                "parts": [{"text": system_message}]
            }
        
        # Convert messages asynchronously
        for msg in messages:
            gemini_msg = await self._convert_message_async(msg)
            body["contents"].append(gemini_msg)
        
        # Generation config
        generation_config: dict[str, Any] = {}
        
        if request.max_tokens:
            generation_config["maxOutputTokens"] = request.max_tokens
        
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        
        if request.top_p is not None:
            generation_config["topP"] = request.top_p
        
        # Gemini uses topK instead of typical sampling
        if request.top_k is not None:
            generation_config["topK"] = request.top_k
        
        if generation_config:
            body["generationConfig"] = generation_config
        
        # Handle tools/functions
        if request.tools:
            tools = []
            for tool in request.tools:
                if tool.type == "function":
                    func = tool.function
                    tools.append({
                        "functionDeclarations": [{
                            "name": func.get("name", ""),
                            "description": func.get("description", ""),
                            "parameters": func.get("parameters", {}),
                        }]
                    })
            if tools:
                body["tools"] = tools
        
        return body
        
        # Generation config
        generation_config: dict[str, Any] = {}
        
        if request.max_tokens:
            generation_config["maxOutputTokens"] = request.max_tokens
        
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        
        if request.top_p is not None:
            generation_config["topP"] = request.top_p
        
        # Gemini uses topK instead of typical sampling
        if request.top_k is not None:
            generation_config["topK"] = request.top_k
        
        if generation_config:
            body["generationConfig"] = generation_config
        
        # Handle tools/functions
        if request.tools:
            tools = []
            for tool in request.tools:
                if tool.type == "function":
                    func = tool.function
                    tools.append({
                        "functionDeclarations": [{
                            "name": func.get("name", ""),
                            "description": func.get("description", ""),
                            "parameters": func.get("parameters", {}),
                        }]
                    })
            if tools:
                body["tools"] = tools
        
        return body
    
    def transform_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Gemini response to OpenAI format.
        
        Args:
            data: Gemini response data
            
        Returns:
            Transformed response
        """
        candidates = data.get("candidates", [])
        if not candidates:
            return CompletionResponse(
                id="gemini-response",
                object="chat.completion",
                created=0,
                model=self.provider_name,
                choices=[
                    CompletionChoice(
                        index=0,
                        message={"role": "assistant", "content": ""},
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )
        
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        
        # Extract text from parts
        text_parts = []
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
        
        text = "".join(text_parts)
        
        # Map finish reason
        finish_reason_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "OTHER": "stop",
        }
        finish_reason = finish_reason_map.get(
            candidate.get("finishReason"), "stop"
        )
        
        # Get usage
        usage_metadata = data.get("usageMetadata", {})
        usage = Usage(
            prompt_tokens=usage_metadata.get("promptTokenCount", 0),
            completion_tokens=usage_metadata.get("candidatesTokenCount", 0),
            total_tokens=usage_metadata.get("totalTokenCount", 0),
        )
        
        return CompletionResponse(
            id=data.get("name", "gemini-response"),
            object="chat.completion",
            created=0,
            model=self.provider_name,
            choices=[
                CompletionChoice(
                    index=0,
                    message={
                        "role": "assistant",
                        "content": text,
                    },
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
        )
    
    def _build_url(self, model: str, stream: bool = False) -> str:
        """Build the API URL.
        
        Args:
            model: Model name
            stream: Whether to use streaming endpoint
            
        Returns:
            API URL
        """
        model_name = self._get_model_name(model)
        
        if self.use_vertex:
            # Vertex AI format
            project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            
            base = self.base_url.format(location=location)
            
            if stream:
                return f"{base}/projects/{project}/locations/{location}/publishers/google/models/{model_name}:streamGenerateContent"
            else:
                return f"{base}/projects/{project}/locations/{location}/publishers/google/models/{model_name}:generateContent"
        else:
            # AI Studio format
            api_key = self._get_api_key()
            
            if stream:
                return f"{self.base_url}/models/{model_name}:streamGenerateContent?key={api_key}"
            else:
                return f"{self.base_url}/models/{model_name}:generateContent?key={api_key}"
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Make a chat completion request.

        Args:
            request: Completion request
            api_key: Optional API key override (for Vertex AI, this is ignored in favor of ADC)
            api_base: Optional API base URL override

        Returns:
            Completion response
        """
        url = self._build_url(request.model, stream=False)
        headers = {
            "Content-Type": "application/json",
        }
        
        body = await self._transform_request_async(request)
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                return self.transform_response(data)
            except httpx.HTTPStatusError as e:
                error_data = e.response.json() if e.response.text else {}
                self._handle_error(e.response.status_code, error_data)
            except httpx.TimeoutException as e:
                from deltallm.exceptions import APITimeoutError
                raise APITimeoutError(f"Request to Gemini timed out: {e}")
            except Exception as e:
                raise APIError(f"Gemini request failed: {e}")
    
    async def chat_completion_stream(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Make a streaming chat completion request.

        Args:
            request: Completion request
            api_key: Optional API key override
            api_base: Optional API base URL override

        Yields:
            Stream chunks
        """
        url = self._build_url(request.model, stream=True)
        headers = {
            "Content-Type": "application/json",
        }
        
        body = await self._transform_request_async(request)
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    error_data = await response.json() if response.text else {}
                    self._handle_error(response.status_code, error_data)
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        
                        try:
                            chunk_data = json.loads(data)
                            yield self.transform_stream_chunk(chunk_data)
                        except json.JSONDecodeError:
                            continue
    
    def transform_stream_chunk(self, data: dict[str, Any]) -> StreamChunk:
        """Transform a Gemini stream chunk to OpenAI format.
        
        Args:
            data: Gemini chunk data
            
        Returns:
            Transformed stream chunk
        """
        candidates = data.get("candidates", [])
        if not candidates:
            return StreamChunk(
                id="gemini-chunk",
                object="chat.completion.chunk",
                created=0,
                model=self.provider_name,
                choices=[],
            )
        
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        
        text = ""
        for part in parts:
            if "text" in part:
                text += part["text"]
        
        return StreamChunk(
            id=data.get("name", "gemini-chunk"),
            object="chat.completion.chunk",
            created=0,
            model=self.provider_name,
            choices=[
                {
                    "index": 0,
                    "delta": {"content": text} if text else {},
                    "finish_reason": None,
                }
            ],
        )
    
    async def embedding(
        self,
        model: str,
        input: list[str],
        api_key: str,
        api_base: Optional[str] = None,
    ) -> list[list[float]]:
        """Create embeddings.
        
        Args:
            model: Model name
            input: List of texts to embed
            api_key: API key
            api_base: Optional API base URL
            
        Returns:
            List of embedding vectors
        """
        model_name = self._get_model_name(model)
        
        if self.use_vertex:
            raise NotImplementedError("Vertex AI embeddings not yet implemented")
        
        api_key = api_key or self._get_api_key()
        url = f"{self.base_url}/models/{model_name}:embedContent?key={api_key}"
        
        headers = {
            "Content-Type": "application/json",
        }
        
        embeddings = []
        
        for text in input:
            body = {
                "content": {
                    "parts": [{"text": text}]
                }
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                
                embedding = data.get("embedding", {}).get("values", [])
                embeddings.append(embedding)
        
        return embeddings
    
    def _handle_error(self, status_code: int, error_data: dict) -> None:
        """Handle Gemini-specific errors.
        
        Args:
            status_code: HTTP status code
            error_data: Error response data
        """
        error = error_data.get("error", {})
        message = error.get("message", "Unknown error")
        
        if status_code == 400:
            if "API key not valid" in message:
                raise AuthenticationError(f"Invalid Gemini API key: {message}")
            raise APIError(f"Bad request: {message}")
        elif status_code == 401:
            raise AuthenticationError(f"Authentication failed: {message}")
        elif status_code == 429:
            raise RateLimitError(f"Gemini rate limit exceeded: {message}")
        elif status_code >= 500:
            raise APIError(f"Gemini server error ({status_code}): {message}")
        else:
            raise APIError(f"Gemini error ({status_code}): {message}")
