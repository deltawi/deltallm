from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, overload

if TYPE_CHECKING:
    from src.router.router import Deployment


_Default = TypeVar("_Default")
_MISSING = object()


class DeploymentRegistryStore(Mapping[str, tuple["Deployment", ...]]):
    """Shared routing-registry reference replaced as one immutable generation."""

    def __init__(self, registry: Mapping[str, Sequence["Deployment"]]) -> None:
        self._current = self._freeze(registry)

    def replace(self, registry: Mapping[str, Sequence["Deployment"]]) -> None:
        self._current = self._freeze(registry)

    def __setitem__(self, key: str, deployments: Sequence["Deployment"]) -> None:
        """Atomically replace one group without exposing a mutable live mapping."""

        replacement = dict(self._current)
        replacement[str(key)] = tuple(deployments)
        self.replace(replacement)

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

        replacement = dict(self._current)
        if key not in replacement:
            if default is _MISSING:
                raise KeyError(key)
            return default
        removed = replacement.pop(key)
        self.replace(replacement)
        return removed

    def snapshot(self) -> Mapping[str, tuple["Deployment", ...]]:
        return self._current

    def __getitem__(self, key: str) -> tuple["Deployment", ...]:
        return self._current[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._current)

    def __len__(self) -> int:
        return len(self._current)

    @staticmethod
    def _freeze(
        registry: Mapping[str, Sequence["Deployment"]],
    ) -> Mapping[str, tuple["Deployment", ...]]:
        return MappingProxyType(
            {str(model_group): tuple(deployments) for model_group, deployments in registry.items()}
        )
