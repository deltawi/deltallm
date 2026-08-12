from __future__ import annotations


class CacheInvalidationBackendUnavailable(RuntimeError):
    """Raised when auth cache invalidation cannot safely run."""


__all__ = ["CacheInvalidationBackendUnavailable"]
