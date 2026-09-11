from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from src.router.execution import RequestDeadline

if TYPE_CHECKING:
    from src.router.router import Deployment

_CAPACITY_KEY = "_deltallm_attempt_capacity"


class AttemptSlotAdmission(ABC):
    """Optional execution-owner admission, before provider permits and timing."""

    @abstractmethod
    def slot(
        self, deployment: Deployment, deadline: RequestDeadline
    ) -> AbstractAsyncContextManager[None]: ...


def bind_attempt_capacity(context: dict[str, object], capacity: AttemptSlotAdmission) -> None:
    context[_CAPACITY_KEY] = capacity


def attempt_capacity(context: dict[str, object]) -> AttemptSlotAdmission | None:
    capacity = context.get(_CAPACITY_KEY)
    if capacity is not None and not isinstance(capacity, AttemptSlotAdmission):
        raise TypeError("Invalid attempt capacity binding")
    return capacity
