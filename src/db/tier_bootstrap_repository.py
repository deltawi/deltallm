from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from src.db.tier_records import (
    TierRecord,
    TierVersionRecord,
    tier_version_select_sql,
    to_tier_creation_request_record,
    to_version_record,
)


class TierBootstrapIdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TierBootstrapResult:
    tier: TierRecord
    initial_version: TierVersionRecord
    idempotency_resolution: Literal["created", "replayed"]


class TierBootstrapRepositoryMixin:
    prisma: Any | None

    async def create_tier_with_initial_draft(
        self,
        *,
        principal_scope: str,
        idempotency_key: str,
        request_hash: str,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
        created_by_account_id: str | None,
        created_by_kind: str,
    ) -> TierBootstrapResult:
        principal_scope = _bounded_nonblank(principal_scope, "principal_scope", maximum=320)
        idempotency_key = _bounded_nonblank(idempotency_key, "idempotency_key", maximum=200)
        request_hash = _bounded_nonblank(request_hash, "request_hash", maximum=128)
        created_by_kind = _validate_creator(
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
        )
        if self.prisma is None:
            raise RuntimeError("tier bootstrap requires a database")
        self.require_transactions("create_tier_with_initial_draft")

        lock_material = f"tier-bootstrap:v1:{principal_scope}:{idempotency_key}"
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._create_tier_with_initial_draft_in_tx(
                principal_scope=principal_scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                lock_material=lock_material,
                tier_key=tier_key,
                name=name,
                description=description,
                enabled=enabled,
                metadata=metadata,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
            )

    async def _create_tier_with_initial_draft_in_tx(
        self,
        *,
        principal_scope: str,
        idempotency_key: str,
        request_hash: str,
        lock_material: str,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
        created_by_account_id: str | None,
        created_by_kind: str,
    ) -> TierBootstrapResult:
        await self.prisma.query_raw(
            """
            SELECT pg_advisory_xact_lock(hashtextextended($1, 0)) AS locked
            """,
            lock_material,
        )
        replay_rows = await self.prisma.query_raw(
            """
            SELECT
                tier_creation_request_id,
                principal_scope,
                idempotency_key,
                request_hash,
                tier_id,
                created_at
            FROM deltallm_tiercreationrequest
            WHERE principal_scope = $1
              AND idempotency_key = $2
            LIMIT 1
            """,
            principal_scope,
            idempotency_key,
        )
        if replay_rows:
            replay = to_tier_creation_request_record(replay_rows[0])
            if replay.request_hash != request_hash:
                raise TierBootstrapIdempotencyConflictError(
                    "idempotency key was already used with different tier input"
                )
            tier = await self.get_tier(replay.tier_id)
            version_rows = await self.prisma.query_raw(
                f"""
                {tier_version_select_sql()}
                WHERE v.tier_id = $1
                  AND v.version_number = 1
                LIMIT 1
                """,
                replay.tier_id,
            )
            if tier is None or not version_rows:
                raise RuntimeError("tier bootstrap replay references incomplete resources")
            return TierBootstrapResult(
                tier=tier,
                initial_version=to_version_record(version_rows[0]),
                idempotency_resolution="replayed",
            )

        tier = await self.create_tier(
            tier_key=tier_key,
            name=name,
            description=description,
            enabled=enabled,
            metadata=metadata,
        )
        version = await self.create_tier_version(
            tier_id=tier.tier_id,
            version_number=1,
            status="draft",
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
            metadata=None,
        )
        request_rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_tiercreationrequest (
                tier_creation_request_id,
                principal_scope,
                idempotency_key,
                request_hash,
                tier_id,
                created_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, NOW())
            RETURNING tier_creation_request_id
            """,
            principal_scope,
            idempotency_key,
            request_hash,
            tier.tier_id,
        )
        if not request_rows:
            raise RuntimeError("tier bootstrap idempotency insert did not return a row")
        return TierBootstrapResult(
            tier=replace(tier, version_count=1),
            initial_version=version,
            idempotency_resolution="created",
        )


def _bounded_nonblank(value: str, field_name: str, *, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return normalized


def _validate_creator(
    *,
    created_by_account_id: str | None,
    created_by_kind: str,
) -> str:
    normalized_kind = str(created_by_kind or "unknown").strip().lower()
    if normalized_kind not in {"account", "master_key", "system", "unknown"}:
        raise ValueError("created_by_kind is invalid")
    if normalized_kind == "account" and not created_by_account_id:
        raise ValueError("account-created tier versions require created_by_account_id")
    if normalized_kind != "account" and created_by_account_id is not None:
        raise ValueError("created_by_account_id requires created_by_kind=account")
    return normalized_kind


__all__ = [
    "TierBootstrapIdempotencyConflictError",
    "TierBootstrapRepositoryMixin",
    "TierBootstrapResult",
]
