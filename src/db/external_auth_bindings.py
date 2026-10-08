from __future__ import annotations

from src.db.platform_accounts import PlatformAccountDatabase
from src.db.external_auth_records import ExternalBindingRecord, ExternalRegisteredWorkspace


class ExternalBindingRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def get(self, binding_id: str) -> ExternalBindingRecord | None:
        rows = await self.db.query_raw(
            "SELECT * FROM deltallm_externalauthbinding WHERE binding_id = $1", binding_id
        )
        return ExternalBindingRecord.model_validate(rows[0]) if rows else None

    async def lock_workspace(
        self, binding_id: str, integration_id: str
    ) -> ExternalRegisteredWorkspace | None:
        rows = await self.db.query_raw(
            """
            SELECT to_jsonb(i) AS integration, to_jsonb(b) AS binding
            FROM deltallm_externalauthintegration i JOIN deltallm_externalauthbinding b
              ON b.integration_id = i.integration_id
            WHERE i.integration_id = $1 AND b.binding_id = $2 FOR SHARE OF i, b
            """,
            integration_id,
            binding_id,
        )
        return ExternalRegisteredWorkspace.model_validate(rows[0]) if rows else None

    async def lock(
        self, binding_id: str, integration_id: str, *, write: bool = False
    ) -> ExternalBindingRecord | None:
        lock_mode = "FOR UPDATE" if write else "FOR SHARE"
        rows = await self.db.query_raw(
            f"""
            SELECT * FROM deltallm_externalauthbinding
            WHERE binding_id = $1 AND integration_id = $2 {lock_mode}
            """,
            binding_id,
            integration_id,
        )
        return ExternalBindingRecord.model_validate(rows[0]) if rows else None

    async def lock_active_tenant(self, binding: ExternalBindingRecord) -> bool:
        return await self.lock_tenant_ids(binding.organization_id, binding.team_id)

    async def lock_tenant_ids(self, organization_id: str, team_id: str) -> bool:
        rows = await self.db.query_raw(
            """
            SELECT t.team_id FROM deltallm_organizationtable o
            JOIN deltallm_teamtable t ON t.organization_id = o.organization_id
            WHERE o.organization_id = $1 AND t.team_id = $2 AND o.lifecycle_state = 'active'
            FOR SHARE OF o, t
            """,
            organization_id,
            team_id,
        )
        return bool(rows)

    async def register(
        self, *, integration_id: str, customer_id: str, organization_id: str, team_id: str
    ) -> ExternalBindingRecord:
        rows = await self.db.query_raw(
            """
            INSERT INTO deltallm_externalauthbinding (
                binding_id, integration_id, external_customer_id, organization_id, team_id, updated_at)
            VALUES (gen_random_uuid(), $1, $2, $3, $4, NOW())
            ON CONFLICT (integration_id, external_customer_id)
            DO UPDATE SET updated_at = deltallm_externalauthbinding.updated_at RETURNING *
            """,
            integration_id,
            customer_id,
            organization_id,
            team_id,
        )
        return ExternalBindingRecord.model_validate(rows[0])

    async def set_state(
        self, binding: ExternalBindingRecord, *, state: str, version: int
    ) -> ExternalBindingRecord | None:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthbinding SET state = $2, epoch = epoch + 1,
                version = version + 1, updated_at = NOW()
            WHERE binding_id = $1 AND version = $3 RETURNING *
            """,
            binding.binding_id,
            state,
            version,
        )
        return ExternalBindingRecord.model_validate(rows[0]) if rows else None

    async def list_page(
        self,
        *,
        integration_id: str,
        after: str | None,
        state: str | None,
        customer: str | None,
        limit: int,
    ) -> list[ExternalBindingRecord]:
        rows = await self.db.query_raw(
            """
            SELECT * FROM deltallm_externalauthbinding WHERE integration_id = $1
              AND ($2::text IS NULL OR binding_id > $2) AND ($3::text IS NULL OR state = $3)
              AND ($4::text IS NULL OR external_customer_id = $4)
            ORDER BY binding_id LIMIT $5
            """,
            integration_id,
            after,
            state,
            customer,
            min(max(limit, 1), 101),
        )
        return [ExternalBindingRecord.model_validate(row) for row in rows]
