from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, overload

if TYPE_CHECKING:
    from src.router.router import Deployment


_Default = TypeVar("_Default")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class _RegistrySnapshot:
    groups: Mapping[str, tuple["Deployment", ...]]
    physical: Mapping[str, "Deployment"]


class DeploymentRegistryStore(Mapping[str, tuple["Deployment", ...]]):
    """Shared routing-registry reference replaced as one immutable generation."""

    def __init__(
        self,
        registry: Mapping[str, Sequence["Deployment"]],
        *,
        physical_deployments: Mapping[str, "Deployment"] | None = None,
    ) -> None:
        self.replace(registry, physical_deployments=physical_deployments)

    def replace(
        self,
        registry: Mapping[str, Sequence["Deployment"]],
        *,
        physical_deployments: Mapping[str, "Deployment"] | None = None,
    ) -> None:
        if isinstance(registry, DeploymentRegistryStore) and physical_deployments is None:
            self._current = registry._current
            return
        if physical_deployments is None:
            # Compatibility for callers supplying only concrete model groups.
            # The canonical builder supplies physical inventory before group overlays.
            physical_deployments = {
                dep.deployment_id: dep
                for entries in registry.values()
                for dep in entries
                if dep.route_group_key is None
            }
        self._current = _RegistrySnapshot(
            groups=self._freeze(registry),
            physical=MappingProxyType(dict(physical_deployments)),
        )

    @property
    def physical_deployments(self) -> Mapping[str, "Deployment"]:
        return self._current.physical

    def __setitem__(self, key: str, deployments: Sequence["Deployment"]) -> None:
        """Atomically replace one group without exposing a mutable live mapping."""

        replacement = dict(self._current.groups)
        replacement[str(key)] = tuple(deployments)
        self.replace(replacement, physical_deployments=self.physical_deployments)

    @overload
    def pop(self, key: str) -> tuple["Deployment", ...]: ...

    @overload
    def pop(self, key: str, default: _Default) -> tuple["Deployment", ...] | _Default: ...

    def pop(
        self,
        key: str,
        default: object = _MISSING,
    ) -> tuple["Deployment", ...] | object:
        """Atomically remove one group for compatibility with control-plane callers."""

        replacement = dict(self._current.groups)
        if key not in replacement:
            if default is _MISSING:
                raise KeyError(key)
            return default
        removed = replacement.pop(key)
        self.replace(replacement, physical_deployments=self.physical_deployments)
        return removed

    def snapshot(self) -> Mapping[str, tuple["Deployment", ...]]:
        return self._current.groups

    def __getitem__(self, key: str) -> tuple["Deployment", ...]:
        return self._current.groups[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._current.groups)

    def __len__(self) -> int:
        return len(self._current.groups)

    @staticmethod
    def _freeze(
        registry: Mapping[str, Sequence["Deployment"]],
    ) -> Mapping[str, tuple["Deployment", ...]]:
        return MappingProxyType(
            {str(model_group): tuple(deployments) for model_group, deployments in registry.items()}
        )
