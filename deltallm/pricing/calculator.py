"""Cost calculator for ProxyLLM.

Provides cost calculation for different endpoint types:
- Chat completions
- Embeddings
- Image generations
- Audio (TTS/STT)
- Rerank
- Moderations
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, TYPE_CHECKING
from uuid import UUID

from .models import PricingConfig, CostBreakdown

if TYPE_CHECKING:
    from .manager import PricingManager


class CostCalculator:
    """Calculate costs for different endpoint types.
    
    Usage:
        calculator = CostCalculator(pricing_manager)
        
        # Chat completion
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        
        # Image generation
        breakdown = calculator.calculate_image_cost(
            model="dall-e-3",
            size="1024x1024",
            quality="hd",
        )
    """
    
    def __init__(self, pricing_manager: "PricingManager"):
        """Initialize calculator with pricing manager.
        
        Args:
            pricing_manager: PricingManager instance for looking up prices
        """
        self.pricing = pricing_manager
    
    def calculate_chat_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        is_batch: bool = False,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for chat/completion request.
        
        Args:
            model: Model name
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            cached_tokens: Number of cached prompt tokens
            is_batch: Whether this is a batch request (50% discount)
            org_id: Organization for pricing lookup
            team_id: Team for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id, team_id)
        
        # Calculate input cost
        uncached_tokens = max(0, prompt_tokens - cached_tokens)
        input_cost = Decimal(uncached_tokens) * pricing.input_cost_per_token
        
        # Add cached token cost if applicable
        cache_read_cost: Optional[Decimal] = None
        if cached_tokens > 0:
            if pricing.cache_read_input_token_cost:
                cache_read_cost = Decimal(cached_tokens) * pricing.cache_read_input_token_cost
                input_cost += cache_read_cost
            else:
                # Fall back to regular pricing if no cache pricing
                cache_read_cost = Decimal(cached_tokens) * pricing.input_cost_per_token
                input_cost += cache_read_cost
        
        # Calculate output cost
        output_cost = Decimal(completion_tokens) * pricing.output_cost_per_token
        
        total = input_cost + output_cost
        
        # Apply batch discount
        batch_discount: Optional[Decimal] = None
        discount_percent: Optional[float] = None
        if is_batch and pricing.batch_discount_percent > 0:
            discount_percent = pricing.batch_discount_percent
            original_total = total
            discount_multiplier = Decimal(1 - discount_percent / 100)
            total = (total * discount_multiplier).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            batch_discount = (original_total - total).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        
        return CostBreakdown(
            total_cost=total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            input_cost=input_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            output_cost=output_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            cache_read_cost=cache_read_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) if cache_read_cost else None,
            batch_discount=batch_discount,
            discount_percent=discount_percent,
        )
    
    def calculate_embedding_cost(
        self,
        model: str,
        prompt_tokens: int,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for embedding request.
        
        Args:
            model: Model name
            prompt_tokens: Number of input tokens
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        cost = Decimal(prompt_tokens) * pricing.input_cost_per_token
        
        return CostBreakdown(
            total_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            input_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def calculate_image_cost(
        self,
        model: str,
        size: str,
        quality: str = "standard",
        n: int = 1,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for image generation.
        
        Args:
            model: Model name (dall-e-3, etc.)
            size: Image size (1024x1024, etc.)
            quality: Image quality (standard, hd)
            n: Number of images
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        
        # Get base cost for size
        if pricing.image_sizes and size in pricing.image_sizes:
            base_cost = pricing.image_sizes[size]
        elif pricing.image_cost_per_image:
            base_cost = pricing.image_cost_per_image
        else:
            base_cost = Decimal("0")
        
        # Apply quality multiplier
        quality_multiplier = pricing.quality_pricing.get(quality, 1.0)
        
        total = base_cost * Decimal(quality_multiplier) * n
        
        return CostBreakdown(
            total_cost=total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            image_cost=total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def calculate_audio_speech_cost(
        self,
        model: str,
        character_count: int,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for TTS (text-to-speech).
        
        Args:
            model: Model name (tts-1, tts-1-hd)
            character_count: Number of characters in input text
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        
        if pricing.audio_cost_per_character:
            cost = Decimal(character_count) * pricing.audio_cost_per_character
        else:
            cost = Decimal("0")
        
        return CostBreakdown(
            total_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            audio_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def calculate_audio_transcription_cost(
        self,
        model: str,
        duration_seconds: float,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for STT (speech-to-text).
        
        Args:
            model: Model name (whisper-1)
            duration_seconds: Audio duration in seconds
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        
        if pricing.audio_cost_per_minute:
            # Convert seconds to minutes
            duration_minutes = duration_seconds / 60.0
            cost = Decimal(duration_minutes) * pricing.audio_cost_per_minute
        else:
            cost = Decimal("0")
        
        return CostBreakdown(
            total_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            audio_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def calculate_rerank_cost(
        self,
        model: str,
        search_count: int,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for rerank request.
        
        Args:
            model: Model name (cohere-rerank-v3-english)
            search_count: Number of documents to rerank
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        
        if pricing.rerank_cost_per_search:
            cost = Decimal(search_count) * pricing.rerank_cost_per_search
        else:
            cost = Decimal("0")
        
        return CostBreakdown(
            total_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            rerank_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def calculate_moderation_cost(
        self,
        model: str,
        prompt_tokens: int,
        org_id: Optional[UUID] = None,
    ) -> CostBreakdown:
        """Calculate cost for moderation request.
        
        Usually free, but we track it anyway.
        
        Args:
            model: Model name (text-moderation-latest)
            prompt_tokens: Number of input tokens
            org_id: Organization for pricing lookup
            
        Returns:
            CostBreakdown with detailed cost information
        """
        pricing = self.pricing.get_pricing(model, org_id)
        cost = Decimal(prompt_tokens) * pricing.input_cost_per_token
        
        return CostBreakdown(
            total_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            input_cost=cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )
    
    def estimate_cost(
        self,
        endpoint: str,
        model: str,
        **kwargs
    ) -> CostBreakdown:
        """Estimate cost for any endpoint type.
        
        This is a convenience method that routes to the appropriate
        calculator based on endpoint type.
        
        Args:
            endpoint: Endpoint path (/v1/chat/completions, etc.)
            model: Model name
            **kwargs: Endpoint-specific parameters
            
        Returns:
            CostBreakdown with detailed cost information
        """
        endpoint = endpoint.lower()
        
        if endpoint in ("/v1/chat/completions", "chat"):
            return self.calculate_chat_cost(
                model=model,
                prompt_tokens=kwargs.get("prompt_tokens", 0),
                completion_tokens=kwargs.get("completion_tokens", 0),
                cached_tokens=kwargs.get("cached_tokens", 0),
                is_batch=kwargs.get("is_batch", False),
                org_id=kwargs.get("org_id"),
                team_id=kwargs.get("team_id"),
            )
        
        elif endpoint in ("/v1/embeddings", "embedding"):
            return self.calculate_embedding_cost(
                model=model,
                prompt_tokens=kwargs.get("prompt_tokens", 0),
                org_id=kwargs.get("org_id"),
            )
        
        elif endpoint in ("/v1/images/generations", "image"):
            return self.calculate_image_cost(
                model=model,
                size=kwargs.get("size", "1024x1024"),
                quality=kwargs.get("quality", "standard"),
                n=kwargs.get("n", 1),
                org_id=kwargs.get("org_id"),
            )
        
        elif endpoint in ("/v1/audio/speech", "audio_speech"):
            return self.calculate_audio_speech_cost(
                model=model,
                character_count=kwargs.get("character_count", 0),
                org_id=kwargs.get("org_id"),
            )
        
        elif endpoint in ("/v1/audio/transcriptions", "/v1/audio/translations", 
                         "audio_transcription"):
            return self.calculate_audio_transcription_cost(
                model=model,
                duration_seconds=kwargs.get("duration_seconds", 0),
                org_id=kwargs.get("org_id"),
            )
        
        elif endpoint in ("/v1/rerank", "rerank"):
            return self.calculate_rerank_cost(
                model=model,
                search_count=kwargs.get("search_count", 0),
                org_id=kwargs.get("org_id"),
            )
        
        elif endpoint in ("/v1/moderations", "moderation"):
            return self.calculate_moderation_cost(
                model=model,
                prompt_tokens=kwargs.get("prompt_tokens", 0),
                org_id=kwargs.get("org_id"),
            )
        
        else:
            # Unknown endpoint, return $0 cost
            return CostBreakdown(
                total_cost=Decimal("0"),
                input_cost=Decimal("0"),
                output_cost=Decimal("0"),
            )
