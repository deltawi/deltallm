"""Hash configured fallback dependencies without enumerating paths or executing routing."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from graphlib import TopologicalSorter
from hashlib import sha256
import json
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.router.failover import FallbackConfig


@dataclass(frozen=True, slots=True)
class FallbackEdges:
    ordinary: tuple[str, ...]
    context: tuple[str, ...]
    content: tuple[str, ...]

    def targets(self) -> tuple[str, ...]:
        return self.ordinary + self.context + self.content


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint_fallback_dependencies(
    local_identities: Mapping[str, str], config: FallbackConfig
) -> Mapping[str, str]:
    """Precompute one digest per group, with O(V+E) graph work and temporary state.

    Cycles are condensed before hashing, so long chains and diamonds neither recurse
    nor enumerate paths. A conservative dependency graph includes all three fallback
    kinds; runtime eligibility, retry limits and traversal remain failover-owned.
    """

    maps = (config.fallbacks, config.context_window_fallbacks, config.content_policy_fallbacks)
    nodes = set(local_identities).union(*(mapping.keys() for mapping in maps))
    nodes.update(target for mapping in maps for targets in mapping.values() for target in targets)
    edges = {
        node: FallbackEdges(*(tuple(dict.fromkeys(mapping.get(node, ()))) for mapping in maps))
        for node in nodes
    }
    # Keep target spelling: failover looks plans up by the configured key even
    # though the planner itself strips input. Normalizing here could hide a change.
    graph = {node: edge.targets() for node, edge in edges.items()}
    components, owner = _components(graph)
    dependencies = {
        index: {
            owner[target] for node in members for target in graph[node] if owner[target] != index
        }
        for index, members in enumerate(components)
    }
    resolved: dict[int, str] = {}
    for index in TopologicalSorter(dependencies).static_order():
        resolved[index] = _digest(
            [
                {
                    "group": node,
                    "local": local_identities.get(node),
                    "edges": [
                        [
                            (target, resolved[owner[target]] if owner[target] != index else None)
                            for target in targets
                        ]
                        for targets in (
                            edges[node].ordinary,
                            edges[node].context,
                            edges[node].content,
                        )
                    ],
                }
                for node in components[index]
            ]
        )
    return MappingProxyType(
        {
            node: "route-response-v1:" + _digest({"root": node, "component": resolved[owner[node]]})
            for node in local_identities
        }
    )


def _finish_order(graph: Mapping[str, tuple[str, ...]]) -> list[str]:
    visited: set[str] = set()
    finished: list[str] = []
    for node in graph:
        if node in visited:
            continue
        visited.add(node)
        stack: list[tuple[str, Iterator[str]]] = [(node, iter(graph[node]))]
        while stack:
            current, targets = stack[-1]
            target = next(targets, None)
            if target is None:
                finished.append(current)
                stack.pop()
            elif target not in visited:
                visited.add(target)
                stack.append((target, iter(graph[target])))
    return finished


def _components(
    graph: Mapping[str, tuple[str, ...]],
) -> tuple[list[tuple[str, ...]], dict[str, int]]:
    reverse: dict[str, list[str]] = {node: [] for node in graph}
    for node, targets in graph.items():
        for target in targets:
            reverse[target].append(node)
    owner: dict[str, int] = {}
    components: list[tuple[str, ...]] = []
    for node in reversed(_finish_order(graph)):
        if node in owner:
            continue
        index = len(components)
        owner[node] = index
        pending, members = [node], []
        while pending:
            current = pending.pop()
            members.append(current)
            for target in reverse[current]:
                if target not in owner:
                    owner[target] = index
                    pending.append(target)
        components.append(tuple(sorted(members)))
    return components, owner
