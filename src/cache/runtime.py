from __future__ import annotations

from typing import Any

from src.config import AppConfig

from .backends import InMemoryBackend, RedisBackend, S3Backend
from .key_builder import CacheKeyBuilder
from .metrics import NoopCacheMetrics, PrometheusCacheMetrics
from .streaming import StreamingCacheHandler


def configure_cache_runtime(
    app: Any,
    *,
    app_config: AppConfig,
    redis_client: Any,
    salt_key: str,
) -> None:
    cache_settings = app_config.general_settings

    app.state.cache_backend = None
    app.state.cache_key_builder = None
    app.state.cache_metrics = NoopCacheMetrics()
    app.state.streaming_cache_handler = None

    if not cache_settings.cache_enabled:
        return

    if cache_settings.cache_backend == "memory":
        cache_backend = InMemoryBackend(max_size=cache_settings.cache_max_size)
    elif cache_settings.cache_backend == "redis":
        cache_backend = RedisBackend(redis_client)
    elif cache_settings.cache_backend == "s3":
        cache_backend = S3Backend()
    else:
        raise ValueError(f"Unsupported cache backend: {cache_settings.cache_backend}")

    app.state.cache_backend = cache_backend
    app.state.cache_key_builder = CacheKeyBuilder(custom_salt=salt_key)
    app.state.streaming_cache_handler = StreamingCacheHandler(cache_backend)
    try:
        app.state.cache_metrics = PrometheusCacheMetrics(cache_type=cache_settings.cache_backend)
    except Exception:
        app.state.cache_metrics = NoopCacheMetrics()
