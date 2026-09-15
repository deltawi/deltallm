from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Generic, TypeVar

from src.request_deadline import RequestDeadline as RequestDeadline

if TYPE_CHECKING:
    from src.router.router import Deployment


T = TypeVar("T")
_FAILOVER_ATTEMPT_CONTEXT_ATTR = "_deltallm_failover_attempt_context"
_FAILOVER_ORIGINAL_ERROR_ATTR = "_deltallm_failover_original_error"


@dataclass(frozen=True, slots=True)
class FailoverAttemptContext:
    """Structured routing context retained on a terminal execution error."""

    model_group: str
    attempted_deployment_ids: tuple[str, ...]

    @property
    def last_attempted_deployment_id(self) -> str | None:
        if not self.attempted_deployment_ids:
            return None
        return self.attempted_deployment_ids[-1]


def attach_failover_attempt_context(
    exc: Exception,
    *,
    model_group: str,
    attempted_deployment_ids: list[str],
) -> Exception:
    existing = get_failover_attempt_context(exc)
    if (
        existing is not None
        and existing.model_group == model_group
        and len(existing.attempted_deployment_ids) >= len(attempted_deployment_ids)
        and existing.attempted_deployment_ids[: len(attempted_deployment_ids)]
        == tuple(attempted_deployment_ids)
    ):
        return exc
    context = FailoverAttemptContext(
        model_group=model_group,
        attempted_deployment_ids=tuple(attempted_deployment_ids),
    )
    setattr(exc, _FAILOVER_ATTEMPT_CONTEXT_ATTR, context)
    return exc


def get_failover_attempt_context(exc: Exception) -> FailoverAttemptContext | None:
    context = getattr(exc, _FAILOVER_ATTEMPT_CONTEXT_ATTR, None)
    return context if isinstance(context, FailoverAttemptContext) else None


def attach_failover_original_error(
    normalized: Exception,
    original: Exception,
) -> Exception:
    setattr(normalized, _FAILOVER_ORIGINAL_ERROR_ATTR, original)
    return normalized


def get_failover_original_error(exc: Exception) -> Exception | None:
    original = getattr(exc, _FAILOVER_ORIGINAL_ERROR_ATTR, None)
    return original if isinstance(original, Exception) and original is not exc else None


@dataclass(frozen=True, slots=True)
class ProviderAttemptResult(Generic[T]):
    """A usable provider result that also carries one aggregate health failure."""

    value: T
    health_error: Exception


@dataclass(slots=True)
class ManagedFailoverResult(Generic[T]):
    """A successful attempt whose capacity permit is owned by the caller."""

    value: T
    deployment: Deployment
    deadline: RequestDeadline
    _release: Callable[[], Awaitable[None]] = field(repr=False)
    recovery_token: str | None = None
    _released: bool = field(default=False, init=False, repr=False)
    _release_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def release(self) -> None:
        async with self._release_lock:
            if self._released:
                return
            await self._release()
            self._released = True
