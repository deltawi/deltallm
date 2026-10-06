"""Persist nullable caller output limits in the existing transactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

OutputPolicyScope = Literal["key", "user", "team", "organization"]
_TARGETS = {
    "key": ("deltallm_verificationtoken", "token"),
    "user": ("deltallm_usertable", "user_id"),
    "team": ("deltallm_teamtable", "team_id"),
    "organization": ("deltallm_organizationtable", "organization_id"),
}


class OutputPolicyDatabase(Protocol):
    async def execute_raw(self, query: str, *params: object) -> int: ...
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class OutputPolicyChange:
    present: bool
    value: int | None


@dataclass(frozen=True, slots=True)
class OutputPolicyPresence:
    key_enabled: bool
    shared_enabled: bool


async def persist_output_policy(
    db: OutputPolicyDatabase, *, scope: OutputPolicyScope, identity: str, change: OutputPolicyChange
) -> None:
    if not change.present:
        return
    table, column = _TARGETS[scope]
    await db.execute_raw(
        f"UPDATE {table} SET output_tpm_limit = $1 WHERE {column} = $2", change.value, identity
    )


async def read_output_policy_presence(db: OutputPolicyDatabase) -> OutputPolicyPresence:
    rows = await db.query_raw("""
        SELECT EXISTS(SELECT 1 FROM deltallm_verificationtoken WHERE output_tpm_limit IS NOT NULL) AS key_enabled,
               (EXISTS(SELECT 1 FROM deltallm_usertable WHERE output_tpm_limit IS NOT NULL) OR
                EXISTS(SELECT 1 FROM deltallm_teamtable WHERE output_tpm_limit IS NOT NULL) OR
                EXISTS(SELECT 1 FROM deltallm_organizationtable WHERE output_tpm_limit IS NOT NULL)) AS shared_enabled
    """)
    return OutputPolicyPresence(
        key_enabled=bool(rows and rows[0].get("key_enabled")),
        shared_enabled=bool(rows and rows[0].get("shared_enabled")),
    )
