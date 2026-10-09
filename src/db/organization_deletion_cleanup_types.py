from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CleanupPageResult:
    processed: int
    remaining: bool


__all__ = ["CleanupPageResult"]
