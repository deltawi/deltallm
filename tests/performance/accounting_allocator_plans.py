"""Capture actual nested SQL plans on one owned diagnostic connection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
from typing import Literal, cast

import asyncpg


@dataclass(slots=True, repr=False)
class CapturedAccountingPlan:
    query: str = field(repr=False)
    node: dict[str, object] = field(repr=False)
    jit_functions: int = 0

    def safe_report(self) -> dict[str, object]:
        return {
            "query_sha256": hashlib.sha256(self.query.encode()).hexdigest(),
            "plan": safe_plan(self.node),
            "jit_functions": self.jit_functions,
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
            jit = value.get("JIT", {})
            if not isinstance(jit, dict):
                raise ValueError("invalid JIT fields")
            functions = jit.get("Functions", 0)
            if type(functions) is not int or not 0 <= functions <= 1_000_000:
                raise ValueError("invalid JIT function count")
            self.plans.append(CapturedAccountingPlan(value["Query Text"], value["Plan"], functions))
            self._bytes += size
        except (KeyError, ValueError, TypeError):
            self._error("plan capture returned invalid JSON")

    def _error(self, reason: str) -> None:
        if reason not in self.errors:
            self.errors.append(reason)


@asynccontextmanager
async def capture_accounting_plans(
    database_url: str,
    *,
    planner: Literal["auto", "generic", "custom", "alternate_join"] = "auto",
) -> AsyncIterator[AccountingPlanCapture]:
    # These settings affect only this diagnostic connection, never the runtime.
    planner_sql = {
        "auto": "SET plan_cache_mode=auto",
        "generic": "SET plan_cache_mode=force_generic_plan",
        "custom": "SET plan_cache_mode=force_custom_plan",
        "alternate_join": "SET plan_cache_mode=force_generic_plan;SET enable_nestloop=off",
    }[planner]
    connection = await asyncpg.connect(database_url, timeout=5, command_timeout=5)
    capture = AccountingPlanCapture(connection)
    try:
        await connection.execute("LOAD 'auto_explain'")
        await connection.execute(planner_sql)
        if planner == "alternate_join":
            # Penalized join costs must not cause JIT to dominate this probe.
            await connection.execute("SET jit=off")
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
