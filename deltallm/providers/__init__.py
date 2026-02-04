"""Provider adapters for ProxyLLM."""

from .base import BaseProvider, ModelInfo, ProviderCapabilities
from .registry import ProviderRegistry, register_provider, get_provider

# Import providers to auto-register them
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from .azure import AzureOpenAIProvider
from .bedrock import AWSBedrockProvider
from .gemini import GeminiProvider
from .cohere import CohereProvider
from .mistral import MistralProvider
from .groq import GroqProvider
from .vllm import VLLMProvider
from .ollama import OllamaProvider

__all__ = [
    "BaseProvider",
    "ModelInfo",
    "ProviderCapabilities",
    "ProviderRegistry",
    "register_provider",
    "get_provider",
    # Provider classes
    "OpenAIProvider",
    "AnthropicProvider",
    "AzureOpenAIProvider",
    "AWSBedrockProvider",
    "GeminiProvider",
    "CohereProvider",
    "MistralProvider",
    "GroqProvider",
    "VLLMProvider",
    "OllamaProvider",
]
