"""Mistral AI provider implementation."""

import os
from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.providers.openai import OpenAIProvider
from deltallm.providers.registry import register_provider
from deltallm.types import CompletionRequest, CompletionResponse, StreamChunk
from deltallm.exceptions import AuthenticationError, APIError, RateLimitError


@register_provider("mistral", models=[
    "mistral/*",
    "mistral-tiny",
    "mistral-small",
    "mistral-medium",
    "mistral-large*",
    "open-mistral*",
    "open-mixtral*",
])
class MistralProvider(OpenAIProvider):
    """Mistral AI provider.
    
    Mistral uses a very similar API to OpenAI, so we extend OpenAIProvider.
    """
    
    provider_name = "mistral"
    supported_models = [
        "mistral-tiny",
        "mistral-small",
        "mistral-medium",
        "mistral-large-latest",
        "mistral-large-2402",
        "mistral-embed",
        "open-mistral-7b",
        "open-mixtral-8x7b",
        "open-mixtral-8x22b",
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the Mistral provider."""
        super().__init__(api_key=api_key, api_base=api_base)
        self.base_url = api_base or "https://api.mistral.ai/v1"
        self.timeout = 60.0
    
    def _get_api_key(self, api_key: Optional[str] = None) -> str:
        """Get the Mistral API key.
        
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
        
        key = os.getenv("MISTRAL_API_KEY")
        if not key:
            raise AuthenticationError(
                "Mistral API key not found. "
                "Set MISTRAL_API_KEY environment variable."
            )
        return key
    
    def _get_model_name(self, model: str) -> str:
        """Get the Mistral model name.
        
        Args:
            model: Model name
            
        Returns:
            Mistral model name
        """
        # Remove mistral/ prefix if present
        if model.startswith("mistral/"):
            return model[8:]
        return model
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Mistral.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        body = super().transform_request(request)
        
        # Update model name
        body["model"] = self._get_model_name(request.model)
        
        return body
    
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
        url = f"{base}/chat/completions"
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
                return self.transform_response(data, request.model)
            except httpx.HTTPStatusError as e:
                error_data = e.response.json() if e.response.text else {}
                self._handle_error(e.response.status_code, error_data)
            except httpx.TimeoutException as e:
                from deltallm.exceptions import APITimeoutError
                raise APITimeoutError(f"Request to Mistral timed out: {e}")
            except Exception as e:
                raise APIError(f"Mistral request failed: {e}")
    
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
        url = f"{base}/chat/completions"
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
                            yield self.transform_stream_chunk(chunk_data, request.model)
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
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
            "Content-Type": "application/json",
        }
        
        body = {
            "model": self._get_model_name(model),
            "input": input,
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            
            return [item["embedding"] for item in data["data"]]
    
    def _handle_error(self, status_code: int, error_data: dict) -> None:
        """Handle Mistral-specific errors.
        
        Args:
            status_code: HTTP status code
            error_data: Error response data
        """
        message = error_data.get("message", "Unknown error")
        
        if status_code == 401:
            raise AuthenticationError(f"Invalid Mistral API key: {message}")
        elif status_code == 429:
            raise RateLimitError(f"Mistral rate limit exceeded: {message}")
        elif status_code >= 500:
            raise APIError(f"Mistral server error ({status_code}): {message}")
        else:
            raise APIError(f"Mistral error ({status_code}): {message}")
