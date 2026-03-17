from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpendReadSource:
    table: str
    user_column: str
    end_user_column: str
    prompt_tokens_column: str
    completion_tokens_column: str
    cached_prompt_tokens_column: str
    cached_completion_tokens_column: str
    organization_column: str | None = None

    def column(self, column: str, *, table_alias: str | None = None) -> str:
        target = getattr(self, column)
        if table_alias:
            return f"{table_alias}.{target}"
        return target


SPEND_READ_SOURCE = SpendReadSource(
    table="deltallm_spendlog_events",
    user_column="user_id",
    end_user_column="end_user_id",
    prompt_tokens_column="input_tokens",
    completion_tokens_column="output_tokens",
    cached_prompt_tokens_column="cached_input_tokens",
    cached_completion_tokens_column="cached_output_tokens",
    organization_column="organization_id",
)


def get_spend_read_source() -> SpendReadSource:
    return SPEND_READ_SOURCE


def apply_org_scope(
    *,
    clauses: list[str],
    params: list[Any],
    org_ids: list[str],
    source: SpendReadSource,
    table_alias: str | None = None,
) -> None:
    if not org_ids:
        clauses.append("1 = 0")
        return

    if source.organization_column is not None:
        org_column = source.column("organization_column", table_alias=table_alias)
        placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(org_ids)))
        params.extend(org_ids)
        clauses.append(f"{org_column} IN ({placeholders})")
        return

    team_column = "team_id" if table_alias is None else f"{table_alias}.team_id"
    placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(org_ids)))
    params.extend(org_ids)
    clauses.append(
        f"{team_column} IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({placeholders}))"
    )
