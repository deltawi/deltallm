"""Utility functions for ProxyLLM."""

from .pricing import get_pricing_info, calculate_cost, get_model_info
from .encryption import (
    EncryptionManager,
    EncryptionError,
    encrypt_api_key,
    decrypt_api_key,
    generate_encryption_key,
    get_encryption_manager,
)
from .model_type_detector import (
    detect_model_type,
    suggest_model_type,
    get_all_model_types,
    MODEL_TYPE_PATTERNS,
)

# migrate_config is imported lazily to avoid circular imports
# Use: from deltallm.utils.migrate_config import migrate_config

__all__ = [
    "get_pricing_info",
    "calculate_cost",
    "get_model_info",
    "EncryptionManager",
    "EncryptionError",
    "encrypt_api_key",
    "decrypt_api_key",
    "generate_encryption_key",
    "get_encryption_manager",
    "detect_model_type",
    "suggest_model_type",
    "get_all_model_types",
    "MODEL_TYPE_PATTERNS",
]
