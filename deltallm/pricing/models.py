"""Pricing data models for ProxyLLM.

This module defines the PricingConfig dataclass which represents
complete pricing configuration for any model/endpoint type.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
from decimal import Decimal


@dataclass(frozen=True)
class PricingConfig:
    """Complete pricing configuration for a model.
    
    Supports multiple pricing modes:
    - chat: Per-token pricing for input/output
    - embedding: Per-token pricing for input only
    - image_generation: Per-image pricing by size
    - audio_speech: Per-character pricing (TTS)
    - audio_transcription: Per-minute pricing (STT)
    - rerank: Per-search pricing
    - moderation: Per-token pricing (usually free)
    - batch: Token pricing with discount
    """
    
    model: str
    mode: Literal[
        "chat", "embedding", "image_generation", 
        "audio_speech", "audio_transcription", 
        "rerank", "moderation", "batch"
    ]
    
    # Token-based pricing (chat, embedding, moderation)
    input_cost_per_token: Decimal = Decimal("0")
    output_cost_per_token: Decimal = Decimal("0")
    
    # Prompt caching (Anthropic, OpenAI)
    cache_creation_input_token_cost: Optional[Decimal] = None
    cache_read_input_token_cost: Optional[Decimal] = None
    
    # Image generation pricing
    image_cost_per_image: Optional[Decimal] = None
    image_sizes: dict[str, Decimal] = field(default_factory=dict)
    quality_pricing: dict[str, float] = field(default_factory=dict)
    
    # Audio pricing
    audio_cost_per_character: Optional[Decimal] = None  # TTS
    audio_cost_per_minute: Optional[Decimal] = None      # STT
    
    # Rerank pricing
    rerank_cost_per_search: Optional[Decimal] = None
    
    # Batch settings
    batch_discount_percent: float = 50.0
    base_model: Optional[str] = None
    
    # Limits
    max_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    
    @property
    def has_token_pricing(self) -> bool:
        """Check if this config has token-based pricing."""
        return self.input_cost_per_token > 0 or self.output_cost_per_token > 0
    
    @property
    def has_image_pricing(self) -> bool:
        """Check if this config has image-based pricing."""
        return bool(self.image_sizes) or (self.image_cost_per_image and self.image_cost_per_image > 0)
    
    @property
    def has_audio_pricing(self) -> bool:
        """Check if this config has audio-based pricing."""
        return bool(self.audio_cost_per_character) or bool(self.audio_cost_per_minute)
    
    @property
    def has_cache_pricing(self) -> bool:
        """Check if this config has prompt caching pricing."""
        return bool(self.cache_creation_input_token_cost) or bool(self.cache_read_input_token_cost)
    
    def to_dict(self) -> dict:
        """Convert PricingConfig to dictionary."""
        result = {
            "model": self.model,
            "mode": self.mode,
            "input_cost_per_token": str(self.input_cost_per_token),
            "output_cost_per_token": str(self.output_cost_per_token),
            "image_sizes": {k: str(v) for k, v in self.image_sizes.items()},
            "quality_pricing": self.quality_pricing,
            "batch_discount_percent": self.batch_discount_percent,
            "max_tokens": self.max_tokens,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }
        
        # Add optional fields only if set
        if self.cache_creation_input_token_cost is not None:
            result["cache_creation_input_token_cost"] = str(self.cache_creation_input_token_cost)
        if self.cache_read_input_token_cost is not None:
            result["cache_read_input_token_cost"] = str(self.cache_read_input_token_cost)
        if self.image_cost_per_image is not None:
            result["image_cost_per_image"] = str(self.image_cost_per_image)
        if self.audio_cost_per_character is not None:
            result["audio_cost_per_character"] = str(self.audio_cost_per_character)
        if self.audio_cost_per_minute is not None:
            result["audio_cost_per_minute"] = str(self.audio_cost_per_minute)
        if self.rerank_cost_per_search is not None:
            result["rerank_cost_per_search"] = str(self.rerank_cost_per_search)
        if self.base_model is not None:
            result["base_model"] = self.base_model
            
        return result
    
    @classmethod
    def from_dict(cls, model: str, data: dict) -> "PricingConfig":
        """Create PricingConfig from dictionary."""
        # Parse image_sizes
        image_sizes = {}
        if "image_sizes" in data:
            image_sizes = {
                k: Decimal(str(v)) 
                for k, v in data["image_sizes"].items()
            }
        
        def to_decimal(value) -> Optional[Decimal]:
            """Convert value to Decimal if not None."""
            if value is None:
                return None
            return Decimal(str(value))
        
        return cls(
            model=model,
            mode=data.get("mode", "chat"),
            input_cost_per_token=Decimal(str(data.get("input_cost_per_token", 0))),
            output_cost_per_token=Decimal(str(data.get("output_cost_per_token", 0))),
            cache_creation_input_token_cost=to_decimal(data.get("cache_creation_input_token_cost")),
            cache_read_input_token_cost=to_decimal(data.get("cache_read_input_token_cost")),
            image_cost_per_image=to_decimal(data.get("image_cost_per_image")),
            image_sizes=image_sizes,
            quality_pricing=data.get("quality_pricing", {}),
            audio_cost_per_character=to_decimal(data.get("audio_cost_per_character")),
            audio_cost_per_minute=to_decimal(data.get("audio_cost_per_minute")),
            rerank_cost_per_search=to_decimal(data.get("rerank_cost_per_search")),
            batch_discount_percent=data.get("batch_discount_percent", 50.0),
            base_model=data.get("base_model"),
            max_tokens=data.get("max_tokens"),
            max_input_tokens=data.get("max_input_tokens"),
            max_output_tokens=data.get("max_output_tokens"),
        )


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for a request.
    
    Provides granular cost information including:
    - Total cost
    - Component costs (input, output, cache, etc.)
    - Discounts applied
    """
    
    total_cost: Decimal
    currency: str = "USD"
    
    # Token costs
    input_cost: Decimal = Decimal("0")
    output_cost: Decimal = Decimal("0")
    cache_creation_cost: Optional[Decimal] = None
    cache_read_cost: Optional[Decimal] = None
    
    # Other costs
    image_cost: Optional[Decimal] = None
    audio_cost: Optional[Decimal] = None
    rerank_cost: Optional[Decimal] = None
    
    # Discounts
    batch_discount: Optional[Decimal] = None
    discount_percent: Optional[float] = None
    
    @property
    def original_cost(self) -> Decimal:
        """Cost before discounts."""
        if self.batch_discount:
            return self.total_cost + self.batch_discount
        return self.total_cost
    
    def to_dict(self) -> dict:
        """Convert CostBreakdown to dictionary."""
        result = {
            "total_cost": str(self.total_cost),
            "currency": self.currency,
            "input_cost": str(self.input_cost),
            "output_cost": str(self.output_cost),
        }
        
        # Add optional fields
        optional_fields = [
            "cache_creation_cost", "cache_read_cost",
            "image_cost", "audio_cost", "rerank_cost",
            "batch_discount", "discount_percent"
        ]
        
        for field_name in optional_fields:
            value = getattr(self, field_name)
            if value is not None:
                if isinstance(value, Decimal):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value
                    
        return result
