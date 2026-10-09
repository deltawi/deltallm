"""Database records and mutations for model deployments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.db.json_fields import _parse_metadata
from src.db.routing.callable_key_locks import lock_callable_keys
from src.db.routing.routing_runtime import RoutingRuntimeRevisionRepository
from src.db.routing.route_policy_dependencies import (
    DEPENDENT_GROUPS_QUERY,
    dependency_lock_errors,
    lock_deployment_dependencies,
)


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


@dataclass
class ModelDeploymentRecord:
    deployment_id: str
    model_name: str
    deltallm_params: dict[str, Any]
    model_id: str | None = None
    named_credential_id: str | None = None
    model_info: dict[str, Any] | None = None
    routing_state_incarnation: str | None = None
    credential_binding_mode: str | None = None
    credential_binding_state: str | None = None
    credential_bound_by_account_id: str | None = None
    credential_bound_at: datetime | None = None
    credential_revoked_at: datetime | None = None
    governance_source: str | None = None


def _model_deployment_record(row: dict[str, Any]) -> ModelDeploymentRecord:
    return ModelDeploymentRecord(
        deployment_id=str(row.get("deployment_id") or ""),
        model_name=str(row.get("model_name") or ""),
        model_id=str(row.get("model_id")) if row.get("model_id") is not None else None,
        named_credential_id=str(row.get("named_credential_id"))
        if row.get("named_credential_id") is not None
        else None,
        deltallm_params=_parse_json_object(row.get("deltallm_params")),
        model_info=_parse_metadata(row.get("model_info")),
        routing_state_incarnation=str(row.get("routing_state_incarnation"))
        if row.get("routing_state_incarnation") is not None
        else None,
        credential_binding_mode=str(row.get("credential_binding_mode"))
        if row.get("credential_binding_mode") is not None
        else None,
        credential_binding_state=str(row.get("credential_binding_state"))
        if row.get("credential_binding_state") is not None
        else None,
        credential_bound_by_account_id=str(row.get("credential_bound_by_account_id"))
        if row.get("credential_bound_by_account_id") is not None
        else None,
        credential_bound_at=row.get("credential_bound_at"),
        credential_revoked_at=row.get("credential_revoked_at"),
        governance_source=str(row.get("governance_source"))
        if row.get("governance_source") is not None
        else None,
    )


class _ModelDeploymentChangedWhileLocking(RuntimeError):
    """Retry an update whose callable ownership changed before locks were held."""


class ModelDeploymentRepository:
    def __init__(self, prisma_client: Any | None = None, *, use_transactions: bool = True) -> None:
        self.prisma = prisma_client
        self._use_transactions = use_transactions

    def with_db(self, prisma_client: Any) -> ModelDeploymentRepository:
        return ModelDeploymentRepository(prisma_client, use_transactions=False)

    async def list_all(self) -> list[ModelDeploymentRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT deployment_id, model_name, model_id, named_credential_id,
                   credential_binding_mode, credential_binding_state,
                   credential_bound_by_account_id, credential_bound_at,
                   credential_revoked_at, deltallm_params, model_info,
                   (
                     SELECT asset.governance_source
                     FROM deltallm_model AS logical_model
                     JOIN deltallm_managedasset AS asset
                       ON asset.asset_id = logical_model.managed_asset_id
                     WHERE logical_model.model_id = deltallm_modeldeployment.model_id
                   ) AS governance_source,
                   to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS')
                     AS routing_state_incarnation
            FROM deltallm_modeldeployment
            ORDER BY model_name ASC, created_at ASC
            """
        )
        return [_model_deployment_record(row) for row in rows]

    async def get_by_deployment_id(self, deployment_id: str) -> ModelDeploymentRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT deployment_id, model_name, model_id, named_credential_id,
                   credential_binding_mode, credential_binding_state,
                   credential_bound_by_account_id, credential_bound_at,
                   credential_revoked_at, deltallm_params, model_info,
                   (
                     SELECT asset.governance_source
                     FROM deltallm_model AS logical_model
                     JOIN deltallm_managedasset AS asset
                       ON asset.asset_id = logical_model.managed_asset_id
                     WHERE logical_model.model_id = deltallm_modeldeployment.model_id
                   ) AS governance_source,
                   to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS')
                     AS routing_state_incarnation
            FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            LIMIT 1
            """,
            deployment_id,
        )
        if not rows:
            return None
        return _model_deployment_record(rows[0])

    async def has_model_name(
        self,
        model_name: str,
        *,
        exclude_deployment_id: str | None = None,
        lock_for_share: bool = False,
    ) -> bool:
        if self.prisma is None:
            return False

        exclude_clause = " AND deployment_id <> $2" if exclude_deployment_id is not None else ""
        params = (
            (model_name, exclude_deployment_id)
            if exclude_deployment_id is not None
            else (model_name,)
        )
        if lock_for_share:
            rows = await self.prisma.query_raw(
                f"""
                SELECT deployment_id
                FROM deltallm_modeldeployment
                WHERE model_name = $1
                {exclude_clause}
                LIMIT 1
                FOR KEY SHARE
                """,
                *params,
            )
        else:
            rows = await self.prisma.query_raw(
                f"""
                SELECT deployment_id
                FROM deltallm_modeldeployment
                WHERE model_name = $1
                {exclude_clause}
                LIMIT 1
                """,
                *params,
            )
        return bool(rows)

    async def list_by_deployment_ids(
        self, deployment_ids: list[str]
    ) -> list[ModelDeploymentRecord]:
        if self.prisma is None or not deployment_ids:
            return []

        normalized_ids = [str(item).strip() for item in deployment_ids if str(item).strip()]
        if not normalized_ids:
            return []

        placeholders = ", ".join(f"${index}" for index in range(1, len(normalized_ids) + 1))
        rows = await self.prisma.query_raw(
            f"""
            SELECT deployment_id, model_name, model_id, named_credential_id,
                   credential_binding_mode, credential_binding_state,
                   credential_bound_by_account_id, credential_bound_at,
                   credential_revoked_at, deltallm_params, model_info,
                   (
                     SELECT asset.governance_source
                     FROM deltallm_model AS logical_model
                     JOIN deltallm_managedasset AS asset
                       ON asset.asset_id = logical_model.managed_asset_id
                     WHERE logical_model.model_id = deltallm_modeldeployment.model_id
                   ) AS governance_source,
                   to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS')
                     AS routing_state_incarnation
            FROM deltallm_modeldeployment
            WHERE deployment_id IN ({placeholders})
            ORDER BY model_name ASC, created_at ASC
            """,
            *normalized_ids,
        )
        return [_model_deployment_record(row) for row in rows]

    async def create(self, record: ModelDeploymentRecord) -> ModelDeploymentRecord:
        if self.prisma is None:
            return record
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).create(record)

        await lock_callable_keys(self.prisma, record.model_name)
        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_modeldeployment (
                deployment_id,
                model_name,
                model_id,
                named_credential_id,
                deltallm_params,
                model_info,
                credential_binding_mode,
                credential_binding_state,
                credential_bound_by_account_id,
                credential_bound_at,
                credential_revoked_at,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb,
                    $7::text, $8::text, $9::text,
                    CASE WHEN $7::text IS NULL THEN NULL ELSE NOW() END,
                    NULL, NOW(), NOW())
            """,
            record.deployment_id,
            record.model_name,
            record.model_id,
            record.named_credential_id,
            json.dumps(record.deltallm_params),
            json.dumps(record.model_info) if record.model_info is not None else None,
            record.credential_binding_mode,
            record.credential_binding_state,
            record.credential_bound_by_account_id,
        )
        if not self._use_transactions:
            await self._bump_runtime_revision()
        return record

    async def update(
        self,
        deployment_id: str,
        *,
        model_name: str,
        named_credential_id: str | None,
        deltallm_params: dict[str, Any],
        model_info: dict[str, Any] | None,
        display_name: str | None = None,
        credential_binding_mode: str | None = None,
        credential_binding_state: str | None = None,
        credential_bound_by_account_id: str | None = None,
        clear_credential_binding: bool = False,
    ) -> ModelDeploymentRecord | None:
        if self.prisma is None:
            return None
        if self._use_transactions and hasattr(self.prisma, "tx"):
            for attempt in range(3):
                try:
                    async with self.prisma.tx() as tx:
                        return await self.with_db(tx).update(
                            deployment_id,
                            model_name=model_name,
                            named_credential_id=named_credential_id,
                            deltallm_params=deltallm_params,
                            model_info=model_info,
                            display_name=display_name,
                            credential_binding_mode=credential_binding_mode,
                            credential_binding_state=credential_binding_state,
                            credential_bound_by_account_id=credential_bound_by_account_id,
                            clear_credential_binding=clear_credential_binding,
                        )
                except _ModelDeploymentChangedWhileLocking:
                    if attempt == 2:
                        raise RuntimeError(
                            "model deployment changed repeatedly while acquiring callable locks"
                        ) from None

        current_rows = await self.prisma.query_raw(
            """
            SELECT model_name
            FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            """,
            deployment_id,
        )
        if not current_rows:
            return None
        current_model_name = str(current_rows[0].get("model_name") or "")
        await lock_callable_keys(self.prisma, current_model_name, model_name)
        confirmed_rows = await self.prisma.query_raw(
            """
            SELECT model_name
            FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            """,
            deployment_id,
        )
        if not confirmed_rows:
            return None
        if str(confirmed_rows[0].get("model_name") or "") != current_model_name:
            raise _ModelDeploymentChangedWhileLocking
        locked_groups = (
            await self._lock_route_groups_for_deployment(deployment_id)
            if not self._use_transactions
            else []
        )

        if display_name is not None:
            await self.prisma.execute_raw(
                """
                UPDATE deltallm_model
                SET display_name = $2,
                    updated_at = NOW()
                WHERE model_id = (
                    SELECT model_id
                    FROM deltallm_modeldeployment
                    WHERE deployment_id = $1
                )
                """,
                deployment_id,
                display_name,
            )

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_modeldeployment
            SET model_name = $2,
                model_id = (
                    SELECT model_id FROM deltallm_model WHERE model_name = $2 LIMIT 1
                ),
                named_credential_id = $3,
                deltallm_params = $4::jsonb,
                model_info = $5::jsonb,
                credential_binding_mode = CASE
                    WHEN $9::boolean THEN NULL
                    ELSE COALESCE($6, credential_binding_mode)
                END,
                credential_binding_state = CASE
                    WHEN $9::boolean THEN NULL
                    ELSE COALESCE($7, credential_binding_state)
                END,
                credential_bound_by_account_id = CASE
                    WHEN $9::boolean THEN NULL
                    ELSE COALESCE($8, credential_bound_by_account_id)
                END,
                credential_bound_at = CASE
                    WHEN $9::boolean THEN NULL
                    WHEN $6 IS NOT NULL THEN NOW()
                    ELSE credential_bound_at
                END,
                credential_revoked_at = CASE
                    WHEN $9::boolean THEN NULL
                    WHEN COALESCE($7, credential_binding_state) = 'active' THEN NULL
                    ELSE credential_revoked_at
                END,
                updated_at = NOW()
            WHERE deployment_id = $1
            RETURNING deployment_id, model_name, model_id, named_credential_id,
                      credential_binding_mode, credential_binding_state,
                      credential_bound_by_account_id, credential_bound_at,
                      credential_revoked_at, deltallm_params, model_info,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS') AS routing_state_incarnation
            """,
            deployment_id,
            model_name,
            named_credential_id,
            json.dumps(deltallm_params),
            json.dumps(model_info) if model_info is not None else None,
            credential_binding_mode,
            credential_binding_state,
            credential_bound_by_account_id,
            clear_credential_binding,
        )
        if not rows:
            return None
        await self._validate_locked_route_groups(locked_groups)
        if not self._use_transactions:
            await self._bump_runtime_revision()
        return _model_deployment_record(rows[0])

    async def delete(self, deployment_id: str) -> bool:
        if self.prisma is None:
            return False
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).delete(deployment_id)

        locked_groups = (
            await self._lock_route_groups_for_deployment(deployment_id)
            if not self._use_transactions
            else []
        )

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            RETURNING deployment_id
            """,
            deployment_id,
        )
        if rows:
            await self._validate_locked_route_groups(locked_groups)
            if not self._use_transactions:
                await self._bump_runtime_revision()
        return bool(rows)

    async def revoke_credential_binding(
        self,
        deployment_id: str,
        *,
        expected_credential_id: str,
    ) -> ModelDeploymentRecord | None:
        """Revoke one exact live binding without letting routing dependencies block it."""

        if self.prisma is None:
            return None
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).revoke_credential_binding(
                    deployment_id,
                    expected_credential_id=expected_credential_id,
                )

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_modeldeployment
            SET named_credential_id = NULL,
                credential_binding_state = 'revoked',
                credential_revoked_at = NOW(),
                updated_at = NOW()
            WHERE deployment_id = $1
              AND named_credential_id = $2
              AND credential_binding_state = 'active'
            RETURNING deployment_id, model_name, model_id, named_credential_id,
                      credential_binding_mode, credential_binding_state,
                      credential_bound_by_account_id, credential_bound_at,
                      credential_revoked_at, deltallm_params, model_info,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS')
                        AS routing_state_incarnation
            """,
            deployment_id,
            expected_credential_id,
        )
        if not rows:
            return None
        if not self._use_transactions:
            await self._bump_runtime_revision()
        return _model_deployment_record(rows[0])

    async def _lock_route_groups_for_deployment(
        self,
        deployment_id: str,
    ) -> list[tuple[str, str]]:
        await lock_deployment_dependencies(self.prisma, {deployment_id})
        with dependency_lock_errors():
            rows = await self.prisma.query_raw(
                DEPENDENT_GROUPS_QUERY,
                deployment_id,
            )
        return [
            (str(row.get("route_group_id") or ""), str(row.get("group_key") or ""))
            for row in rows
            if row.get("route_group_id") and row.get("group_key")
        ]

    async def _validate_locked_route_groups(
        self,
        groups: list[tuple[str, str]],
    ) -> None:
        if not groups:
            return
        from src.db.routing.route_groups import RouteGroupRepository
        from src.db.routing.route_policy_lifecycle import RoutePolicyStateConflictError

        route_groups = RouteGroupRepository(self.prisma, use_transactions=False)
        try:
            for group_id, group_key in groups:
                await route_groups._validate_runtime_invariants_after_group_change(
                    group_id,
                    group_key=group_key,
                )
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"model deployment change would invalidate route group '{group_key}': {exc}"
            ) from exc

    async def _bump_runtime_revision(self) -> int:
        return await RoutingRuntimeRevisionRepository(self.prisma).bump_revision()

    async def bulk_insert_if_empty(self, records: list[ModelDeploymentRecord]) -> bool:
        if self.prisma is None or not records:
            return False
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).bulk_insert_if_empty(records)

        count_rows = await self.prisma.query_raw(
            "SELECT COUNT(*)::int AS count FROM deltallm_modeldeployment"
        )
        if count_rows and int(count_rows[0].get("count") or 0) > 0:
            return False

        for record in records:
            await self.prisma.execute_raw(
                """
                INSERT INTO deltallm_modeldeployment (
                    deployment_id,
                    model_name,
                    model_id,
                    named_credential_id,
                    deltallm_params,
                    model_info,
                    credential_binding_mode,
                    credential_binding_state,
                    credential_bound_by_account_id,
                    credential_bound_at,
                    credential_revoked_at,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb,
                        $7::text, $8::text, $9::text,
                        CASE WHEN $7::text IS NULL THEN NULL ELSE NOW() END,
                        NULL, NOW(), NOW())
                ON CONFLICT (deployment_id) DO NOTHING
                """,
                record.deployment_id,
                record.model_name,
                record.model_id,
                record.named_credential_id,
                json.dumps(record.deltallm_params),
                json.dumps(record.model_info) if record.model_info is not None else None,
                record.credential_binding_mode,
                record.credential_binding_state,
                record.credential_bound_by_account_id,
            )
        if not self._use_transactions:
            await self._bump_runtime_revision()
        return True
