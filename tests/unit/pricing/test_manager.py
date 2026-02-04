"""Tests for PricingManager."""

import os
import pytest
import tempfile
from decimal import Decimal
from uuid import uuid4

from deltallm.pricing.manager import PricingManager
from deltallm.pricing.models import PricingConfig


class TestPricingManager:
    """Tests for PricingManager."""
    
    def test_default_initialization(self):
        """Test manager loads defaults on initialization."""
        manager = PricingManager(enable_hot_reload=False)
        
        # Check that default models are loaded
        pricing = manager.get_pricing("gpt-4o")
        assert pricing.model == "gpt-4o"
        assert pricing.input_cost_per_token > 0
    
    def test_get_pricing_unknown_model(self):
        """Test getting pricing for unknown model returns default."""
        manager = PricingManager(enable_hot_reload=False)
        
        pricing = manager.get_pricing("unknown-model-xyz")
        
        assert pricing.model == "unknown-model-xyz"
        assert pricing.input_cost_per_token == Decimal("0")
    
    def test_get_pricing_prefix_matching(self):
        """Test prefix matching for model variants."""
        manager = PricingManager(enable_hot_reload=False)
        
        # Should match gpt-4o for date-suffixed versions
        pricing = manager.get_pricing("gpt-4o-2024-08-06")
        
        # Should get gpt-4o pricing (or variant)
        assert pricing.input_cost_per_token > 0
    
    def test_yaml_config_loading(self):
        """Test loading pricing from YAML file."""
        # Create a temporary YAML file
        yaml_content = """
version: "1.0"
pricing:
  custom-model:
    mode: chat
    input_cost_per_token: 0.000001
    output_cost_per_token: 0.000002
    max_tokens: 4096
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name
        
        try:
            manager = PricingManager(config_path=temp_path, enable_hot_reload=False)
            
            pricing = manager.get_pricing("custom-model")
            assert pricing.model == "custom-model"
            assert pricing.input_cost_per_token == Decimal("0.000001")
            assert pricing.output_cost_per_token == Decimal("0.000002")
            assert pricing.max_tokens == 4096
        finally:
            os.unlink(temp_path)
    
    def test_yaml_overrides_defaults(self):
        """Test that YAML config overrides default pricing."""
        yaml_content = """
version: "1.0"
pricing:
  gpt-4o:
    mode: chat
    input_cost_per_token: 0.000001
    output_cost_per_token: 0.000002
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name
        
        try:
            manager = PricingManager(config_path=temp_path, enable_hot_reload=False)
            
            pricing = manager.get_pricing("gpt-4o")
            # Should be overridden values from YAML
            assert pricing.input_cost_per_token == Decimal("0.000001")
            assert pricing.output_cost_per_token == Decimal("0.000002")
        finally:
            os.unlink(temp_path)
    
    def test_set_custom_pricing_global(self):
        """Test setting global custom pricing."""
        manager = PricingManager(enable_hot_reload=False)
        
        custom_pricing = PricingConfig(
            model="custom-model",
            mode="chat",
            input_cost_per_token=Decimal("0.0001"),
        )
        
        manager.set_custom_pricing("custom-model", custom_pricing)
        
        pricing = manager.get_pricing("custom-model")
        assert pricing.input_cost_per_token == Decimal("0.0001")
    
    def test_set_custom_pricing_org_level(self):
        """Test setting org-level custom pricing."""
        manager = PricingManager(enable_hot_reload=False)
        org_id = uuid4()
        
        custom_pricing = PricingConfig(
            model="gpt-4o",
            mode="chat",
            input_cost_per_token=Decimal("0.000001"),  # Special org rate
        )
        
        manager.set_custom_pricing("gpt-4o", custom_pricing, org_id=org_id)
        
        # Without org_id, should get default
        default_pricing = manager.get_pricing("gpt-4o")
        assert default_pricing.input_cost_per_token != Decimal("0.000001")
        
        # With org_id, should get custom
        org_pricing = manager.get_pricing("gpt-4o", org_id=org_id)
        assert org_pricing.input_cost_per_token == Decimal("0.000001")
    
    def test_set_custom_pricing_team_level(self):
        """Test setting team-level custom pricing."""
        manager = PricingManager(enable_hot_reload=False)
        org_id = uuid4()
        team_id = uuid4()
        
        custom_pricing = PricingConfig(
            model="gpt-4o",
            mode="chat",
            input_cost_per_token=Decimal("0.0000005"),  # Special team rate
        )
        
        manager.set_custom_pricing("gpt-4o", custom_pricing, org_id=org_id, team_id=team_id)
        
        # With org_id only, should get default (not team rate)
        org_pricing = manager.get_pricing("gpt-4o", org_id=org_id)
        assert org_pricing.input_cost_per_token != Decimal("0.0000005")
        
        # With both org_id and team_id, should get team rate
        team_pricing = manager.get_pricing("gpt-4o", org_id=org_id, team_id=team_id)
        assert team_pricing.input_cost_per_token == Decimal("0.0000005")
    
    def test_remove_custom_pricing(self):
        """Test removing custom pricing."""
        manager = PricingManager(enable_hot_reload=False)
        
        custom_pricing = PricingConfig(
            model="test-model",
            mode="chat",
            input_cost_per_token=Decimal("0.0001"),
        )
        
        manager.set_custom_pricing("test-model", custom_pricing)
        assert manager.get_pricing("test-model").input_cost_per_token == Decimal("0.0001")
        
        removed = manager.remove_custom_pricing("test-model")
        assert removed is True
        
        # Should return to default (zero for unknown model)
        assert manager.get_pricing("test-model").input_cost_per_token == Decimal("0")
        
        # Removing again should return False
        removed = manager.remove_custom_pricing("test-model")
        assert removed is False
    
    def test_list_models(self):
        """Test listing all models with pricing."""
        manager = PricingManager(enable_hot_reload=False)
        
        models = manager.list_models()
        
        # Should have many default models
        assert len(models) > 10
        assert "gpt-4o" in models
        assert "gpt-4o-mini" in models
    
    def test_list_models_filter_by_mode(self):
        """Test filtering models by mode."""
        manager = PricingManager(enable_hot_reload=False)
        
        embedding_models = manager.list_models(mode="embedding")
        
        # Should only have embedding models
        for name, config in embedding_models.items():
            assert config.mode == "embedding"
    
    def test_list_models_include_overrides(self):
        """Test listing models includes overrides."""
        manager = PricingManager(enable_hot_reload=False)
        
        custom_pricing = PricingConfig(
            model="custom-override-model",
            mode="chat",
            input_cost_per_token=Decimal("0.0001"),
        )
        manager.set_custom_pricing("custom-override-model", custom_pricing)
        
        models = manager.list_models(include_overrides=True)
        
        # Should include the override
        assert "custom-override-model" in models
    
    def test_reload(self):
        """Test manual reload of configuration."""
        yaml_content = """
version: "1.0"
pricing:
  reload-test-model:
    mode: chat
    input_cost_per_token: 0.000001
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name
        
        try:
            manager = PricingManager(config_path=temp_path, enable_hot_reload=False)
            
            # Initial load
            pricing = manager.get_pricing("reload-test-model")
            assert pricing.input_cost_per_token == Decimal("0.000001")
            
            # Modify file
            with open(temp_path, 'w') as f:
                f.write("""
version: "1.0"
pricing:
  reload-test-model:
    mode: chat
    input_cost_per_token: 0.000002
""")
            
            # Reload
            manager.reload()
            
            # Check updated value
            pricing = manager.get_pricing("reload-test-model")
            assert pricing.input_cost_per_token == Decimal("0.000002")
        finally:
            os.unlink(temp_path)
    
    def test_complex_yaml_with_all_modes(self):
        """Test loading complex YAML with all pricing modes."""
        yaml_content = """
version: "1.0"
pricing:
  chat-model:
    mode: chat
    input_cost_per_token: 0.000001
    output_cost_per_token: 0.000002
    cache_creation_input_token_cost: 0.00000125
    cache_read_input_token_cost: 0.0000001
  
  embedding-model:
    mode: embedding
    input_cost_per_token: 0.0000001
  
  image-model:
    mode: image_generation
    image_sizes:
      "1024x1024": 0.04
      "512x512": 0.02
    quality_pricing:
      standard: 1.0
      hd: 2.0
  
  tts-model:
    mode: audio_speech
    audio_cost_per_character: 0.000015
  
  stt-model:
    mode: audio_transcription
    audio_cost_per_minute: 0.006
  
  rerank-model:
    mode: rerank
    rerank_cost_per_search: 0.002
  
  moderation-model:
    mode: moderation
    input_cost_per_token: 0
  
  batch-model:
    mode: batch
    base_model: chat-model
    batch_discount_percent: 50
    input_cost_per_token: 0.0000005
    output_cost_per_token: 0.000001
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name
        
        try:
            manager = PricingManager(config_path=temp_path, enable_hot_reload=False)
            
            # Chat with cache
            chat = manager.get_pricing("chat-model")
            assert chat.mode == "chat"
            assert chat.cache_read_input_token_cost == Decimal("0.0000001")
            
            # Embedding
            embed = manager.get_pricing("embedding-model")
            assert embed.mode == "embedding"
            
            # Image
            image = manager.get_pricing("image-model")
            assert image.mode == "image_generation"
            assert image.image_sizes["1024x1024"] == Decimal("0.04")
            assert image.quality_pricing["hd"] == 2.0
            
            # TTS
            tts = manager.get_pricing("tts-model")
            assert tts.mode == "audio_speech"
            
            # STT
            stt = manager.get_pricing("stt-model")
            assert stt.mode == "audio_transcription"
            
            # Rerank
            rerank = manager.get_pricing("rerank-model")
            assert rerank.mode == "rerank"
            
            # Moderation
            mod = manager.get_pricing("moderation-model")
            assert mod.mode == "moderation"
            
            # Batch
            batch = manager.get_pricing("batch-model")
            assert batch.mode == "batch"
            assert batch.batch_discount_percent == 50
        finally:
            os.unlink(temp_path)


class TestPricingManagerHierarchy:
    """Tests for pricing hierarchy (team > org > global > yaml > defaults)."""
    
    def test_hierarchy_team_over_org(self):
        """Test that team pricing overrides org pricing."""
        manager = PricingManager(enable_hot_reload=False)
        org_id = uuid4()
        team_id = uuid4()
        
        org_pricing = PricingConfig(model="test", mode="chat", input_cost_per_token=Decimal("0.001"))
        team_pricing = PricingConfig(model="test", mode="chat", input_cost_per_token=Decimal("0.0005"))
        
        manager.set_custom_pricing("test", org_pricing, org_id=org_id)
        manager.set_custom_pricing("test", team_pricing, org_id=org_id, team_id=team_id)
        
        # Team should win
        pricing = manager.get_pricing("test", org_id=org_id, team_id=team_id)
        assert pricing.input_cost_per_token == Decimal("0.0005")
    
    def test_hierarchy_org_over_global(self):
        """Test that org pricing overrides global pricing."""
        manager = PricingManager(enable_hot_reload=False)
        org_id = uuid4()
        
        global_pricing = PricingConfig(model="test", mode="chat", input_cost_per_token=Decimal("0.001"))
        org_pricing = PricingConfig(model="test", mode="chat", input_cost_per_token=Decimal("0.0005"))
        
        manager.set_custom_pricing("test", global_pricing)
        manager.set_custom_pricing("test", org_pricing, org_id=org_id)
        
        # Org should win
        pricing = manager.get_pricing("test", org_id=org_id)
        assert pricing.input_cost_per_token == Decimal("0.0005")
        
        # Global should still exist for others
        pricing = manager.get_pricing("test")
        assert pricing.input_cost_per_token == Decimal("0.001")
