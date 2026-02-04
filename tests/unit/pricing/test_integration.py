"""Integration tests and usage examples for the pricing module."""

import pytest
from decimal import Decimal

from deltallm.pricing import PricingManager, CostCalculator, PricingConfig


class TestPricingIntegration:
    """Integration tests showing real-world usage."""
    
    def test_complete_pricing_workflow(self):
        """Test a complete pricing workflow."""
        # Initialize manager with default config
        manager = PricingManager(enable_hot_reload=False)
        calculator = CostCalculator(manager)
        
        # Test 1: Chat completion cost
        chat_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=2000,
            completion_tokens=1000,
        )
        print(f"\nGPT-4o chat cost: ${chat_cost.total_cost}")
        assert chat_cost.total_cost > 0
        
        # Test 2: Claude with prompt caching
        claude_cost = calculator.calculate_chat_cost(
            model="claude-3-5-sonnet-20241022",
            prompt_tokens=5000,
            completion_tokens=2000,
            cached_tokens=4000,  # Most prompts are cached
        )
        print(f"Claude with caching: ${claude_cost.total_cost}")
        print(f"  - Cache read cost: ${claude_cost.cache_read_cost}")
        assert claude_cost.cache_read_cost is not None
        assert claude_cost.cache_read_cost > 0
        
        # Test 3: Batch processing (50% discount)
        batch_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=10000,
            completion_tokens=5000,
            is_batch=True,
        )
        print(f"\nBatch processing cost: ${batch_cost.total_cost}")
        print(f"  - Original cost: ${batch_cost.original_cost}")
        print(f"  - Discount: ${batch_cost.batch_discount} ({batch_cost.discount_percent}% off)")
        assert batch_cost.batch_discount is not None
        assert batch_cost.batch_discount > 0
        
        # Test 4: Image generation
        image_cost = calculator.calculate_image_cost(
            model="dall-e-3",
            size="1024x1024",
            quality="hd",
            n=4,
        )
        print(f"\nDALL-E 3 HD (4 images): ${image_cost.total_cost}")
        assert image_cost.total_cost == Decimal("0.320")  # 4 * 0.04 * 2.0
        
        # Test 5: Text-to-Speech
        tts_cost = calculator.calculate_audio_speech_cost(
            model="tts-1",
            character_count=5000,
        )
        print(f"TTS (5000 chars): ${tts_cost.total_cost}")
        assert tts_cost.total_cost == Decimal("0.075")  # 5000 * 0.000015
        
        # Test 6: Speech-to-Text
        stt_cost = calculator.calculate_audio_transcription_cost(
            model="whisper-1",
            duration_seconds=300,  # 5 minutes
        )
        print(f"STT (5 min): ${stt_cost.total_cost}")
        assert stt_cost.total_cost == Decimal("0.030")  # 5 * 0.006
        
        # Test 7: Embeddings
        embedding_cost = calculator.calculate_embedding_cost(
            model="text-embedding-3-small",
            prompt_tokens=10000,
        )
        print(f"\nEmbeddings (10K tokens): ${embedding_cost.total_cost}")
        assert embedding_cost.total_cost > 0
    
    def test_custom_pricing_override(self):
        """Test setting custom pricing for specific organizations."""
        from uuid import uuid4
        
        manager = PricingManager(enable_hot_reload=False)
        calculator = CostCalculator(manager)
        
        # Set custom pricing for a specific organization
        org_id = uuid4()
        custom_pricing = PricingConfig(
            model="gpt-4o",
            mode="chat",
            input_cost_per_token=Decimal("0.000001"),  # Special enterprise rate
            output_cost_per_token=Decimal("0.000005"),
        )
        manager.set_custom_pricing("gpt-4o", custom_pricing, org_id=org_id)
        
        # Default pricing
        default_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        
        # Custom org pricing
        org_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            org_id=org_id,
        )
        
        print(f"\nDefault cost: ${default_cost.total_cost}")
        print(f"Custom org cost: ${org_cost.total_cost}")
        
        # Custom org should be cheaper
        assert org_cost.total_cost < default_cost.total_cost
    
    def test_estimate_cost_convenience(self):
        """Test the estimate_cost convenience method."""
        calculator = CostCalculator(PricingManager(enable_hot_reload=False))
        
        # Can use endpoint paths directly
        estimates = {
            "chat": calculator.estimate_cost(
                "/v1/chat/completions",
                "gpt-4o",
                prompt_tokens=1000,
                completion_tokens=500,
            ),
            "embedding": calculator.estimate_cost(
                "/v1/embeddings",
                "text-embedding-3-small",
                prompt_tokens=1000,
            ),
            "image": calculator.estimate_cost(
                "/v1/images/generations",
                "dall-e-3",
                size="1024x1024",
            ),
            "tts": calculator.estimate_cost(
                "/v1/audio/speech",
                "tts-1",
                character_count=1000,
            ),
            "stt": calculator.estimate_cost(
                "/v1/audio/transcriptions",
                "whisper-1",
                duration_seconds=60,
            ),
        }
        
        print("\nCost estimates by endpoint:")
        for endpoint, breakdown in estimates.items():
            print(f"  {endpoint}: ${breakdown.total_cost}")
            assert breakdown.total_cost >= 0
    
    def test_yaml_config_loading(self, tmp_path):
        """Test loading pricing from YAML config."""
        import yaml
        
        # Create a test config
        config_data = {
            "version": "1.0",
            "pricing": {
                "custom-llm": {
                    "mode": "chat",
                    "input_cost_per_token": 0.000001,
                    "output_cost_per_token": 0.000002,
                    "max_tokens": 32768,
                },
                "custom-embedding": {
                    "mode": "embedding",
                    "input_cost_per_token": 0.00000005,
                    "max_tokens": 8192,
                },
            }
        }
        
        config_file = tmp_path / "test_pricing.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        # Load the config
        manager = PricingManager(config_path=str(config_file), enable_hot_reload=False)
        calculator = CostCalculator(manager)
        
        # Test custom models
        chat_cost = calculator.calculate_chat_cost(
            "custom-llm",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert chat_cost.total_cost == Decimal("0.002")  # 1000*0.000001 + 500*0.000002
        
        embedding_cost = calculator.calculate_embedding_cost(
            "custom-embedding",
            prompt_tokens=1000,
        )
        assert embedding_cost.total_cost == Decimal("0.00005")  # 1000*0.00000005


class TestPricingExamples:
    """Usage examples demonstrating the pricing system."""
    
    def test_example_budget_estimation(self):
        """Example: Estimating budget for various workloads."""
        calculator = CostCalculator(PricingManager(enable_hot_reload=False))
        
        # Scenario: Processing 1M tokens through different models
        scenarios = [
            ("gpt-4o", "high-quality"),
            ("gpt-4o-mini", "cost-effective"),
            ("claude-3-5-sonnet-20241022", "anthropic"),
        ]
        
        print("\nBudget estimation for 1M input + 500K output tokens:")
        for model, description in scenarios:
            cost = calculator.calculate_chat_cost(
                model=model,
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
            )
            print(f"  {model} ({description}): ${cost.total_cost}")
            assert cost.total_cost > 0
    
    def test_example_cost_comparison(self):
        """Example: Comparing costs between different approaches."""
        calculator = CostCalculator(PricingManager(enable_hot_reload=False))
        
        # Real-time vs batch processing
        realtime_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=100_000,
            completion_tokens=50_000,
            is_batch=False,
        )
        
        batch_cost = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=100_000,
            completion_tokens=50_000,
            is_batch=True,
        )
        
        print(f"\nReal-time processing: ${realtime_cost.total_cost}")
        print(f"Batch processing: ${batch_cost.total_cost}")
        print(f"Savings with batch: ${realtime_cost.total_cost - batch_cost.total_cost}")
        
        assert batch_cost.total_cost < realtime_cost.total_cost
        assert batch_cost.total_cost == realtime_cost.total_cost / 2
    
    def test_example_multimodal_costing(self):
        """Example: Costing a multimodal workflow."""
        calculator = CostCalculator(PricingManager(enable_hot_reload=False))
        
        # Workflow: Transcribe audio -> Generate response -> Create image
        workflow_costs = []
        
        # 1. Transcribe 10 minutes of audio
        stt_cost = calculator.calculate_audio_transcription_cost(
            "whisper-1",
            duration_seconds=600,
        )
        workflow_costs.append(("STT (10 min)", stt_cost.total_cost))
        
        # 2. Process transcript with GPT-4 (~2K tokens)
        chat_cost = calculator.calculate_chat_cost(
            "gpt-4o",
            prompt_tokens=2000,
            completion_tokens=500,
        )
        workflow_costs.append(("Chat (2K in, 500 out)", chat_cost.total_cost))
        
        # 3. Generate an image based on response
        image_cost = calculator.calculate_image_cost(
            "dall-e-3",
            size="1024x1024",
            quality="standard",
        )
        workflow_costs.append(("Image (1024x1024)", image_cost.total_cost))
        
        print("\nMultimodal workflow costs:")
        total = Decimal("0")
        for step, cost in workflow_costs:
            print(f"  {step}: ${cost}")
            total += cost
        print(f"  Total: ${total}")
        
        assert total > 0
