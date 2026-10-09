"""Persist nullable caller output limits in the existing transactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
import json

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
    model_present: bool = False
    model_value: dict[str, int] | None = None

    @property
    def changed(self) -> bool:
        return self.present or self.model_present


@dataclass(frozen=True, slots=True)
class OutputPolicyPresence:
    key_enabled: bool
    shared_enabled: bool
    tier_enabled: bool = False


async def persist_output_policy(
    db: OutputPolicyDatabase, *, scope: OutputPolicyScope, identity: str, change: OutputPolicyChange
) -> None:
    if not change.changed:
        return
    table, column = _TARGETS[scope]
    if change.present:
        await db.execute_raw(
            f"UPDATE {table} SET output_tpm_limit = $1 WHERE {column} = $2", change.value, identity
        )
    if change.model_present:
        if scope not in {"key", "team"}:
            raise ValueError("Model output limits require a key or team scope")
        await db.execute_raw(
            f"UPDATE {table} SET model_output_tpm_limit = $1::jsonb WHERE {column} = $2",
            json.dumps(change.model_value) if change.model_value is not None else None,
            identity,
        )


async def read_output_policy_presence(db: OutputPolicyDatabase) -> OutputPolicyPresence:
    rows = await db.query_raw("""
        SELECT EXISTS(SELECT 1 FROM deltallm_verificationtoken WHERE output_tpm_limit IS NOT NULL
                      OR (model_output_tpm_limit IS NOT NULL AND model_output_tpm_limit <> '{}'::jsonb)) AS key_enabled,
               (EXISTS(SELECT 1 FROM deltallm_usertable WHERE output_tpm_limit IS NOT NULL) OR
                EXISTS(SELECT 1 FROM deltallm_teamtable WHERE output_tpm_limit IS NOT NULL
                       OR (model_output_tpm_limit IS NOT NULL AND model_output_tpm_limit <> '{}'::jsonb)) OR
                EXISTS(SELECT 1 FROM deltallm_organizationtable WHERE output_tpm_limit IS NOT NULL)) AS shared_enabled,
               EXISTS(SELECT 1 FROM deltallm_tiermodelpolicy p
                      JOIN deltallm_tierversion v ON v.tier_version_id = p.tier_version_id
                      JOIN deltallm_tier t ON t.tier_id = v.tier_id
                      WHERE p.output_tpm_limit IS NOT NULL AND p.enabled AND p.access_mode = 'allow'
                        AND v.status = 'active' AND t.enabled) AS tier_enabled
    """)
    return OutputPolicyPresence(
        key_enabled=bool(rows and rows[0].get("key_enabled")),
        shared_enabled=bool(rows and rows[0].get("shared_enabled")),
        tier_enabled=bool(rows and rows[0].get("tier_enabled")),
    )


async def read_organization_preview_limits(
    db: OutputPolicyDatabase, organization_id: str
) -> dict[str, object]:
    rows = await db.query_raw(
        """SELECT rpm_limit, tpm_limit, output_tpm_limit, rph_limit, rpd_limit, tpd_limit,
                  model_rpm_limit, model_tpm_limit
           FROM deltallm_organizationtable WHERE organization_id = $1 LIMIT 1""",
        organization_id,
    )
    return dict(rows[0]) if rows else {}


async def tier_version_has_output_policy(db: OutputPolicyDatabase, version_id: str) -> bool:
    rows = await db.query_raw(
        """SELECT EXISTS(SELECT 1 FROM deltallm_tiermodelpolicy
                         WHERE tier_version_id = $1 AND enabled AND access_mode = 'allow'
                           AND output_tpm_limit IS NOT NULL) AS output_enabled""",
        version_id,
    )
    return bool(rows and rows[0].get("output_enabled"))
