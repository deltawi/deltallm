"""Waits between result saves that a busy database refused.

The provider has already answered when the result is saved. With accounting v2
an answer that is not saved ends the item: its next claim cannot call the
provider again, so it fails as accounting_closed. A busy pool (all jobs starting
at once, a rollout) usually frees up within seconds, so the save waits it out.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import random
from time import monotonic

from src.batch.retry import jittered_busy_wait

# The first waits are the same as the lease renewal and requeue waits: each a
# random value from half of the delay to all of it.
SHORT_DELAYS_SECONDS = (0.5, 1.0, 2.0)
# The longer waits, with up to 20% jitter, bring the total to about 40 s (at
# most 46.7 s).
LONG_DELAYS_SECONDS = (4.0, 8.0, 8.0, 8.0, 8.0)
JITTER = 0.2
# A claim returns its lease end in database time. Allow this much difference
# between the database clock and the worker clock.
CLOCK_SKEW_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class BusyWait:
    seconds: float
    # A long wait needs a lease known to cover it, and a shutdown drain stops
    # it.
    long: bool


class ResultSaveWaits:
    """The waits of one result save, and how long the item lease is known to last.

    The short waits need no lease check, as before. The heartbeat stops before
    the save and, with write grouping, no renewal comes before it, so the lease
    can be old. A long wait is therefore taken only while the known lease
    covers the wait plus a reserve: a quarter of the lease, and at least a
    renewal call and a save call at their longest. The lease is known from the
    claim's lease end, or from a renewal that this save made. Heartbeat
    renewals only make the real lease longer. A renewal is sent only while
    the known lease covers it and a save call after it. A save that comes
    after a long wait or a renewal skips the save queue: the lease left covers
    one save call, but not a wait for a save slot, which has no bound. The
    save checks the owner and claim epoch, so a save after another worker's
    claim changes nothing.
    """

    def __init__(
        self,
        lease_seconds: float,
        *,
        call_seconds: float = 0.0,
        known_lease_seconds: float | None = None,
    ) -> None:
        self._lease_seconds = max(0.0, float(lease_seconds))
        # A renewal call and then a save call, each at its longest.
        self._two_calls_seconds = 2 * max(0.0, float(call_seconds))
        self._reserve_seconds = max(self._lease_seconds / 4, self._two_calls_seconds)
        self._lease_until = (
            None if known_lease_seconds is None else monotonic() + known_lease_seconds
        )
        # A long wait or a renewal came before the next save.
        self._delayed = False
        self._waits: Iterator[BusyWait] = iter(
            [
                BusyWait(seconds=jittered_busy_wait(delay), long=False)
                for delay in SHORT_DELAYS_SECONDS
            ]
            + [_jittered(delay) for delay in LONG_DELAYS_SECONDS]
        )

    def next_wait(self) -> BusyWait | None:
        return next(self._waits, None)

    @staticmethod
    def now() -> float:
        return monotonic()

    def lease_renewed(self, started_at: float) -> None:
        """Record a renewal sent at ``started_at``; the new lease runs from then."""
        renewed_until = started_at + self._lease_seconds
        if self._lease_until is None or renewed_until > self._lease_until:
            self._lease_until = renewed_until

    def save_delayed(self) -> None:
        self._delayed = True

    def skips_save_queue(self) -> bool:
        """True when the next save goes to the database without the save queue."""
        return self._delayed

    def covers(self, wait: BusyWait) -> bool:
        if self._lease_until is None:
            return False
        return monotonic() + wait.seconds + self._reserve_seconds <= self._lease_until

    def can_renew(self) -> bool:
        """True when the known lease covers a renewal call and then a save call.

        Also True when the lease or the call time is not known, as before.
        """
        if self._lease_until is None or not self._two_calls_seconds:
            return True
        return self._lease_until - monotonic() >= self._two_calls_seconds


def _jittered(delay: float) -> BusyWait:
    # Jitter keeps items that failed together from coming back together.
    return BusyWait(seconds=delay + random.uniform(0.0, delay * JITTER), long=True)


def known_lease_seconds(lease_ends: Iterable[datetime | None]) -> float | None:
    """Seconds that the earliest claimed lease is known to last; None if unknown."""
    ends = list(lease_ends)
    if not ends or any(end is None for end in ends):
        return None
    earliest = min(end for end in ends if end is not None)
    return (earliest - datetime.now(UTC)).total_seconds() - CLOCK_SKEW_SECONDS
