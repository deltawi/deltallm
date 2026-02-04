"""Groq provider implementation."""

import os
from typing import Any, AsyncIterator, Optional

import httpx

from deltallm.providers.openai import OpenAIProvider
from deltallm.providers.base import ProviderCapabilities
from deltallm.providers.registry import register_provider
from deltallm.types import CompletionRequest, CompletionResponse, StreamChunk
from deltallm.exceptions import AuthenticationError, APIError, RateLimitError


@register_provider("groq", models=[
    "groq/*",
    "gpt-oss-*",
    "kimi-k2-*",
    "llama-4-*",
    "llama-3.3-*",
    "llama-3.1-*",
    "llama3-*",
    "qwen3-*",
    "mixtral-*",
    "gemma-*",
])
class GroqProvider(OpenAIProvider):
    """Groq provider for fast inference.
    
    Groq uses the OpenAI-compatible API format and supports:
    - Text chat completion
    - Vision/multimodal (Llama 4 models)
    - Tool calling
    - Streaming
    """
    
    provider_name = "groq"
    capabilities = ProviderCapabilities(
        chat=True,
        streaming=True,
        embeddings=False,  # Groq doesn't support embeddings yet
        images=False,      # No image generation
        audio=True,        # Whisper support
        tools=True,        # Tool calling supported
        vision=True,       # Vision supported via Llama 4
        json_mode=True,
        supported_model_types=["chat", "audio_transcription"],
    )
    
    # Models that support vision/multimodal
    vision_models = {
        "llama-4-scout-17b-16e",
        "llama-4-scout-17b-16e-128k",
        "llama-4-maverick-17b-128e",
        "llama-4-maverick-17b-128e-128k",
    }
    supported_models = [
        # GPT OSS Models
        "gpt-oss-20b",
        "gpt-oss-20b-128k",
        "gpt-oss-safeguard-20b",
        "gpt-oss-120b",
        "gpt-oss-120b-128k",
        # Kimi Models
        "kimi-k2-0905-1t",
        "kimi-k2-0905-1t-256k",
        # Llama 4 Models
        "llama-4-scout-17b-16e",
        "llama-4-scout-17b-16e-128k",
        "llama-4-maverick-17b-128e",
        "llama-4-maverick-17b-128e-128k",
        "llama-guard-4-12b",
        "llama-guard-4-12b-128k",
        # Llama 3.3/3.1 Models
        "llama-3.3-70b-versatile",
        "llama-3.3-70b-versatile-128k",
        "llama-3.1-8b-instant",
        "llama-3.1-8b-instant-128k",
        "llama-3.1-405b-reasoning",
        "llama-3.1-70b-versatile",
        # Qwen Models
        "qwen3-32b",
        "qwen3-32b-131k",
        # Legacy Models
        "llama3-70b-8192",
        "llama3-8b-8192",
        "mixtral-8x7b-32768",
        "gemma-7b-it",
        "gemma2-9b-it",
        "whisper-large-v3",
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the Groq provider."""
        super().__init__(api_key=api_key, api_base=api_base)
        self.api_base = api_base or "https://api.groq.com/openai/v1"
    
    def _get_api_key(self, api_key: Optional[str] = None) -> str:
        """Get the Groq API key.
        
        Args:
            api_key: Optional API key override
            
        Returns:
            API key
        """
        # Priority: override > instance > environment
        if api_key:
            return api_key
        
        if self.api_key:
            return self.api_key
        
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise AuthenticationError(
                "Groq API key not found. "
                "Set GROQ_API_KEY environment variable or pass api_key."
            )
        return key
    
    def _get_model_name(self, model: str) -> str:
        """Get the Groq model name.
        
        Args:
            model: Model name
            
        Returns:
            Groq model name
        """
        # Remove groq/ prefix if present
        if model.startswith("groq/"):
            return model[5:]
        return model
    
    def supports_vision(self, model: str) -> bool:
        """Check if the model supports vision/multimodal.
        
        Args:
            model: Model name
            
        Returns:
            True if model supports vision
        """
        model_name = self._get_model_name(model)
        return model_name in self.vision_models
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Groq.
        
        Handles both text and multimodal (vision) requests.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        body = super().transform_request(request)
        
        # Update model name
        body["model"] = self._get_model_name(request.model)
        
        # Ensure messages with images are properly formatted
        # Groq uses OpenAI-compatible format for vision
        if self.supports_vision(request.model):
            # Vision is supported, messages should already be in correct format
            # from OpenAIProvider.transform_request
            pass
        
        return body
    
    def get_model_info(self, model: str) -> "ModelInfo":
        """Get information about a Groq model.
        
        Args:
            model: Model name
            
        Returns:
            ModelInfo with capabilities
        """
        from deltallm.providers.base import ModelInfo
        
        model_name = self._get_model_name(model)
        supports_vision = self.supports_vision(model)
        
        # Model-specific token limits
        token_limits = {
            # Llama 4 models - 128k context
            "llama-4-scout-17b-16e": 128000,
            "llama-4-scout-17b-16e-128k": 128000,
            "llama-4-maverick-17b-128e": 128000,
            "llama-4-maverick-17b-128e-128k": 128000,
            "llama-guard-4-12b": 128000,
            "llama-guard-4-12b-128k": 128000,
            # Llama 3.3 - 128k context
            "llama-3.3-70b-versatile": 128000,
            "llama-3.3-70b-versatile-128k": 128000,
            # GPT OSS models
            "gpt-oss-20b": 128000,
            "gpt-oss-20b-128k": 128000,
            "gpt-oss-120b": 128000,
            "gpt-oss-120b-128k": 128000,
            "gpt-oss-safeguard-20b": 128000,
            # Kimi
            "kimi-k2-0905-1t": 256000,
            "kimi-k2-0905-1t-256k": 256000,
            # Qwen
            "qwen3-32b": 131000,
            "qwen3-32b-131k": 131000,
            # Legacy models
            "llama-3.1-405b-reasoning": 128000,
            "llama-3.1-70b-versatile": 128000,
            "llama-3.1-8b-instant": 128000,
            "llama3-70b-8192": 8192,
            "llama3-8b-8192": 8192,
            "mixtral-8x7b-32768": 32768,
            "gemma-7b-it": 8192,
            "gemma2-9b-it": 8192,
            "whisper-large-v3": 0,  # Audio model
        }
        
        max_tokens = token_limits.get(model_name, 8192)
        
        return ModelInfo(
            id=model_name,
            name=model_name,
            max_tokens=max_tokens,
            max_input_tokens=max_tokens,
            max_output_tokens=4096 if max_tokens > 0 else 0,
            input_cost_per_token=0.0,  # Pricing varies by model
            output_cost_per_token=0.0,
            supports_vision=supports_vision,
            supports_tools=True,
            supports_streaming=True,
            provider="groq",
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
        base = api_base or self.api_base
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
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
                raise APITimeoutError(f"Request to Groq timed out: {e}")
            except Exception as e:
                raise APIError(f"Groq request failed: {e}")
    
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
        base = api_base or self.api_base
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._get_api_key(api_key)}",
            "Content-Type": "application/json",
        }

        body = self.transform_request(request)
        body["stream"] = True

        async with httpx.AsyncClient(timeout=60.0) as client:
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
        
        Note: Groq doesn't currently support embeddings, this is a placeholder.
        
        Args:
            model: Model name
            input: List of texts to embed
            api_key: API key
            api_base: Optional API base URL
            
        Returns:
            List of embedding vectors
        """
        raise NotImplementedError("Groq does not currently support embeddings")
    
    def _handle_error(self, status_code: int, error_data: dict) -> None:
        """Handle Groq-specific errors.
        
        Args:
            status_code: HTTP status code
            error_data: Error response data
        """
        error = error_data.get("error", {})
        message = error.get("message", "Unknown error")
        
        if status_code == 401:
            raise AuthenticationError(f"Invalid Groq API key: {message}")
        elif status_code == 429:
            raise RateLimitError(f"Groq rate limit exceeded: {message}")
        elif status_code >= 500:
            raise APIError(f"Groq server error ({status_code}): {message}")
        else:
            raise APIError(f"Groq error ({status_code}): {message}")
