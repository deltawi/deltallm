"""Finite serialized collection and retained payload charges for one queue."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class DurableBatchBytes(Generic[T]):
    def __init__(
        self,
        payload_size: Callable[[T], int] | None,
        *,
        max_batch_bytes: int | None,
        max_retained_bytes: int | None,
    ) -> None:
        enabled = payload_size is not None
        if enabled != (max_batch_bytes is not None and max_retained_bytes is not None):
            raise ValueError("byte collection requires a size owner and both byte limits")
        if not enabled and (max_batch_bytes is not None or max_retained_bytes is not None):
            raise ValueError("byte limits require a size owner")
        if max_batch_bytes is not None and not 3 <= max_batch_bytes <= 1_048_576:
            raise ValueError("batch byte capacity must be between 3 and 1048576")
        if max_retained_bytes is not None and not 1 <= max_retained_bytes <= 64 * 1024 * 1024:
            raise ValueError("retained byte capacity must be between 1 and 67108864")
        self._size = payload_size
        self.maximum_batch = max_batch_bytes
        self._maximum_retained = max_retained_bytes
        self.retained = 0

    def measure(self, value: T) -> int:
        size = 0 if self._size is None else self._size(value)
        if type(size) is not int or size < 0:
            raise ValueError("payload size must be a non-negative integer")
        return size

    def fits(self, size: int) -> bool:
        return self.maximum_batch is None or (
            size + 2 <= self.maximum_batch
            and self.retained + self.charge(size) <= self._maximum_retained
        )

    def charge(self, size: int) -> int:
        # Fixed space covers the future, wrapper, queue entry, and bounded
        # collection references. Payloads remain immutable bytes in accounting.
        return 0 if self._size is None else 4096 + size

    def add(self, size: int) -> None:
        self.retained += self.charge(size)

    def release(self, size: int) -> None:
        self.retained -= self.charge(size)

    def batch_fits(self, collected_bytes: int, next_size: int, *, first: bool) -> bool:
        return self.maximum_batch is None or (
            collected_bytes + next_size + (0 if first else 1) <= self.maximum_batch
        )
