"""AWS Bedrock provider implementation."""

import json
import os
from typing import Any, AsyncIterator, Optional
from urllib.parse import urljoin

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


@register_provider("bedrock", models=[
    "bedrock/*",
    "anthropic.claude-*",
    "meta.llama*",
    "mistral.mistral*",
    "mistral.mixtral*",
    "cohere.command*",
    "amazon.titan*",
])
class AWSBedrockProvider(BaseProvider):
    """AWS Bedrock provider.

    Supports Claude, Llama, Mistral, and other models via AWS Bedrock.
    Requires AWS credentials (access key ID, secret access key, region).
    """

    provider_name = "bedrock"
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
        # Anthropic
        "anthropic.claude-3-opus-20240229-v1:0",
        "anthropic.claude-3-sonnet-20240229-v1:0",
        "anthropic.claude-3-haiku-20240307-v1:0",
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "anthropic.claude-instant-v1",
        "anthropic.claude-v2",
        "anthropic.claude-v2:1",
        # Meta Llama
        "meta.llama2-13b-chat-v1",
        "meta.llama2-70b-chat-v1",
        "meta.llama3-8b-instruct-v1:0",
        "meta.llama3-70b-instruct-v1:0",
        # Mistral
        "mistral.mistral-7b-instruct-v0:2",
        "mistral.mixtral-8x7b-instruct-v0:1",
        "mistral.mistral-large-2402-v1:0",
        # Cohere
        "cohere.command-text-v14",
        "cohere.command-light-text-v14",
        # Amazon
        "amazon.titan-text-express-v1",
        "amazon.titan-text-lite-v1",
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize the Bedrock provider."""
        super().__init__(api_key=api_key, api_base=api_base)
        self.timeout = 60.0
        
        # Try to import boto3 (optional dependency)
        try:
            import boto3
            self._boto3_available = True
        except ImportError:
            self._boto3_available = False
    
    def _get_credentials(self) -> tuple[str, str, Optional[str]]:
        """Get AWS credentials.
        
        Returns:
            Tuple of (access_key_id, secret_access_key, session_token)
        """
        # Check self.api_key first, then fall back to environment variables
        access_key = self.api_key or os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        session_token = os.getenv("AWS_SESSION_TOKEN")
        
        if not access_key or not secret_key:
            raise AuthenticationError(
                "AWS credentials not found. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
            )
        
        return access_key, secret_key, session_token
    
    def _get_region(self) -> str:
        """Get AWS region.
        
        Returns:
            AWS region
        """
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if not region:
            raise AuthenticationError(
                "AWS region not configured. Set AWS_REGION environment variable."
            )
        return region
    
    def _get_model_id(self, model: str) -> str:
        """Get the full Bedrock model ID.
        
        Args:
            model: Model name
            
        Returns:
            Full model ID
        """
        # Remove bedrock/ prefix if present
        if model.startswith("bedrock/"):
            model = model[8:]
        
        # Check if already a full model ID
        if "." in model:
            return model
        
        # Map short names to full IDs
        model_mapping = {
            "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
            "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
            "claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
            "claude-instant": "anthropic.claude-instant-v1",
            "claude-v2": "anthropic.claude-v2",
            "llama2-13b": "meta.llama2-13b-chat-v1",
            "llama2-70b": "meta.llama2-70b-chat-v1",
            "llama3-8b": "meta.llama3-8b-instruct-v1:0",
            "llama3-70b": "meta.llama3-70b-instruct-v1:0",
            "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
            "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
            "mistral-large": "mistral.mistral-large-2402-v1:0",
        }
        
        if model in model_mapping:
            return model_mapping[model]
        
        return model
    
    def _is_anthropic_model(self, model_id: str) -> bool:
        """Check if model is from Anthropic.
        
        Args:
            model_id: Model ID
            
        Returns:
            True if Anthropic model
        """
        return model_id.startswith("anthropic.")
    
    def _is_meta_model(self, model_id: str) -> bool:
        """Check if model is from Meta.
        
        Args:
            model_id: Model ID
            
        Returns:
            True if Meta model
        """
        return model_id.startswith("meta.")
    
    def _is_mistral_model(self, model_id: str) -> bool:
        """Check if model is from Mistral.
        
        Args:
            model_id: Model ID
            
        Returns:
            True if Mistral model
        """
        return model_id.startswith("mistral.")
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Bedrock.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        model_id = self._get_model_id(request.model)
        
        if self._is_anthropic_model(model_id):
            return self._transform_anthropic_request(request)
        elif self._is_meta_model(model_id):
            return self._transform_meta_request(request)
        elif self._is_mistral_model(model_id):
            return self._transform_mistral_request(request)
        else:
            # Default to anthropic format for unknown models
            return self._transform_anthropic_request(request)
    
    def _transform_anthropic_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Anthropic models on Bedrock.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [],
        }
        
        # Extract system message
        system_message = None
        for msg in request.messages:
            if msg.role == "system":
                system_message = msg.content
                break
        
        if system_message:
            body["system"] = system_message
        
        # Convert messages (exclude system)
        bedrock_messages = []
        for msg in request.messages:
            if msg.role == "system":
                continue
            
            bedrock_msg = {
                "role": "user" if msg.role in ["user", "tool"] else "assistant",
                "content": msg.content,
            }
            bedrock_messages.append(bedrock_msg)
        
        body["messages"] = bedrock_messages
        
        # Add optional parameters
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            body["temperature"] = request.temperature
        
        if request.top_p is not None:
            body["top_p"] = request.top_p
        
        # Handle tools
        if request.tools:
            body["tools"] = [tool.model_dump() for tool in request.tools]
        
        return body
    
    def _transform_meta_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Meta Llama models on Bedrock.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        # Build prompt from messages
        prompt = self._messages_to_prompt(request.messages)
        
        body: dict[str, Any] = {
            "prompt": prompt,
        }
        
        if request.max_tokens:
            body["max_gen_len"] = request.max_tokens
        
        if request.temperature is not None:
            body["temperature"] = request.temperature
        
        if request.top_p is not None:
            body["top_p"] = request.top_p
        
        return body
    
    def _transform_mistral_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform request for Mistral models on Bedrock.
        
        Args:
            request: Completion request
            
        Returns:
            Transformed request body
        """
        # Build prompt from messages
        prompt = self._messages_to_prompt(request.messages)
        
        body: dict[str, Any] = {
            "prompt": prompt,
        }
        
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            body["temperature"] = request.temperature
        
        if request.top_p is not None:
            body["top_p"] = request.top_p
        
        return body
    
    def _messages_to_prompt(self, messages: list[Message]) -> str:
        """Convert messages to a single prompt string.
        
        Args:
            messages: List of messages
            
        Returns:
            Prompt string
        """
        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"<s>[INST] <<SYS>>\n{msg.content}\n<</SYS>>\n\n")
            elif msg.role == "user":
                parts.append(f"{msg.content} [/INST]")
            elif msg.role == "assistant":
                parts.append(f" {msg.content} </s><s>[INST]")
        
        return "".join(parts)
    
    def transform_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Bedrock response to OpenAI format.
        
        Args:
            data: Bedrock response data
            
        Returns:
            Transformed response
        """
        # Detect response type
        if "content" in data and isinstance(data["content"], list):
            # Anthropic format
            return self._transform_anthropic_response(data)
        elif "generation" in data:
            # Meta format
            return self._transform_meta_response(data)
        elif "outputs" in data:
            # Mistral format
            return self._transform_mistral_response(data)
        else:
            raise APIError(f"Unknown response format from Bedrock: {data}")
    
    def _transform_anthropic_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Anthropic response from Bedrock.
        
        Args:
            data: Response data
            
        Returns:
            CompletionResponse
        """
        # Extract text content
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content = block.get("text", "")
                break
        
        # Map stop reason
        stop_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        stop_reason = stop_reason_map.get(data.get("stop_reason"), "stop")
        
        # Get usage
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )
        
        return CompletionResponse(
            id=data.get("id", "bedrock-response"),
            object="chat.completion",
            created=0,  # Bedrock doesn't provide this
            model=self.provider_name,
            choices=[
                CompletionChoice(
                    index=0,
                    message={
                        "role": "assistant",
                        "content": content,
                    },
                    finish_reason=stop_reason,
                )
            ],
            usage=usage,
        )
    
    def _transform_meta_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Meta Llama response from Bedrock.
        
        Args:
            data: Response data
            
        Returns:
            CompletionResponse
        """
        generation = data.get("generation", "")
        
        return CompletionResponse(
            id="bedrock-meta-response",
            object="chat.completion",
            created=0,
            model=self.provider_name,
            choices=[
                CompletionChoice(
                    index=0,
                    message={
                        "role": "assistant",
                        "content": generation,
                    },
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=data.get("prompt_token_count", 0),
                completion_tokens=data.get("generation_token_count", 0),
                total_tokens=data.get("prompt_token_count", 0) + data.get("generation_token_count", 0),
            ),
        )
    
    def _transform_mistral_response(self, data: dict[str, Any]) -> CompletionResponse:
        """Transform Mistral response from Bedrock.
        
        Args:
            data: Response data
            
        Returns:
            CompletionResponse
        """
        outputs = data.get("outputs", [])
        text = outputs[0].get("text", "") if outputs else ""
        
        return CompletionResponse(
            id="bedrock-mistral-response",
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
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Make a chat completion request via Bedrock.

        Note: For Bedrock, api_key is used as the region if provided,
        otherwise AWS_REGION env var is used.

        Args:
            request: Completion request
            api_key: Optional region override
            api_base: Optional API base URL (not used for Bedrock)

        Returns:
            Completion response
        """
        if self._boto3_available:
            return await self._chat_completion_boto3(request, api_key)
        else:
            return await self._chat_completion_http(request, api_key)
    
    async def _chat_completion_boto3(
        self,
        request: CompletionRequest,
        region_override: Optional[str],
    ) -> CompletionResponse:
        """Make request using boto3.
        
        Args:
            request: Completion request
            region_override: Optional region override
            
        Returns:
            Completion response
        """
        import boto3
        
        region = region_override or self._get_region()
        
        bedrock = boto3.client(
            service_name="bedrock-runtime",
            region_name=region,
        )
        
        model_id = self._get_model_id(request.model)
        body = self.transform_request(request)
        
        try:
            response = bedrock.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
            )
            
            response_body = json.loads(response.get("body").read())
            return self.transform_response(response_body)
        except Exception as e:
            raise APIError(f"Bedrock request failed: {e}")
    
    async def _chat_completion_http(
        self,
        request: CompletionRequest,
        region_override: Optional[str],
    ) -> CompletionResponse:
        """Make request using HTTP with SigV4 signing.
        
        Args:
            request: Completion request
            region_override: Optional region override
            
        Returns:
            Completion response
        """
        # This would require implementing AWS SigV4 signing
        # For now, raise an error suggesting boto3
        raise APIError(
            "AWS Bedrock requires boto3. Install with: pip install boto3 "
            "or use the proxy server which handles authentication."
        )
