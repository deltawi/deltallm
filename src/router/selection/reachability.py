from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

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
