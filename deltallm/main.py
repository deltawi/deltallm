"""Main completion functions for ProxyLLM."""

import os
from typing import Any, AsyncIterator, Optional, Union

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    Message,
    EmbeddingRequest,
    EmbeddingResponse,
)
from deltallm.exceptions import (
    ProxyLLMError,
    AuthenticationError,
    ModelNotSupportedError,
)
from deltallm.providers.registry import ProviderRegistry
from deltallm.utils.pricing import calculate_cost


async def completion(
    model: str,
    messages: list[Message],
    *,
    # Authentication
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    # Generation parameters
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_completion_tokens: Optional[int] = None,
    stop: Optional[Union[str, list[str]]] = None,
    # Request options
    stream: bool = False,
    timeout: Optional[float] = None,
    # Retry/fallback
    num_retries: int = 0,
    fallbacks: Optional[list[str]] = None,
    # Additional params
    **kwargs
) -> Union[CompletionResponse, AsyncIterator[StreamChunk]]:
    """Execute a chat completion request.
    
    Args:
        model: Model name (e.g., "gpt-4o", "anthropic/claude-3-sonnet")
        messages: List of messages
        api_key: API key (if not set, uses environment variable)
        api_base: Custom API base URL
        temperature: Sampling temperature (0-2)
        top_p: Nucleus sampling parameter
        max_tokens: Maximum tokens to generate
        max_completion_tokens: Alternative to max_tokens
        stop: Stop sequences
        stream: Whether to stream the response
        timeout: Request timeout in seconds
        num_retries: Number of retries on failure
        fallbacks: Fallback models if this one fails
        **kwargs: Additional parameters
        
    Returns:
        Completion response or async iterator of stream chunks
        
    Examples:
        ```python
        # Simple completion
        response = await completion(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello!"}]
        )
        print(response.choices[0].message["content"])
        
        # With specific provider
        response = await completion(
            model="anthropic/claude-3-sonnet-20240229",
            messages=[{"role": "user", "content": "Hello!"}],
            api_key="your-api-key"
        )
        
        # Streaming
        async for chunk in await completion(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello!"}],
            stream=True
        ):
            if chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="")
        ```
    """
    # Get provider for the model
    provider_class = ProviderRegistry.get_for_model(model)
    
    # Get API key from environment if not provided
    if api_key is None:
        env_var = f"{provider_class.provider_name.upper()}_API_KEY"
        api_key = os.environ.get(env_var)
    
    if not api_key and provider_class.provider_name != "openai":
        # Try generic key for openai-compatible endpoints
        api_key = os.environ.get("OPENAI_API_KEY")
    
    # Create provider instance
    provider = provider_class(api_key=api_key, api_base=api_base)
    
    # Build request
    request = CompletionRequest(
        model=model.split("/")[-1] if "/" in model else model,
        messages=messages if isinstance(messages, list) else [Message.user(messages)],
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        max_completion_tokens=max_completion_tokens,
        stop=stop,
        stream=stream,
        timeout=timeout,
        **kwargs
    )
    
    # Execute request
    if stream:
        return await provider.chat_completion_stream(request)
    else:
        return await provider.chat_completion(request)


# Alias for async completion
acompletion = completion


def completion_sync(
    model: str,
    messages: list[Message],
    **kwargs
) -> CompletionResponse:
    """Synchronous version of completion.
    
    Args:
        model: Model name
        messages: List of messages
        **kwargs: Additional arguments passed to completion()
        
    Returns:
        Completion response
    """
    import asyncio
    return asyncio.run(completion(model, messages, **kwargs))


async def embedding(
    model: str,
    input: Union[str, list[str]],
    *,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    encoding_format: str = "float",
    dimensions: Optional[int] = None,
    timeout: Optional[float] = None,
) -> EmbeddingResponse:
    """Execute an embedding request.
    
    Args:
        model: Model name (e.g., "text-embedding-3-small")
        input: Text or list of texts to embed
        api_key: API key
        api_base: Custom API base URL
        encoding_format: "float" or "base64"
        dimensions: Number of dimensions to return
        timeout: Request timeout
        
    Returns:
        Embedding response
        
    Examples:
        ```python
        # Single text
        response = await embedding(
            model="text-embedding-3-small",
            input="Hello world"
        )
        print(response.data[0].embedding)
        
        # Multiple texts
        response = await embedding(
            model="text-embedding-3-small",
            input=["Hello", "World"]
        )
        for item in response.data:
            print(item.embedding)
        ```
    """
    # Get provider for the model
    provider_class = ProviderRegistry.get_for_model(model)
    
    # Get API key from environment if not provided
    if api_key is None:
        env_var = f"{provider_class.provider_name.upper()}_API_KEY"
        api_key = os.environ.get(env_var)
    
    # Create provider instance
    provider = provider_class(api_key=api_key, api_base=api_base)
    
    # Build request
    request = EmbeddingRequest(
        model=model.split("/")[-1] if "/" in model else model,
        input=input,
        encoding_format=encoding_format,  # type: ignore
        dimensions=dimensions,
    )
    
    return await provider.embedding(request)


def embedding_sync(
    model: str,
    input: Union[str, list[str]],
    **kwargs
) -> EmbeddingResponse:
    """Synchronous version of embedding.
    
    Args:
        model: Model name
        input: Text or list of texts
        **kwargs: Additional arguments
        
    Returns:
        Embedding response
    """
    import asyncio
    return asyncio.run(embedding(model, input, **kwargs))


async def image_generation(
    prompt: str,
    model: str = "dall-e-2",
    *,
    api_key: Optional[str] = None,
    size: str = "1024x1024",
    quality: str = "standard",
    n: int = 1,
    **kwargs
) -> dict[str, Any]:
    """Generate images from text prompts.
    
    Args:
        prompt: Text description of the image
        model: Image generation model
        api_key: API key
        size: Image size
        quality: Image quality
        n: Number of images
        **kwargs: Additional parameters
        
    Returns:
        Image generation response
        
    Raises:
        NotImplementedError: If the provider doesn't support image generation
    """
    raise NotImplementedError("Image generation not yet implemented")


# Import provider modules to register them
def _register_default_providers():
    """Import and register default providers."""
    try:
        from deltallm.providers.openai import OpenAIProvider
    except ImportError:
        pass
    
    try:
        from deltallm.providers.anthropic import AnthropicProvider
    except ImportError:
        pass


_register_default_providers()
