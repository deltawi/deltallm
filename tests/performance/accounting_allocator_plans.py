"""Capture actual nested SQL plans on one owned diagnostic connection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
from typing import cast

import asyncpg


@dataclass(slots=True, repr=False)
class CapturedAccountingPlan:
    query: str = field(repr=False)
    node: dict[str, object] = field(repr=False)

    def safe_report(self) -> dict[str, object]:
        return {
            "query_sha256": hashlib.sha256(self.query.encode()).hexdigest(),
            "plan": safe_plan(self.node),
        }


def safe_plan(node: Mapping[str, object]) -> dict[str, object]:
    result = {
        name: node[name]
        for name in (
            "Node Type",
            "Relation Name",
            "Index Name",
            "Actual Rows",
            "Actual Loops",
            "Rows Removed by Filter",
            "Rows Removed by Index Recheck",
            "Shared Hit Blocks",
            "Shared Read Blocks",
            "Temp Read Blocks",
            "Temp Written Blocks",
        )
        if name in node
    }
    children = cast(list[dict[str, object]], node.get("Plans", []))
    if children:
        result["Plans"] = [safe_plan(child) for child in children]
    return result


class AccountingPlanCapture:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection
        self.plans: list[CapturedAccountingPlan] = []
        self.errors: list[str] = []
        self._bytes = 0

    async def query_raw(self, query: str, *parameters: object) -> Sequence[Mapping[str, object]]:
        return [dict(row) for row in await self._connection.fetch(query, *parameters)]

    def capture(self, _connection: asyncpg.Connection, message: asyncpg.LogMessage) -> None:
        if "plan:\n" not in message.message:
            return
        size = len(message.message.encode())
        if len(self.plans) >= 512 or size > 2_097_152 or self._bytes + size > 67_108_864:
            self._error("plan capture exceeded its bound")
            return
        try:
            value = json.loads(message.message.split("plan:\n", 1)[1])
            if not isinstance(value["Query Text"], str) or not isinstance(value["Plan"], dict):
                raise ValueError("invalid plan fields")
            self.plans.append(CapturedAccountingPlan(value["Query Text"], value["Plan"]))
            self._bytes += size
        except (KeyError, ValueError, TypeError):
            self._error("plan capture returned invalid JSON")

    def _error(self, reason: str) -> None:
        if reason not in self.errors:
            self.errors.append(reason)


@asynccontextmanager
async def capture_accounting_plans(database_url: str) -> AsyncIterator[AccountingPlanCapture]:
    connection = await asyncpg.connect(database_url, timeout=5, command_timeout=5)
    capture = AccountingPlanCapture(connection)
    try:
        await connection.execute("LOAD 'auto_explain'")
        connection.add_log_listener(capture.capture)
        await connection.execute(
            "SET client_min_messages=log;"
            "SET auto_explain.log_analyze=on;"
            "SET auto_explain.log_buffers=on;"
            "SET auto_explain.log_nested_statements=on;"
            "SET auto_explain.log_format=json;"
            "SET auto_explain.log_min_duration=0;"
        )
        yield capture
        await connection.execute("SET auto_explain.log_min_duration=-1")
    finally:
        await connection.close(timeout=5)
