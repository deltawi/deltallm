"""Provider registry for managing provider adapters."""

from typing import Optional, Type
import re

from deltallm.exceptions import ModelNotSupportedError
from .base import BaseProvider


class ProviderRegistry:
    """Registry for provider adapters."""
    
    _providers: dict[str, Type[BaseProvider]] = {}
    _model_mappings: dict[str, str] = {}  # model -> provider_name
    
    @classmethod
    def register(
        cls, 
        provider_name: str, 
        provider_class: Type[BaseProvider],
        models: Optional[list[str]] = None
    ) -> None:
        """Register a provider.
        
        Args:
            provider_name: The provider identifier
            provider_class: The provider class
            models: Optional list of supported model names/patterns
        """
        cls._providers[provider_name] = provider_class
        
        if models:
            for model in models:
                cls._model_mappings[model] = provider_name
    
    @classmethod
    def get(cls, provider_name: str) -> Type[BaseProvider]:
        """Get a provider by name.
        
        Args:
            provider_name: The provider identifier
            
        Returns:
            The provider class
            
        Raises:
            KeyError: If provider is not registered
        """
        if provider_name not in cls._providers:
            raise KeyError(f"Provider '{provider_name}' is not registered")
        return cls._providers[provider_name]
    
    @classmethod
    def get_for_model(cls, model: str) -> Type[BaseProvider]:
        """Get a provider for a model.
        
        Supports formats:
        - "provider/model-name" -> uses specified provider
        - "model-name" -> auto-detects provider
        
        Args:
            model: The model name
            
        Returns:
            The provider class
            
        Raises:
            ModelNotSupportedError: If no provider supports the model
        """
        # Check if model includes provider prefix
        if "/" in model:
            provider_name, model_name = model.split("/", 1)
            if provider_name in cls._providers:
                return cls._providers[provider_name]
            raise ModelNotSupportedError(model_name, provider_name)
        
        # Try exact match
        if model in cls._model_mappings:
            provider_name = cls._model_mappings[model]
            return cls._providers[provider_name]
        
        # Try pattern matching
        for pattern, provider_name in cls._model_mappings.items():
            if cls._match_pattern(model, pattern):
                return cls._providers[provider_name]
        
        # Try each provider's supports_model method
        for provider_name, provider_class in cls._providers.items():
            # Create a temporary instance to check support
            try:
                temp = provider_class()
                if temp.supports_model(model):
                    return provider_class
            except Exception:
                continue
        
        raise ModelNotSupportedError(model)
    
    @classmethod
    def _match_pattern(cls, model: str, pattern: str) -> bool:
        """Check if a model matches a pattern.
        
        Args:
            model: The model name
            pattern: The pattern (supports * wildcards)
            
        Returns:
            True if the model matches
        """
        # Convert pattern to regex
        regex = pattern.replace("*", ".*")
        return bool(re.match(f"^{regex}$", model))
    
    @classmethod
    def get_by_type(cls, provider_type: str) -> Optional[Type[BaseProvider]]:
        """Get a provider by type name.

        This is used by the DynamicRouter to get provider classes
        based on the provider_type stored in ProviderConfig.

        Args:
            provider_type: The provider type (openai, anthropic, etc.)

        Returns:
            The provider class or None if not found
        """
        return cls._providers.get(provider_type)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names.

        Returns:
            List of provider names
        """
        return list(cls._providers.keys())
    
    @classmethod
    def list_models(cls) -> list[str]:
        """List all registered models.
        
        Returns:
            List of model names
        """
        return list(cls._model_mappings.keys())
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered providers (mainly for testing)."""
        cls._providers.clear()
        cls._model_mappings.clear()


def register_provider(
    provider_name: str,
    models: Optional[list[str]] = None
) -> callable:
    """Decorator to register a provider class.
    
    Args:
        provider_name: The provider identifier
        models: Optional list of supported models
        
    Returns:
        Decorator function
    """
    def decorator(cls: Type[BaseProvider]) -> Type[BaseProvider]:
        ProviderRegistry.register(provider_name, cls, models)
        cls.provider_name = provider_name
        return cls
    return decorator


def get_provider(provider_name: str) -> Type[BaseProvider]:
    """Get a provider by name.
    
    Args:
        provider_name: The provider identifier
        
    Returns:
        The provider class
    """
    return ProviderRegistry.get(provider_name)
