"""Configuration management for the proxy server."""

import os
from typing import Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


@dataclass
class CacheConfig:
    """Cache configuration."""
    type: str = "memory"  # memory, redis, s3
    host: Optional[str] = None
    port: Optional[int] = None
    password: Optional[str] = None
    ttl: int = 3600
    max_size: Optional[int] = None


@dataclass
class RouterConfig:
    """Router configuration."""
    routing_strategy: str = "simple-shuffle"
    num_retries: int = 3
    timeout: float = 60.0
    enable_cooldowns: bool = True
    cooldown_time: float = 60.0
    cooldown_failure_threshold: int = 3
    fallbacks: list[dict[str, list[str]]] = field(default_factory=list)


@dataclass
class GeneralConfig:
    """General server configuration."""
    master_key: Optional[str] = None
    database_url: Optional[str] = None
    redis_url: Optional[str] = None
    log_level: str = "INFO"
    allowed_ips: list[str] = field(default_factory=list)
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
    max_requests_per_minute: Optional[int] = None
    max_tokens_per_minute: Optional[int] = None


@dataclass
class ProxyConfig:
    """Full proxy configuration."""
    model_list: list[dict[str, Any]] = field(default_factory=list)
    router: RouterConfig = field(default_factory=RouterConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    litellm_settings: dict[str, Any] = field(default_factory=dict)


def _resolve_env_vars(value: Any) -> Any:
    """Resolve environment variables in configuration values.
    
    Supports format: os.environ/VAR_NAME or ${VAR_NAME}
    
    Args:
        value: Configuration value
        
    Returns:
        Resolved value
    """
    if isinstance(value, str):
        if value.startswith("os.environ/"):
            env_var = value[11:]
            return os.environ.get(env_var)
        elif value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_config(config_path: Optional[str] = None) -> ProxyConfig:
    """Load configuration from file.
    
    Args:
        config_path: Path to configuration file. If None, uses default locations.
        
    Returns:
        Proxy configuration
    """
    if not YAML_AVAILABLE:
        raise ImportError("PyYAML is required for configuration loading")
    
    # Default config locations
    if config_path is None:
        search_paths = [
            "config.yaml",
            "config/config.yaml",
            "/etc/deltallm/config.yaml",
        ]
        for path in search_paths:
            if os.path.exists(path):
                config_path = path
                break
    
    # Default configuration
    config = ProxyConfig()
    
    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        
        if data:
            # Parse model list
            if "model_list" in data:
                config.model_list = _resolve_env_vars(data["model_list"])
            
            # Parse router settings
            if "router_settings" in data:
                router_data = data["router_settings"]
                config.router = RouterConfig(
                    routing_strategy=router_data.get("routing_strategy", "simple-shuffle"),
                    num_retries=router_data.get("num_retries", 3),
                    timeout=router_data.get("timeout", 60.0),
                    enable_cooldowns=router_data.get("enable_cooldowns", True),
                    cooldown_time=router_data.get("cooldown_time", 60.0),
                    cooldown_failure_threshold=router_data.get("cooldown_failure_threshold", 3),
                    fallbacks=router_data.get("fallbacks", []),
                )
            
            # Parse cache settings
            if "litellm_settings" in data:
                litellm = data["litellm_settings"]
                config.litellm_settings = litellm
                
                if litellm.get("cache"):
                    cache_params = litellm.get("cache_params", {})
                    config.cache = CacheConfig(
                        type=cache_params.get("type", "memory"),
                        host=cache_params.get("host"),
                        port=cache_params.get("port"),
                        password=cache_params.get("password"),
                        ttl=cache_params.get("ttl", 3600),
                        max_size=cache_params.get("max_size"),
                    )
            
            # Parse general settings
            if "general_settings" in data:
                general = data["general_settings"]
                config.general = GeneralConfig(
                    master_key=_resolve_env_vars(general.get("master_key")),
                    database_url=_resolve_env_vars(general.get("database_url")),
                    redis_url=_resolve_env_vars(general.get("redis_url")),
                    log_level=general.get("log_level", "INFO"),
                    allowed_ips=general.get("allowed_ips", []),
                    ssl_certfile=general.get("ssl_certfile"),
                    ssl_keyfile=general.get("ssl_keyfile"),
                    max_requests_per_minute=general.get("max_requests_per_minute"),
                    max_tokens_per_minute=general.get("max_tokens_per_minute"),
                )
    
    return config
