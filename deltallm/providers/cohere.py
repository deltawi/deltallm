"""Cohere provider implementation."""

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
from deltallm.exceptions import AuthenticationError, APIError, RateLimitError


@register_provider("cohere", models=[
    "cohere/*",
    "command-r",
    "command-r-plus",
    "command",
    "command-nightly",
    "command-light",
    "embed-english*",
    "embed-multilingual*",
])
class CohereProvider(BaseProvider):
    """Cohere provider for command-r and embed models."""

    provider_name = "cohere"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=True,
        images=False,
        audio=False,
        tools=True,
        vision=False,
        json_mode=True,
        supported_model_types=["chat", "embedding", "rerank"],
    )
    supported_models = [
        "command-r",
        "command-r-plus",
        "command",
        "command-nightly",
        "command-light",
        "command-light-nightly",
        "embed-english-v3.0",
        "embed-multilingual-v3.0",
        "embed-english-light-v3.0",
        "embed-multilingual-light-v3.0",
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the Cohere provider."""
        super().__init__(api_key=api_key, api_base=api_base)
        self.timeout = 60.0
        self.base_url = api_base or "https://api.cohere.com/v1"
    
    def _get_api_key(self, api_key: Optional[str] = None) -> str:
        """Get the Cohere API key.
        
        Args:
            api_key: Optional API key
            
        Returns:
            API key
        """
        # Check passed api_key first, then self.api_key, then environment
        if api_key:
            return api_key
        
        if self.api_key:
            return self.api_key
        
        key = os.getenv("COHERE_API_KEY")
        if not key:
            raise AuthenticationError(
                "Cohere API key not found. "
                "Set COHERE_API_KEY environment variable."
            )
        return key
    
    def _get_model_name(self, model: str) -> str:
        """Get the Cohere model name.
        
        Args:
            model: Model name
            
        Returns:
            Cohere model name
        """
        # Remove cohere/ prefix if present
        if model.startswith("cohere/"):
            return model[7:]
        return model
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Cohere.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        body: dict[str, Any] = {
            "model": self._get_model_name(request.model),
        }
        
        # Convert messages to chat history
        chat_history = []
        message = None
        
        for msg in request.messages:
            if msg.role == "system":
                body["preamble"] = msg.content
            elif msg.role == "user":
                # If we already have a message, save the conversation turn
                if message:
                    chat_history.append({
                        "role": "USER",
                        "message": message,
                    })
                message = msg.content
            elif msg.role == "assistant":
                if message:
                    chat_history.append({
                        "role": "USER",
                        "message": message,
                    })
                    message = None
                chat_history.append({
                    "role": "CHATBOT",
                    "message": msg.content,
                })
        
        # The last user message is the main message
        if message:
            body["message"] = message
        elif chat_history:
            # If no pending message, use the last chatbot response
            body["message"] = "Continue"
        
        if chat_history:
            body["chat_history"] = chat_history
        
        # Add optional parameters
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            body["temperature"] = request.temperature
        
        if request.top_p is not None:
            body["p"] = request.top_p
        
        if request.stream:
            body["stream"] = True
        
        # Handle tools
        if request.tools:
            tools = []
            for tool in request.tools:
                if tool.type == "function":
                    func = tool.function
                    tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameter_definitions": func.get("parameters", {}).get("properties", {}),
                    })
            if tools:
                body["tools"] = tools
        
        return body
    
    def transform_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Cohere response to OpenAI format.
        
        Args:
            data: Cohere response data
            
        Returns:
            Transformed response
        """
        # Get the main text
        text = ""
        if "text" in data:
            text = data["text"]
        elif "message" in data:
            text = data["message"]
        
        # Map finish reason
        finish_reason = data.get("finish_reason", "COMPLETE")
        finish_reason_map = {
            "COMPLETE": "stop",
            "MAX_TOKENS": "length",
            "ERROR": "error",
            "ERROR_TOXIC": "content_filter",
        }
        
        # Get usage
        usage_data = data.get("meta", {}).get("tokens", {})
        usage = Usage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )
        
        return CompletionResponse(
            id=data.get("generation_id", "cohere-response"),
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
                    finish_reason=finish_reason_map.get(finish_reason, "stop"),
                )
            ],
            usage=usage,
        )
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Make a chat completion request.

        Args:
            request: Completion request
            api_key: Optional API key override
            api_base: Optional API base URL override

        Returns:
            Completion response
        """
        base = api_base or self.base_url
        url = f"{base}/chat"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
            "Content-Type": "application/json",
        }
        
        body = self.transform_request(request)
        
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
                raise APITimeoutError(f"Request to Cohere timed out: {e}")
            except Exception as e:
                raise APIError(f"Cohere request failed: {e}")
    
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
        base = api_base or self.base_url
        url = f"{base}/chat"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
            "Content-Type": "application/json",
        }
        
        body = self.transform_request(request)
        body["stream"] = True
        
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
                            import json
                            chunk_data = json.loads(data)
                            
                            # Cohere streaming format
                            text = chunk_data.get("text", "")
                            if text:
                                yield StreamChunk(
                                    id=chunk_data.get("generation_id", "cohere-chunk"),
                                    object="chat.completion.chunk",
                                    created=0,
                                    model=self.provider_name,
                                    choices=[
                                        {
                                            "index": 0,
                                            "delta": {"content": text},
                                            "finish_reason": None,
                                        }
                                    ],
                                )
                        except json.JSONDecodeError:
                            continue
    
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
        url = f"{self.base_url}/embed"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
            "Content-Type": "application/json",
        }
        
        body = {
            "model": self._get_model_name(model),
            "texts": input,
            "input_type": "search_document",
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            
            return data.get("embeddings", [])
    
    def _handle_error(self, status_code: int, error_data: dict) -> None:
        """Handle Cohere-specific errors.
        
        Args:
            status_code: HTTP status code
            error_data: Error response data
        """
        message = error_data.get("message", "Unknown error")
        
        if status_code == 401:
            raise AuthenticationError(f"Invalid Cohere API key: {message}")
        elif status_code == 429:
            raise RateLimitError(f"Cohere rate limit exceeded: {message}")
        elif status_code >= 500:
            raise APIError(f"Cohere server error ({status_code}): {message}")
        else:
            raise APIError(f"Cohere error ({status_code}): {message}")
