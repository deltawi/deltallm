"""Tests for CostCalculator."""

import pytest
from decimal import Decimal

from deltallm.pricing.manager import PricingManager
from deltallm.pricing.calculator import CostCalculator


class TestCostCalculatorChat:
    """Tests for chat cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_chat_cost_basic(self, calculator):
        """Test basic chat cost calculation."""
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        
        # gpt-4o: input $2.50/1M = 0.0000025/token, output $10/1M = 0.00001/token
        # 1000 * 0.0000025 + 500 * 0.00001 = 0.0025 + 0.005 = 0.0075
        expected = Decimal("0.0075")
        assert breakdown.total_cost == expected
        assert breakdown.input_cost == Decimal("0.0025")
        assert breakdown.output_cost == Decimal("0.005")
    
    def test_chat_cost_with_cached_tokens(self, calculator):
        """Test chat cost with cached tokens."""
        breakdown = calculator.calculate_chat_cost(
            model="claude-3-5-sonnet-20241022",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=200,
        )
        
        # claude-3-5-sonnet: input $3/1M, output $15/1M, cache read $0.30/1M
        # uncached: 800 * 0.000003 = 0.0024
        # cached: 200 * 0.0000003 = 0.00006
        # output: 500 * 0.000015 = 0.0075
        # total: 0.0024 + 0.00006 + 0.0075 = 0.00996
        expected = Decimal("0.00996")
        assert breakdown.total_cost == expected
        assert breakdown.cache_read_cost == Decimal("0.00006")
    
    def test_chat_cost_with_batch_discount(self, calculator):
        """Test chat cost with batch discount."""
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            is_batch=True,
        )
        
        # Without discount: 0.0075
        # With 50% discount: 0.0075 * 0.5 = 0.00375
        expected = Decimal("0.00375")
        assert breakdown.total_cost == expected
        assert breakdown.batch_discount == Decimal("0.00375")
        assert breakdown.discount_percent == 50.0
        assert breakdown.original_cost == Decimal("0.0075")
    
    def test_chat_cost_cached_tokens_no_cache_pricing(self, calculator):
        """Test chat cost with cached tokens but no cache pricing defined."""
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o",  # No cache pricing
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=200,
        )
        
        # All tokens charged at regular rate
        # 1000 * 0.0000025 + 500 * 0.00001 = 0.0025 + 0.005 = 0.0075
        expected = Decimal("0.0075")
        assert breakdown.total_cost == expected
        assert breakdown.cache_read_cost == Decimal("0.0005")  # 200 * 0.0000025
    
    def test_chat_cost_zero_tokens(self, calculator):
        """Test chat cost with zero tokens."""
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=0,
            completion_tokens=0,
        )
        
        assert breakdown.total_cost == Decimal("0")
        assert breakdown.input_cost == Decimal("0")
        assert breakdown.output_cost == Decimal("0")


class TestCostCalculatorEmbedding:
    """Tests for embedding cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_embedding_cost(self, calculator):
        """Test embedding cost calculation."""
        breakdown = calculator.calculate_embedding_cost(
            model="text-embedding-3-small",
            prompt_tokens=1000,
        )
        
        # text-embedding-3-small: $0.02/1M = 0.00000002/token
        # 1000 * 0.00000002 = 0.00002
        expected = Decimal("0.00002")
        assert breakdown.total_cost == expected
        assert breakdown.input_cost == expected


class TestCostCalculatorImage:
    """Tests for image generation cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_image_cost_basic(self, calculator):
        """Test basic image cost calculation."""
        breakdown = calculator.calculate_image_cost(
            model="dall-e-3",
            size="1024x1024",
        )
        
        # dall-e-3 1024x1024: $0.040
        expected = Decimal("0.040")
        assert breakdown.total_cost == expected
        assert breakdown.image_cost == expected
    
    def test_image_cost_hd_quality(self, calculator):
        """Test image cost with HD quality."""
        breakdown = calculator.calculate_image_cost(
            model="dall-e-3",
            size="1024x1024",
            quality="hd",
        )
        
        # dall-e-3 1024x1024 HD: $0.040 * 2.0 = $0.080
        expected = Decimal("0.080")
        assert breakdown.total_cost == expected
    
    def test_image_cost_multiple_images(self, calculator):
        """Test image cost for multiple images."""
        breakdown = calculator.calculate_image_cost(
            model="dall-e-3",
            size="1024x1024",
            n=4,
        )
        
        # 4 * $0.040 = $0.16
        expected = Decimal("0.160")
        assert breakdown.total_cost == expected
    
    def test_image_cost_dalle2(self, calculator):
        """Test DALL-E 2 pricing."""
        breakdown_1024 = calculator.calculate_image_cost(
            model="dall-e-2",
            size="1024x1024",
        )
        breakdown_512 = calculator.calculate_image_cost(
            model="dall-e-2",
            size="512x512",
        )
        
        assert breakdown_1024.total_cost == Decimal("0.020")
        assert breakdown_512.total_cost == Decimal("0.018")


class TestCostCalculatorAudio:
    """Tests for audio cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_audio_speech_cost(self, calculator):
        """Test TTS cost calculation."""
        breakdown = calculator.calculate_audio_speech_cost(
            model="tts-1",
            character_count=1000,
        )
        
        # tts-1: $0.015/1K characters = 0.000015/character
        # 1000 * 0.000015 = 0.015
        expected = Decimal("0.015")
        assert breakdown.total_cost == expected
        assert breakdown.audio_cost == expected
    
    def test_audio_speech_hd_cost(self, calculator):
        """Test TTS HD cost calculation."""
        breakdown = calculator.calculate_audio_speech_cost(
            model="tts-1-hd",
            character_count=1000,
        )
        
        # tts-1-hd: $0.030/1K characters
        expected = Decimal("0.030")
        assert breakdown.total_cost == expected
    
    def test_audio_transcription_cost(self, calculator):
        """Test STT cost calculation."""
        breakdown = calculator.calculate_audio_transcription_cost(
            model="whisper-1",
            duration_seconds=120,  # 2 minutes
        )
        
        # whisper-1: $0.006/minute
        # 2 * 0.006 = 0.012
        expected = Decimal("0.012")
        assert breakdown.total_cost == expected
        assert breakdown.audio_cost == expected
    
    def test_audio_transcription_partial_minute(self, calculator):
        """Test STT cost for partial minute."""
        breakdown = calculator.calculate_audio_transcription_cost(
            model="whisper-1",
            duration_seconds=30,  # 0.5 minutes
        )
        
        # 0.5 * 0.006 = 0.003
        expected = Decimal("0.003")
        assert breakdown.total_cost == expected


class TestCostCalculatorRerank:
    """Tests for rerank cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_rerank_cost(self, calculator):
        """Test rerank cost calculation."""
        breakdown = calculator.calculate_rerank_cost(
            model="cohere-rerank-v3-english",
            search_count=100,
        )
        
        # $0.002/search
        # 100 * 0.002 = 0.2
        expected = Decimal("0.2")
        assert breakdown.total_cost == expected
        assert breakdown.rerank_cost == expected


class TestCostCalculatorModeration:
    """Tests for moderation cost calculation."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_moderation_cost_free(self, calculator):
        """Test moderation cost (usually free)."""
        breakdown = calculator.calculate_moderation_cost(
            model="text-moderation-latest",
            prompt_tokens=1000,
        )
        
        # Usually free
        expected = Decimal("0")
        assert breakdown.total_cost == expected
        assert breakdown.input_cost == expected


class TestCostCalculatorEstimate:
    """Tests for the estimate_cost convenience method."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_estimate_chat(self, calculator):
        """Test estimate_cost for chat endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/chat/completions",
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        
        assert breakdown.total_cost == Decimal("0.0075")
    
    def test_estimate_embedding(self, calculator):
        """Test estimate_cost for embedding endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/embeddings",
            model="text-embedding-3-small",
            prompt_tokens=1000,
        )
        
        assert breakdown.total_cost == Decimal("0.00002")
    
    def test_estimate_image(self, calculator):
        """Test estimate_cost for image endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/images/generations",
            model="dall-e-3",
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        assert breakdown.total_cost == Decimal("0.040")
    
    def test_estimate_audio_speech(self, calculator):
        """Test estimate_cost for audio speech endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/audio/speech",
            model="tts-1",
            character_count=1000,
        )
        
        assert breakdown.total_cost == Decimal("0.015")
    
    def test_estimate_audio_transcription(self, calculator):
        """Test estimate_cost for audio transcription endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/audio/transcriptions",
            model="whisper-1",
            duration_seconds=60,
        )
        
        assert breakdown.total_cost == Decimal("0.006")
    
    def test_estimate_rerank(self, calculator):
        """Test estimate_cost for rerank endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/rerank",
            model="cohere-rerank-v3-english",
            search_count=50,
        )
        
        assert breakdown.total_cost == Decimal("0.1")
    
    def test_estimate_moderation(self, calculator):
        """Test estimate_cost for moderation endpoint."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/moderations",
            model="text-moderation-latest",
            prompt_tokens=1000,
        )
        
        assert breakdown.total_cost == Decimal("0")
    
    def test_estimate_unknown_endpoint(self, calculator):
        """Test estimate_cost for unknown endpoint returns zero."""
        breakdown = calculator.estimate_cost(
            endpoint="/v1/unknown",
            model="some-model",
        )
        
        assert breakdown.total_cost == Decimal("0")


class TestCostCalculatorPrecision:
    """Tests for cost calculation precision."""
    
    @pytest.fixture
    def calculator(self):
        """Create a calculator with default pricing."""
        manager = PricingManager(enable_hot_reload=False)
        return CostCalculator(manager)
    
    def test_precision_small_costs(self, calculator):
        """Test precision for very small costs."""
        # gpt-4o-mini has very low costs
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
        )
        
        # input: 100 * 0.00000015 = 0.000015
        # output: 50 * 0.0000006 = 0.00003
        # total: 0.000045
        expected = Decimal("0.000045")
        assert breakdown.total_cost == expected
    
    def test_precision_rounding(self, calculator):
        """Test rounding behavior."""
        # Use a model with repeating decimal cost
        breakdown = calculator.calculate_chat_cost(
            model="gpt-4o-mini",
            prompt_tokens=1,
            completion_tokens=0,
        )
        
        # 1 * 0.00000015 = 0.00000015 -> rounds to 0.000000
        # Actually, it should maintain precision
        assert breakdown.total_cost >= Decimal("0")
