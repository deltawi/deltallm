from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class BatchCreatePromotionResult:
    session_id: str
    batch_id: str
    promoted: bool


class BatchCreatePromoter(Protocol):
    async def promote_session(self, session_id: str) -> BatchCreatePromotionResult:
        ...
