"""Typed billing boundary shared by legacy and native Realtime owners."""

from datetime import datetime
from typing import Protocol
from datetime import timedelta

from src.billing.realtime_charge import RealtimeChargeContext
from src.billing.realtime_usage import RealtimeUsageReceipt


class RealtimeBilling(Protocol):
    @property
    def requires_cost_bounds(self) -> bool: ...

    @property
    def terminal_lifetime(self) -> timedelta | None: ...

    async def check_owner(self, context: RealtimeChargeContext) -> None: ...

    async def dispatch(
        self, operation_id: str, context: RealtimeChargeContext, *, expires_at: datetime
    ) -> None: ...

    async def accept(
        self, operation_id: str, context: RealtimeChargeContext, receipt: RealtimeUsageReceipt
    ) -> None: ...

    async def close(self, session_id: str) -> None: ...
