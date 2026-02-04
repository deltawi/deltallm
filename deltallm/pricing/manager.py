"""Pricing manager for ProxyLLM.

Manages pricing configuration from YAML with optional DB overrides.
Supports hot reload of configuration files and database persistence.
"""

import os
import yaml
import logging
from typing import Optional, Dict, TYPE_CHECKING
from decimal import Decimal
from uuid import UUID

from .models import PricingConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Optional imports for hot reload
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

logger = logging.getLogger(__name__)


class PricingManager:
    """Manages pricing configuration from YAML with optional DB overrides.
    
    Hierarchy (highest priority first):
    1. Team-specific pricing (DB)
    2. Org-specific pricing (DB)
    3. Custom global pricing (DB)
    4. YAML file pricing
    5. Built-in defaults
    
    Usage:
        manager = PricingManager("config/pricing.yaml")
        pricing = manager.get_pricing("gpt-4o")
        
        # With org/team override
        pricing = manager.get_pricing("gpt-4o", org_id=org_uuid, team_id=team_uuid)
    """
    
    def __init__(
        self, 
        config_path: str = "config/pricing.yaml",
        enable_hot_reload: bool = True
    ):
        """Initialize the pricing manager.
        
        Args:
            config_path: Path to YAML pricing configuration file
            enable_hot_reload: Whether to watch for file changes and reload
        """
        self.config_path = os.path.abspath(config_path)
        self._pricing: Dict[str, PricingConfig] = {}
        self._db_overrides: Dict[str, PricingConfig] = {}
        self._observer: Optional[Observer] = None
        
        # Load defaults and config
        self._load_defaults()
        self._load_yaml_config()
        
        # Setup hot reload if enabled
        if enable_hot_reload and WATCHDOG_AVAILABLE:
            self._setup_hot_reload()
        elif enable_hot_reload and not WATCHDOG_AVAILABLE:
            logger.warning("Hot reload requested but watchdog not installed. "
                          "Install with: pip install watchdog")
    
    def _load_defaults(self):
        """Load built-in defaults for common models."""
        defaults = {
            # OpenAI GPT-4o models
            "gpt-4o": PricingConfig(
                model="gpt-4o",
                mode="chat",
                input_cost_per_token=Decimal("0.0000025"),  # $2.50 / 1M tokens
                output_cost_per_token=Decimal("0.00001"),   # $10.00 / 1M tokens
                max_tokens=128000,
                max_output_tokens=16384,
            ),
            "gpt-4o-mini": PricingConfig(
                model="gpt-4o-mini",
                mode="chat",
                input_cost_per_token=Decimal("0.00000015"),  # $0.15 / 1M tokens
                output_cost_per_token=Decimal("0.0000006"),  # $0.60 / 1M tokens
                max_tokens=128000,
                max_output_tokens=16384,
            ),
            "gpt-4-turbo": PricingConfig(
                model="gpt-4-turbo",
                mode="chat",
                input_cost_per_token=Decimal("0.00001"),   # $10 / 1M tokens
                output_cost_per_token=Decimal("0.00003"),  # $30 / 1M tokens
                max_tokens=128000,
                max_output_tokens=4096,
            ),
            "gpt-4": PricingConfig(
                model="gpt-4",
                mode="chat",
                input_cost_per_token=Decimal("0.00003"),   # $30 / 1M tokens
                output_cost_per_token=Decimal("0.00006"),  # $60 / 1M tokens
                max_tokens=8192,
                max_output_tokens=8192,
            ),
            "gpt-3.5-turbo": PricingConfig(
                model="gpt-3.5-turbo",
                mode="chat",
                input_cost_per_token=Decimal("0.0000005"),   # $0.50 / 1M tokens
                output_cost_per_token=Decimal("0.0000015"),  # $1.50 / 1M tokens
                max_tokens=16385,
                max_output_tokens=4096,
            ),
            # OpenAI o1 models
            "o1": PricingConfig(
                model="o1",
                mode="chat",
                input_cost_per_token=Decimal("0.000015"),  # $15 / 1M tokens
                output_cost_per_token=Decimal("0.00006"),  # $60 / 1M tokens
                max_tokens=200000,
                max_output_tokens=100000,
            ),
            "o1-mini": PricingConfig(
                model="o1-mini",
                mode="chat",
                input_cost_per_token=Decimal("0.000003"),   # $3 / 1M tokens
                output_cost_per_token=Decimal("0.000012"),  # $12 / 1M tokens
                max_tokens=128000,
                max_output_tokens=65536,
            ),
            # Anthropic Claude models
            "claude-3-5-sonnet-20241022": PricingConfig(
                model="claude-3-5-sonnet-20241022",
                mode="chat",
                input_cost_per_token=Decimal("0.000003"),     # $3 / 1M tokens
                output_cost_per_token=Decimal("0.000015"),    # $15 / 1M tokens
                cache_creation_input_token_cost=Decimal("0.00000375"),  # $3.75 / 1M
                cache_read_input_token_cost=Decimal("0.0000003"),       # $0.30 / 1M
                max_tokens=200000,
                max_output_tokens=8192,
            ),
            "claude-3-opus-20240229": PricingConfig(
                model="claude-3-opus-20240229",
                mode="chat",
                input_cost_per_token=Decimal("0.000015"),     # $15 / 1M tokens
                output_cost_per_token=Decimal("0.000075"),    # $75 / 1M tokens
                cache_creation_input_token_cost=Decimal("0.00001875"),
                cache_read_input_token_cost=Decimal("0.0000015"),
                max_tokens=200000,
                max_output_tokens=4096,
            ),
            "claude-3-haiku-20240307": PricingConfig(
                model="claude-3-haiku-20240307",
                mode="chat",
                input_cost_per_token=Decimal("0.00000025"),   # $0.25 / 1M tokens
                output_cost_per_token=Decimal("0.00000125"),  # $1.25 / 1M tokens
                max_tokens=200000,
                max_output_tokens=4096,
            ),
            # Embedding models
            "text-embedding-3-small": PricingConfig(
                model="text-embedding-3-small",
                mode="embedding",
                input_cost_per_token=Decimal("0.00000002"),  # $0.02 / 1M tokens
                max_tokens=8191,
            ),
            "text-embedding-3-large": PricingConfig(
                model="text-embedding-3-large",
                mode="embedding",
                input_cost_per_token=Decimal("0.00000013"),  # $0.13 / 1M tokens
                max_tokens=8191,
            ),
            "text-embedding-ada-002": PricingConfig(
                model="text-embedding-ada-002",
                mode="embedding",
                input_cost_per_token=Decimal("0.0000001"),  # $0.10 / 1M tokens
                max_tokens=8191,
            ),
            # Image generation models
            "dall-e-3": PricingConfig(
                model="dall-e-3",
                mode="image_generation",
                image_sizes={
                    "1024x1024": Decimal("0.040"),
                    "1024x1792": Decimal("0.080"),
                    "1792x1024": Decimal("0.080"),
                },
                quality_pricing={
                    "standard": 1.0,
                    "hd": 2.0,
                },
            ),
            "dall-e-2": PricingConfig(
                model="dall-e-2",
                mode="image_generation",
                image_sizes={
                    "1024x1024": Decimal("0.020"),
                    "512x512": Decimal("0.018"),
                    "256x256": Decimal("0.016"),
                },
            ),
            # Audio TTS models
            "tts-1": PricingConfig(
                model="tts-1",
                mode="audio_speech",
                audio_cost_per_character=Decimal("0.000015"),  # $0.015 / 1K chars
            ),
            "tts-1-hd": PricingConfig(
                model="tts-1-hd",
                mode="audio_speech",
                audio_cost_per_character=Decimal("0.000030"),  # $0.030 / 1K chars
            ),
            # Audio STT models
            "whisper-1": PricingConfig(
                model="whisper-1",
                mode="audio_transcription",
                audio_cost_per_minute=Decimal("0.006"),  # $0.006 / minute
            ),
            # Moderation models
            "text-moderation-latest": PricingConfig(
                model="text-moderation-latest",
                mode="moderation",
                input_cost_per_token=Decimal("0"),  # Free
            ),
            "text-moderation-stable": PricingConfig(
                model="text-moderation-stable",
                mode="moderation",
                input_cost_per_token=Decimal("0"),  # Free
            ),
            # Rerank models
            "cohere-rerank-v3-english": PricingConfig(
                model="cohere-rerank-v3-english",
                mode="rerank",
                rerank_cost_per_search=Decimal("0.002"),  # $0.002 / search
            ),
            "cohere-rerank-v3-multilingual": PricingConfig(
                model="cohere-rerank-v3-multilingual",
                mode="rerank",
                rerank_cost_per_search=Decimal("0.002"),
            ),
        }
        self._pricing.update(defaults)
        logger.info(f"Loaded {len(defaults)} default pricing configurations")
    
    def _load_yaml_config(self):
        """Load pricing from YAML file."""
        if not os.path.exists(self.config_path):
            logger.info(f"Pricing config file not found at {self.config_path}, using defaults")
            return
        
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            if not config or "pricing" not in config:
                logger.warning(f"No 'pricing' section found in {self.config_path}")
                return
            
            count = 0
            for model, data in config.get("pricing", {}).items():
                try:
                    self._pricing[model] = PricingConfig.from_dict(model, data)
                    count += 1
                except Exception as e:
                    logger.error(f"Error parsing pricing for {model}: {e}")
            
            logger.info(f"Loaded {count} pricing configurations from {self.config_path}")
            
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML config: {e}")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    
    def _setup_hot_reload(self):
        """Setup file watcher for hot reload."""
        if not WATCHDOG_AVAILABLE:
            return
        
        class PricingReloadHandler(FileSystemEventHandler):
            """Handler for file system events."""
            
            def __init__(self, manager: "PricingManager"):
                self.manager = manager
                self._debounce_timer = None
            
            def on_modified(self, event):
                if event.src_path == self.manager.config_path:
                    logger.info(f"Pricing config modified, reloading...")
                    self.manager._load_yaml_config()
        
        try:
            observer = Observer()
            observer.schedule(
                PricingReloadHandler(self),
                path=os.path.dirname(self.config_path),
                recursive=False
            )
            observer.start()
            self._observer = observer
            logger.info(f"Hot reload enabled for {self.config_path}")
        except Exception as e:
            logger.error(f"Error setting up hot reload: {e}")
    
    def stop(self):
        """Stop the file observer if running."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Hot reload stopped")
    
    def get_pricing(
        self, 
        model: str,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None
    ) -> PricingConfig:
        """Get pricing for a model, considering hierarchy.
        
        Args:
            model: Model name
            org_id: Organization for override lookup
            team_id: Team for override lookup
            
        Returns:
            PricingConfig (returns default with $0 if not found)
        """
        # Check hierarchy: team -> org -> global -> yaml -> defaults
        
        if team_id and org_id:
            key = f"team:{team_id}:{model}"
            if key in self._db_overrides:
                return self._db_overrides[key]
        
        if org_id:
            key = f"org:{org_id}:{model}"
            if key in self._db_overrides:
                config = self._db_overrides[key]
                logger.debug(f"Returning org override for {key} - input_cost: {config.input_cost_per_token!r}")
                return config
        
        # Global DB override
        if model in self._db_overrides:
            config = self._db_overrides[model]
            logger.debug(f"Returning DB override for {model} - input_cost: {config.input_cost_per_token!r}")
            return config
        
        # YAML / defaults
        if model in self._pricing:
            return self._pricing[model]
        
        # Try prefix matching for variants
        for model_key in sorted(self._pricing.keys(), key=len, reverse=True):
            if model.startswith(model_key) or model_key in model:
                return self._pricing[model_key]
        
        # Return default pricing ($0) for unknown models
        logger.warning(f"No pricing found for model: {model}, returning default")
        return PricingConfig(model=model, mode="chat")
    
    def set_custom_pricing(
        self,
        model: str,
        pricing: PricingConfig,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None
    ):
        """Set custom pricing (stored in memory, should be persisted to DB).
        
        Args:
            model: Model name
            pricing: Pricing configuration
            org_id: Organization ID for org-specific pricing
            team_id: Team ID for team-specific pricing (requires org_id)
        """
        key = self._make_key(model, org_id, team_id)
        self._db_overrides[key] = pricing
        logger.info(f"Set custom pricing for {key}")
    
    def remove_custom_pricing(
        self,
        model: str,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None
    ) -> bool:
        """Remove custom pricing.
        
        Args:
            model: Model name
            org_id: Organization ID
            team_id: Team ID
            
        Returns:
            True if removed, False if not found
        """
        key = self._make_key(model, org_id, team_id)
        if key in self._db_overrides:
            del self._db_overrides[key]
            logger.info(f"Removed custom pricing for {key}")
            return True
        return False
    
    def list_models(
        self,
        mode: Optional[str] = None,
        include_overrides: bool = True
    ) -> Dict[str, PricingConfig]:
        """List all models with pricing.
        
        Args:
            mode: Filter by mode (chat, embedding, etc.)
            include_overrides: Include DB overrides
            
        Returns:
            Dictionary of model name -> PricingConfig
        """
        result = dict(self._pricing)
        
        if include_overrides:
            # Add overrides, filtering out the key prefix
            for key, pricing in self._db_overrides.items():
                if key.startswith("team:") or key.startswith("org:"):
                    # Extract model name from key
                    parts = key.split(":", 2)
                    if len(parts) == 3:
                        model = parts[2]
                        result[f"{key}"] = pricing
                else:
                    result[key] = pricing
        
        if mode:
            result = {
                k: v for k, v in result.items() 
                if v.mode == mode
            }
        
        return result
    
    def _make_key(
        self, 
        model: str, 
        org_id: Optional[UUID] = None, 
        team_id: Optional[UUID] = None
    ) -> str:
        """Create a lookup key for pricing overrides.
        
        Args:
            model: Model name
            org_id: Organization ID
            team_id: Team ID
            
        Returns:
            Lookup key string
        """
        if team_id and org_id:
            return f"team:{team_id}:{model}"
        elif org_id:
            return f"org:{org_id}:{model}"
        else:
            return model
    
    def reload(self):
        """Force reload of pricing configuration."""
        self._load_yaml_config()
        logger.info("Pricing configuration reloaded")

    # ========== Database Persistence Methods ==========

    async def load_from_database(self, db: "AsyncSession") -> int:
        """Load all custom pricing from database into memory.

        Args:
            db: Database session

        Returns:
            Number of pricing configurations loaded
        """
        from sqlalchemy import select
        from deltallm.db.models import ModelPricing

        try:
            result = await db.execute(select(ModelPricing))
            rows = result.scalars().all()

            count = 0
            for row in rows:
                config = self._db_row_to_config(row)
                key = self._make_key(row.model_name, row.org_id, row.team_id)
                self._db_overrides[key] = config
                count += 1
                logger.debug(f"Loaded pricing from DB for {key} - input_cost: {config.input_cost_per_token!r}")

            logger.info(f"Loaded {count} custom pricing configurations from database")
            return count
        except Exception as e:
            logger.error(f"Error loading pricing from database: {e}")
            return 0

    async def save_to_database(
        self,
        db: "AsyncSession",
        model: str,
        pricing: PricingConfig,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
    ) -> None:
        """Save custom pricing to database (upsert).

        Args:
            db: Database session
            model: Model name
            pricing: Pricing configuration to save
            org_id: Organization ID for org-specific pricing
            team_id: Team ID for team-specific pricing
        """
        from sqlalchemy import select
        from deltallm.db.models import ModelPricing

        # Check if exists
        stmt = select(ModelPricing).where(
            ModelPricing.model_name == model,
            ModelPricing.org_id == org_id,
            ModelPricing.team_id == team_id,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing record
            logger.info(f"Updating existing pricing record for {model} - input_cost: {pricing.input_cost_per_token!r}, output_cost: {pricing.output_cost_per_token!r}")
            existing.mode = pricing.mode
            existing.input_cost_per_token = pricing.input_cost_per_token
            existing.output_cost_per_token = pricing.output_cost_per_token
            existing.cache_creation_input_token_cost = pricing.cache_creation_input_token_cost
            existing.cache_read_input_token_cost = pricing.cache_read_input_token_cost
            existing.image_cost_per_image = pricing.image_cost_per_image
            existing.image_sizes = {k: str(v) for k, v in pricing.image_sizes.items()}
            existing.quality_pricing = pricing.quality_pricing
            existing.audio_cost_per_character = pricing.audio_cost_per_character
            existing.audio_cost_per_minute = pricing.audio_cost_per_minute
            existing.rerank_cost_per_search = pricing.rerank_cost_per_search
            existing.batch_discount_percent = pricing.batch_discount_percent
            existing.base_model = pricing.base_model
            existing.max_tokens = pricing.max_tokens
            existing.max_input_tokens = pricing.max_input_tokens
            existing.max_output_tokens = pricing.max_output_tokens
            logger.info(f"Updated pricing for {model} in database")
        else:
            # Insert new record
            logger.info(f"Inserting new pricing record for {model} - input_cost: {pricing.input_cost_per_token!r}, output_cost: {pricing.output_cost_per_token!r}")
            row = ModelPricing(
                model_name=model,
                org_id=org_id,
                team_id=team_id,
                mode=pricing.mode,
                input_cost_per_token=pricing.input_cost_per_token,
                output_cost_per_token=pricing.output_cost_per_token,
                cache_creation_input_token_cost=pricing.cache_creation_input_token_cost,
                cache_read_input_token_cost=pricing.cache_read_input_token_cost,
                image_cost_per_image=pricing.image_cost_per_image,
                image_sizes={k: str(v) for k, v in pricing.image_sizes.items()},
                quality_pricing=pricing.quality_pricing,
                audio_cost_per_character=pricing.audio_cost_per_character,
                audio_cost_per_minute=pricing.audio_cost_per_minute,
                rerank_cost_per_search=pricing.rerank_cost_per_search,
                batch_discount_percent=pricing.batch_discount_percent,
                base_model=pricing.base_model,
                max_tokens=pricing.max_tokens,
                max_input_tokens=pricing.max_input_tokens,
                max_output_tokens=pricing.max_output_tokens,
            )
            db.add(row)
            logger.info(f"Inserted pricing for {model} in database")

        await db.commit()

        # Update in-memory cache
        key = self._make_key(model, org_id, team_id)
        self._db_overrides[key] = pricing

    async def delete_from_database(
        self,
        db: "AsyncSession",
        model: str,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
    ) -> bool:
        """Delete custom pricing from database.

        Args:
            db: Database session
            model: Model name
            org_id: Organization ID
            team_id: Team ID

        Returns:
            True if deleted, False if not found
        """
        from sqlalchemy import select, delete
        from deltallm.db.models import ModelPricing

        stmt = delete(ModelPricing).where(
            ModelPricing.model_name == model,
            ModelPricing.org_id == org_id,
            ModelPricing.team_id == team_id,
        )
        result = await db.execute(stmt)
        await db.commit()

        deleted = result.rowcount > 0

        if deleted:
            # Remove from in-memory cache
            key = self._make_key(model, org_id, team_id)
            self._db_overrides.pop(key, None)
            logger.info(f"Deleted pricing for {model} from database")

        return deleted

    def _db_row_to_config(self, row) -> PricingConfig:
        """Convert a ModelPricing database row to PricingConfig.

        Args:
            row: ModelPricing database row

        Returns:
            PricingConfig instance
        """
        # Parse image_sizes from JSONB (stored as string values)
        image_sizes = {}
        if row.image_sizes:
            image_sizes = {k: Decimal(str(v)) for k, v in row.image_sizes.items()}

        return PricingConfig(
            model=row.model_name,
            mode=row.mode,
            input_cost_per_token=row.input_cost_per_token or Decimal("0"),
            output_cost_per_token=row.output_cost_per_token or Decimal("0"),
            cache_creation_input_token_cost=row.cache_creation_input_token_cost,
            cache_read_input_token_cost=row.cache_read_input_token_cost,
            image_cost_per_image=row.image_cost_per_image,
            image_sizes=image_sizes,
            quality_pricing=row.quality_pricing or {},
            audio_cost_per_character=row.audio_cost_per_character,
            audio_cost_per_minute=row.audio_cost_per_minute,
            rerank_cost_per_search=row.rerank_cost_per_search,
            batch_discount_percent=float(row.batch_discount_percent) if row.batch_discount_percent else 50.0,
            base_model=row.base_model,
            max_tokens=row.max_tokens,
            max_input_tokens=row.max_input_tokens,
            max_output_tokens=row.max_output_tokens,
        )
