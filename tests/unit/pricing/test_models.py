"""Tests for pricing models."""

import pytest
from decimal import Decimal
from deltallm.pricing.models import PricingConfig, CostBreakdown


class TestPricingConfig:
    """Tests for PricingConfig dataclass."""
    
    def test_basic_creation(self):
        """Test basic PricingConfig creation."""
        config = PricingConfig(
            model="gpt-4o",
            mode="chat",
            input_cost_per_token=Decimal("0.000005"),
            output_cost_per_token=Decimal("0.000015"),
        )
        
        assert config.model == "gpt-4o"
        assert config.mode == "chat"
        assert config.input_cost_per_token == Decimal("0.000005")
        assert config.output_cost_per_token == Decimal("0.000015")
    
    def test_default_values(self):
        """Test that default values are set correctly."""
        config = PricingConfig(
            model="unknown",
            mode="chat",
        )
        
        assert config.input_cost_per_token == Decimal("0")
        assert config.output_cost_per_token == Decimal("0")
        assert config.image_sizes == {}
        assert config.quality_pricing == {}
        assert config.batch_discount_percent == 50.0
    
    def test_has_token_pricing(self):
        """Test has_token_pricing property."""
        no_pricing = PricingConfig(model="test", mode="chat")
        assert not no_pricing.has_token_pricing
        
        input_only = PricingConfig(
            model="test", mode="chat",
            input_cost_per_token=Decimal("0.001")
        )
        assert input_only.has_token_pricing
        
        output_only = PricingConfig(
            model="test", mode="chat",
            output_cost_per_token=Decimal("0.001")
        )
        assert output_only.has_token_pricing
    
    def test_has_image_pricing(self):
        """Test has_image_pricing property."""
        no_pricing = PricingConfig(model="test", mode="image_generation")
        assert not no_pricing.has_image_pricing
        
        with_sizes = PricingConfig(
            model="test", mode="image_generation",
            image_sizes={"1024x1024": Decimal("0.04")}
        )
        assert with_sizes.has_image_pricing
        
        with_cost = PricingConfig(
            model="test", mode="image_generation",
            image_cost_per_image=Decimal("0.04")
        )
        assert with_cost.has_image_pricing
    
    def test_has_audio_pricing(self):
        """Test has_audio_pricing property."""
        no_pricing = PricingConfig(model="test", mode="audio_speech")
        assert not no_pricing.has_audio_pricing
        
        tts = PricingConfig(
            model="test", mode="audio_speech",
            audio_cost_per_character=Decimal("0.000015")
        )
        assert tts.has_audio_pricing
        
        stt = PricingConfig(
            model="test", mode="audio_transcription",
            audio_cost_per_minute=Decimal("0.006")
        )
        assert stt.has_audio_pricing
    
    def test_has_cache_pricing(self):
        """Test has_cache_pricing property."""
        no_cache = PricingConfig(model="test", mode="chat")
        assert not no_cache.has_cache_pricing
        
        with_cache = PricingConfig(
            model="test", mode="chat",
            cache_creation_input_token_cost=Decimal("0.00000375"),
            cache_read_input_token_cost=Decimal("0.0000003"),
        )
        assert with_cache.has_cache_pricing
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = PricingConfig(
            model="gpt-4o",
            mode="chat",
            input_cost_per_token=Decimal("0.000005"),
            output_cost_per_token=Decimal("0.000015"),
            max_tokens=128000,
        )
        
        data = config.to_dict()
        
        assert data["model"] == "gpt-4o"
        assert data["mode"] == "chat"
        assert data["input_cost_per_token"] == "0.000005"
        assert data["max_tokens"] == 128000
    
    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "mode": "chat",
            "input_cost_per_token": 0.000005,
            "output_cost_per_token": "0.000015",
            "max_tokens": 128000,
        }
        
        config = PricingConfig.from_dict("gpt-4o", data)
        
        assert config.model == "gpt-4o"
        assert config.mode == "chat"
        assert config.input_cost_per_token == Decimal("0.000005")
        assert config.output_cost_per_token == Decimal("0.000015")
        assert config.max_tokens == 128000
    
    def test_from_dict_with_image_sizes(self):
        """Test creation from dict with image sizes."""
        data = {
            "mode": "image_generation",
            "image_sizes": {
                "1024x1024": 0.04,
                "1024x1792": 0.08,
            },
            "quality_pricing": {
                "standard": 1.0,
                "hd": 2.0,
            },
        }
        
        config = PricingConfig.from_dict("dall-e-3", data)
        
        assert config.mode == "image_generation"
        assert config.image_sizes["1024x1024"] == Decimal("0.04")
        assert config.image_sizes["1024x1792"] == Decimal("0.08")
        assert config.quality_pricing["hd"] == 2.0
    
    def test_from_dict_with_cache_pricing(self):
        """Test creation from dict with cache pricing."""
        data = {
            "mode": "chat",
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
            "cache_creation_input_token_cost": 0.00000375,
            "cache_read_input_token_cost": 0.0000003,
        }
        
        config = PricingConfig.from_dict("claude-3-5-sonnet", data)
        
        assert config.cache_creation_input_token_cost == Decimal("0.00000375")
        assert config.cache_read_input_token_cost == Decimal("0.0000003")


class TestCostBreakdown:
    """Tests for CostBreakdown dataclass."""
    
    def test_basic_creation(self):
        """Test basic CostBreakdown creation."""
        breakdown = CostBreakdown(
            total_cost=Decimal("0.011"),
            input_cost=Decimal("0.005"),
            output_cost=Decimal("0.006"),
        )
        
        assert breakdown.total_cost == Decimal("0.011")
        assert breakdown.input_cost == Decimal("0.005")
        assert breakdown.output_cost == Decimal("0.006")
        assert breakdown.currency == "USD"
    
    def test_original_cost_no_discount(self):
        """Test original_cost property without discount."""
        breakdown = CostBreakdown(
            total_cost=Decimal("0.010"),
        )
        
        assert breakdown.original_cost == Decimal("0.010")
    
    def test_original_cost_with_discount(self):
        """Test original_cost property with discount."""
        breakdown = CostBreakdown(
            total_cost=Decimal("0.005"),
            batch_discount=Decimal("0.005"),
        )
        
        assert breakdown.original_cost == Decimal("0.010")
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        breakdown = CostBreakdown(
            total_cost=Decimal("0.011"),
            input_cost=Decimal("0.005"),
            output_cost=Decimal("0.006"),
            cache_read_cost=Decimal("0.0001"),
            batch_discount=Decimal("0.001"),
            discount_percent=50.0,
        )
        
        data = breakdown.to_dict()
        
        assert data["total_cost"] == "0.011"
        assert data["input_cost"] == "0.005"
        assert data["output_cost"] == "0.006"
        assert data["cache_read_cost"] == "0.0001"
        assert data["batch_discount"] == "0.001"
        assert data["discount_percent"] == 50.0
    
    def test_to_dict_skips_none(self):
        """Test that None values are skipped in to_dict."""
        breakdown = CostBreakdown(
            total_cost=Decimal("0.01"),
        )
        
        data = breakdown.to_dict()
        
        assert "image_cost" not in data
        assert "audio_cost" not in data
        assert "cache_read_cost" not in data
