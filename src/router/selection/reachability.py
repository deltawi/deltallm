from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from src.models.errors import InvalidRequestError

if TYPE_CHECKING:
    from src.router.failover import FallbackConfig


def selector_reachable_groups(selectors: Collection[str], config: FallbackConfig) -> frozenset[str]:
    """Reverse reachability in O(V+E), compiled once; cycles never enumerate paths."""
    parents: dict[str, set[str]] = {}
    for mapping in (
        config.fallbacks,
        config.context_window_fallbacks,
        config.content_policy_fallbacks,
    ):
        for source, targets in mapping.items():
            for target in targets:
                parents.setdefault(target, set()).add(source)
    seen = set(selectors)
    pending = list(selectors)
    while pending:
        for parent in parents.get(pending.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                pending.append(parent)
    return frozenset(seen)


def require_batch_selector_support(group: str, reachable_groups: frozenset[str]) -> None:
    if group in reachable_groups:
        raise InvalidRequestError(
            message="Batch requests do not yet support model-router selectors or selector fallback targets",
            code="batch_model_router_selector_unsupported",
        )
