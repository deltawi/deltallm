"""One bounded batch, deadline, result-set, and retry owner for accounting persistence."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import TypeVar

from src.db.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    AccountingResultFailure,
    outcome_may_be_ambiguous,
)
from src.db.accounting_permit_results import invalid_result

T = TypeVar("T")


async def with_recovery(
    keys: Sequence[str],
    attempt: Callable[[float], Awaitable[dict[str, T]]],
    recover: Callable[[float], Awaitable[dict[str, T]]],
    *,
    calls: AccountingDatabaseCalls,
    expires_at: float,
) -> tuple[dict[str, T], dict[str, T]]:
    deadline = calls.attempt_deadline(expires_at)
    recovered: dict[str, T] = {}
    for number in range(3):
        try:
            return await attempt(deadline), recovered
        except AccountingProtocolUnavailable as exc:
            if exc.reason in {item.value for item in AccountingResultFailure}:
                raise
            if outcome_may_be_ambiguous(exc):
                try:
                    recovered.update(await recover(expires_at))
                except AccountingProtocolUnavailable as recovery_error:
                    if recovery_error.reason in {item.value for item in AccountingResultFailure}:
                        raise
                if len(recovered) == len(keys):
                    return recovered, recovered
            remaining = deadline - asyncio.get_running_loop().time()
            if number == 2 or remaining <= 0.01:
                raise
            await asyncio.sleep(min(0.01 * (number + 1), remaining / 2))
    raise invalid_result()  # pragma: no cover - each attempt returns or raises


def result_rows(
    rows: Sequence[Mapping[str, object]], field: str, keys: Sequence[str]
) -> dict[str, Mapping[str, object]]:
    result = {str(row.get(field)): row for row in rows}
    if len(result) != len(rows) or set(result) != set(keys):
        raise AccountingProtocolUnavailable(AccountingResultFailure.INCOMPLETE_RESULT)
    return result


def batch_payload(values: list[dict[str, object]], keys: Sequence[str]) -> str:
    if len(values) > 256 or len(set(keys)) != len(keys):
        raise ValueError("permit batches must have up to 256 unique identities")
    payload = json.dumps(values, allow_nan=False, separators=(",", ":"), sort_keys=True)
    if len(payload.encode()) > 1_048_576:
        raise ValueError("permit batch exceeds its serialized size limit")
    return payload


def one_generation(values: Iterable[int]) -> int:
    generations = set(values)
    if len(generations) != 1:
        raise ValueError("one permit batch cannot mix protocol generations")
    return generations.pop()
