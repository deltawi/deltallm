"""Deployments one failover run may try, so a provider call can size its one reservation."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from src.router.router import Deployment

_CANDIDATES: ContextVar[tuple[Deployment, ...]] = ContextVar(
    "deltallm_failover_candidates", default=()
)


@contextmanager
def failover_scope() -> Iterator[None]:
    """Start one failover run with no known candidates; restore the outer run on exit."""
    token = _CANDIDATES.set(())
    try:
        yield
    finally:
        _CANDIDATES.reset(token)


def set_failover_candidates(deployments: Iterable[object]) -> None:
    """Record the concrete deployments of the planned chain for the current run."""
    _CANDIDATES.set(tuple(item for item in deployments if isinstance(item, Deployment)))


def failover_candidates() -> tuple[Deployment, ...]:
    return _CANDIDATES.get()
