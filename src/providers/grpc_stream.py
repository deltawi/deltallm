from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class GrpcStreamHandle:
    lines: AsyncIterator[str]
    aclose: Callable[[], Awaitable[None]]
