"""Azure OpenAI provider implementation."""

import os
from typing import Any, AsyncIterator, Optional
from urllib.parse import urljoin

import httpx

from deltallm.providers.openai import OpenAIProvider
from deltallm.providers.registry import register_provider
from deltallm.types import CompletionRequest, CompletionResponse, StreamChunk
from deltallm.exceptions import AuthenticationError, APIError


@register_provider("azure", models=[
    "azure/*",
])
class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI provider.
    
    Azure OpenAI uses the same API format as OpenAI but with:
    - Different base URL format (https://{endpoint}/openai/deployments/{deployment})
    - API key authentication (not Bearer token)
    - api-version query parameter
    - Deployment-based model routing
    """
    
    provider_name = "azure"
    supported_models = [
        "gpt-4o",
        "gpt-4",
        "gpt-4-turbo",
        "gpt-35-turbo",
        "gpt-3.5-turbo",
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ]
    
    def __init__(self, api_version: str = "2024-02-01") -> None:
        """Initialize the Azure OpenAI provider.
        
        Args:
            api_version: Azure API version
        """
        super().__init__()
        self.api_version = api_version
    
    def _get_api_base(self, request: CompletionRequest) -> str:
        """Get the API base URL.
        
        Args:
            request: Completion request
            
        Returns:
            API base URL
        """
        # Check for api_base in request or use env var
        if hasattr(request, 'api_base') and request.api_base:
            return request.api_base.rstrip('/')
        
        # Try environment variable
        api_base = os.getenv("AZURE_OPENAI_ENDPOINT")
        if api_base:
            return api_base.rstrip('/')
        
        raise AuthenticationError(
            "Azure OpenAI endpoint not configured. "
            "Set AZURE_OPENAI_ENDPOINT or pass api_base in request."
        )
    
    def _get_deployment(self, request: CompletionRequest) -> str:
        """Get the deployment name from the model.
        
        Args:
            request: Completion request
            
        Returns:
            Deployment name
        """
        # Model could be in format "azure/deployment-name" or just "deployment-name"
        model = request.model
        if "/" in model:
            _, deployment = model.split("/", 1)
            return deployment
        return model
    
    def _build_url(self, request: CompletionRequest, path: str) -> str:
        """Build the full API URL.
        
        Args:
            request: Completion request
            path: API path
            
        Returns:
            Full URL
        """
        base = self._get_api_base(request)
        deployment = self._get_deployment(request)
        
        # Azure URL format: {endpoint}/openai/deployments/{deployment}/{path}?api-version={version}
        url = f"{base}/openai/deployments/{deployment}{path}"
        
        # Add api-version
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api-version={self.api_version}"
        
        return url
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Azure OpenAI.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        # Azure uses the same format as OpenAI
        body = super().transform_request(request)
        
        # Azure doesn't accept the model parameter in the body
        # (it's determined by the deployment in the URL)
        if "model" in body:
            del body["model"]
        
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
            api_key: Optional Azure API key override
            api_base: Optional API base URL override

        Returns:
            Completion response
        """
        url = self._build_url(request, "/chat/completions")
        key = api_key or os.getenv("AZURE_OPENAI_API_KEY", "")
        headers = {
            "api-key": key,  # Azure uses api-key header, not Authorization
            "Content-Type": "application/json",
        }

        body = self.transform_request(request)

        async with httpx.AsyncClient(timeout=60.0) as client:
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
                raise APITimeoutError(f"Request to Azure OpenAI timed out: {e}")
            except Exception as e:
                raise APIError(f"Azure OpenAI request failed: {e}")
    
    async def chat_completion_stream(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Make a streaming chat completion request.

        Args:
            request: Completion request
            api_key: Optional Azure API key override
            api_base: Optional API base URL override

        Yields:
            Stream chunks
        """
        url = self._build_url(request, "/chat/completions")
        key = api_key or os.getenv("AZURE_OPENAI_API_KEY", "")
        headers = {
            "api-key": key,
            "Content-Type": "application/json",
        }

        body = self.transform_request(request)
        body["stream"] = True

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
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
            except httpx.TimeoutException as e:
                from deltallm.exceptions import APITimeoutError
                raise APITimeoutError(f"Request to Azure OpenAI timed out: {e}")
    
    async def embedding(
        self,
        model: str,
        input: list[str],
        api_key: str,
        api_base: Optional[str] = None,
    ) -> list[list[float]]:
        """Create embeddings.
        
        Args:
            model: Model/deployment name
            input: List of texts to embed
            api_key: Azure API key
            api_base: Optional API base URL
            
        Returns:
            List of embedding vectors
        """
        # Create a minimal request to get the base URL
        request = CompletionRequest(model=model, messages=[])
        if api_base:
            request.api_base = api_base
        
        url = self._build_url(request, "/embeddings")
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
        }
        
        body = {
            "input": input,
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            
            return [item["embedding"] for item in data["data"]]
    
    def _handle_error(self, status_code: int, error_data: dict) -> None:
        """Handle Azure-specific errors.
        
        Args:
            status_code: HTTP status code
            error_data: Error response data
        """
        # Azure error format: {"error": {"code": "...", "message": "..."}}
        error = error_data.get("error", {})
        error_code = error.get("code", "")
        message = error.get("message", "Unknown error")
        
        if status_code == 401 or error_code == "401":
            raise AuthenticationError(f"Azure authentication failed: {message}")
        elif status_code == 404 and "Deployment" in message:
            from deltallm.exceptions import ModelNotSupportedError
            raise ModelNotSupportedError(self.provider_name, message=message)
        elif status_code == 429:
            from deltallm.exceptions import RateLimitError
            raise RateLimitError(f"Azure rate limit exceeded: {message}")
        else:
            raise APIError(f"Azure API error ({status_code}): {message}", provider=self.provider_name)
