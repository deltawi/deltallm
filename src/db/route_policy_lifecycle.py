from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.db.route_group_identity import RouteGroupIdentityMixin
from src.db.route_policy_dependencies import (
    RoutePolicyStateConflictError,
    lock_policy_group,
    selector_deployment_id,
)
from src.router.policy_validation import (
    CURRENT_POLICY_SEMANTICS_VERSION,
    PolicyMemberInventoryItem,
    merge_policy_document_for_write,
    merge_policy_members,
    normalize_route_policy_write_fields,
    validate_route_policy,
    validate_stored_route_policy,
)
from src.router.route_group_validation import validate_route_group_member_modes
from src.router.selection.policy import (
    SELECTOR_POLICY_SEMANTICS_VERSION,
    RouteSelectorActivationUnsupportedError,
    ensure_selector_activation_supported,
)


@dataclass
class RoutePolicyRecord:
    route_policy_id: str
    route_group_id: str
    version: int
    status: str
    policy_json: dict[str, Any]
    semantics_version: int = 1
    published_at: datetime | None = None
    published_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RoutePolicyWriteResult:
    policy: RoutePolicyRecord
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutePolicyValidationContext:
    group_key: str
    group_mode: str
    inventory: dict[str, PolicyMemberInventoryItem]
    deployments: dict[str, PolicyMemberInventoryItem] | None = None


@dataclass(frozen=True, slots=True)
class StoredRoutePolicyDocument:
    policy_json: dict[str, Any]
    semantics_version: int


@dataclass(frozen=True, slots=True)
class PreparedRoutePolicyWrite:
    """Keep opaque storage fields separate from the canonical validation projection."""

    document: dict[str, Any]
    normalized: dict[str, Any]
    warnings: tuple[str, ...]


def _write_classifier_id(
    payload: Mapping[str, object],
    current: StoredRoutePolicyDocument | None,
) -> str | None:
    if "selector" in payload:
        return selector_deployment_id(payload)
    return (
        selector_deployment_id(current.policy_json, current.semantics_version) if current else None
    )


def parse_policy_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def to_policy_record(row: dict[str, Any]) -> RoutePolicyRecord:
    return RoutePolicyRecord(
        route_policy_id=str(row.get("route_policy_id") or ""),
        route_group_id=str(row.get("route_group_id") or ""),
        version=int(row.get("version") or 0),
        semantics_version=int(row.get("semantics_version") or 1),
        status=str(row.get("status") or "draft"),
        policy_json=parse_policy_json(row.get("policy_json")),
        published_at=_parse_datetime(row.get("published_at")),
        published_by=row.get("published_by"),
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
    )


class RoutePolicyLifecycleMixin(RouteGroupIdentityMixin):
    prisma: Any | None
    selector_activation_check: Callable[[], None] | None

    def _require_selector_publication(
        self, document: dict[str, Any], context: RoutePolicyValidationContext
    ) -> None:
        if document.get("selector") is None:
            return
        from src.router.selection.activation import validate_selector_activation_inventory

        if self.selector_activation_check is None:
            raise RouteSelectorActivationUnsupportedError(
                "selector activation requires durable spend outbox and its worker"
            )
        self.selector_activation_check()
        try:
            validate_selector_activation_inventory(document, context.inventory, context.deployments)
        except ValueError as exc:
            raise RouteSelectorActivationUnsupportedError(str(exc)) from exc

    async def get_published_policy(self, group_key: str) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None
        lookup = self._group_lookup(group_key)
        rows = await self.prisma.query_raw(
            f"""
            SELECT p.route_policy_id, p.route_group_id, p.version, p.semantics_version, p.status,
                   p.policy_json, p.published_at, p.published_by, p.created_at, p.updated_at
            FROM deltallm_routepolicy p
            JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
            WHERE g.{lookup.column} = $1
              AND p.status = 'published'
            ORDER BY p.version DESC
            LIMIT 1
            """,
            lookup.value,
        )
        return to_policy_record(rows[0]) if rows else None

    async def publish_policy(
        self,
        group_key: str,
        policy_json: dict[str, Any],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyWriteResult | None:
        if self.prisma is None:
            return None
        self.require_transactions("publish_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._publish_policy_in_tx(
                group_key,
                policy_json,
                published_by=published_by,
            )

    async def _publish_policy_in_tx(
        self,
        group_key: str,
        policy_json: dict[str, Any],
        *,
        published_by: str | None,
    ) -> RoutePolicyWriteResult | None:
        group_id = await self._lock_group_id(
            group_key,
            classifier_id=selector_deployment_id(policy_json),
        )
        if group_id is None:
            return None
        current = await self._latest_policy_document(group_id, status="published")
        context = await self._load_policy_validation_context(
            group_id,
            classifier_id=_write_classifier_id(policy_json, current),
        )
        prepared = self._prepare_policy_write(
            policy_json,
            current=current,
            context=context,
        )
        ensure_selector_activation_supported(
            prepared.normalized,
            semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
        )
        self._require_selector_publication(prepared.normalized, context)
        policy = await self._replace_published_policy(
            group_id,
            prepared.document,
            semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
            published_by=published_by,
        )
        return RoutePolicyWriteResult(policy, prepared.warnings) if policy is not None else None

    async def save_draft_policy(
        self,
        group_key: str,
        policy_json: dict[str, Any],
    ) -> RoutePolicyWriteResult | None:
        if self.prisma is None:
            return None
        self.require_transactions("save_draft_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._save_draft_policy_in_tx(group_key, policy_json)

    async def _save_draft_policy_in_tx(
        self,
        group_key: str,
        policy_json: dict[str, Any],
    ) -> RoutePolicyWriteResult | None:
        group_id = await self._lock_group_id(
            group_key,
            classifier_id=selector_deployment_id(policy_json),
        )
        if group_id is None:
            return None
        drafts = await self.prisma.query_raw(
            """
            SELECT route_policy_id, policy_json, semantics_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'draft'
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
        )
        if drafts:
            current = StoredRoutePolicyDocument(
                policy_json=parse_policy_json(drafts[0].get("policy_json")),
                semantics_version=int(drafts[0].get("semantics_version") or 1),
            )
        else:
            current = await self._latest_policy_document(group_id, status="published")
        context = await self._load_policy_validation_context(
            group_id,
            classifier_id=_write_classifier_id(policy_json, current),
        )
        prepared = self._prepare_policy_write(
            policy_json,
            current=current,
            context=context,
        )
        if drafts:
            policy = await self._update_draft(
                str(drafts[0]["route_policy_id"]),
                prepared.document,
                semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
            )
        else:
            policy = await self._insert_policy(
                group_id,
                "draft",
                prepared.document,
                semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
                published_by=None,
            )
        return RoutePolicyWriteResult(policy, prepared.warnings) if policy is not None else None

    def _prepare_policy_write(
        self,
        policy_json: dict[str, Any],
        *,
        current: StoredRoutePolicyDocument | None,
        context: RoutePolicyValidationContext,
    ) -> PreparedRoutePolicyWrite:
        if not isinstance(policy_json, dict):
            raise ValueError("policy payload must be an object")
        current_semantics = (
            current.semantics_version if current else CURRENT_POLICY_SEMANTICS_VERSION
        )
        existing_selector = (
            current.policy_json.get("selector")
            if current and current_semantics >= SELECTOR_POLICY_SEMANTICS_VERSION
            else None
        )
        selector_enabled = policy_json.get("selector", existing_selector) is not None
        normalized, warnings = normalize_route_policy_write_fields(
            policy_json,
            selector_enabled=selector_enabled,
            workload_mode=context.group_mode,
        )
        effective = merge_policy_document_for_write(
            current.policy_json if current is not None else None,
            normalized,
            existing_semantics_version=current_semantics,
        )
        try:
            projection, _ = self._validate_policy_document(
                effective,
                context=context,
                semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
                stored_document=True,
            )
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"policy is incompatible with current route-group members: {exc}"
            ) from exc
        return PreparedRoutePolicyWrite(effective, projection, tuple(warnings))

    async def publish_latest_draft(
        self,
        group_key: str,
        *,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None
        self.require_transactions("publish_latest_draft")
        async with self.prisma.tx() as tx:
            repository = self.with_db(tx)
            group_id = await repository._lock_group_id(group_key)
            if group_id is None:
                return None
            return await repository._publish_latest_draft_in_tx(
                group_id,
                published_by=published_by,
            )

    async def _publish_latest_draft_in_tx(
        self,
        group_id: str,
        *,
        published_by: str | None,
    ) -> RoutePolicyRecord | None:
        drafts = await self.prisma.query_raw(
            """
            SELECT route_policy_id, semantics_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'draft'
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
        )
        if not drafts:
            return None
        draft_document = await self._policy_document_by_id(str(drafts[0]["route_policy_id"]))
        if draft_document is None:
            raise RuntimeError("draft policy changed while it was being published")
        context = await self._load_policy_validation_context(
            group_id,
            classifier_id=selector_deployment_id(
                draft_document,
                int(drafts[0].get("semantics_version") or 1),
            ),
        )
        try:
            normalized, _ = self._validate_policy_document(
                draft_document,
                context=context,
                semantics_version=int(drafts[0].get("semantics_version") or 1),
                stored_document=True,
            )
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"draft policy is incompatible with current route-group members: {exc}"
            ) from exc
        ensure_selector_activation_supported(
            normalized,
            semantics_version=int(drafts[0].get("semantics_version") or 1),
        )
        if int(drafts[0].get("semantics_version") or 1) >= SELECTOR_POLICY_SEMANTICS_VERSION:
            self._require_selector_publication(normalized, context)
        await self._archive_published(group_id)
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_routepolicy
            SET status = 'published', published_at = NOW(), published_by = $2, updated_at = NOW()
            WHERE route_policy_id = $1
              AND status = 'draft'
            RETURNING route_policy_id, route_group_id, version, semantics_version, status, policy_json,
                      published_at, published_by, created_at, updated_at
            """,
            str(drafts[0]["route_policy_id"]),
            published_by,
        )
        if not rows:
            raise RuntimeError("draft policy changed while it was being published")
        await self._bump_runtime_revision()
        return to_policy_record(rows[0])

    async def rollback_policy(
        self,
        group_key: str,
        *,
        target_version: int,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None
        self.require_transactions("rollback_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._rollback_policy_in_tx(
                group_key,
                target_version=target_version,
                published_by=published_by,
            )

    async def _rollback_policy_in_tx(
        self,
        group_key: str,
        *,
        target_version: int,
        published_by: str | None,
    ) -> RoutePolicyRecord | None:
        group_id = await self._lock_group_id(group_key, target_version=target_version)
        if group_id is None:
            return None
        source = await self.prisma.query_raw(
            """
            SELECT policy_json, semantics_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND version = $2
            LIMIT 1
            """,
            group_id,
            target_version,
        )
        if not source:
            return None
        source_document = parse_policy_json(source[0].get("policy_json"))
        semantics_version = int(source[0].get("semantics_version") or 1)
        context = await self._load_policy_validation_context(
            group_id,
            classifier_id=selector_deployment_id(source_document, semantics_version),
        )
        try:
            normalized, _ = self._validate_policy_document(
                source_document,
                context=context,
                semantics_version=semantics_version,
                stored_document=True,
            )
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"policy version {target_version} is incompatible with current "
                f"route-group members: {exc}"
            ) from exc
        ensure_selector_activation_supported(
            normalized,
            semantics_version=semantics_version,
        )
        if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION:
            self._require_selector_publication(normalized, context)
        return await self._replace_published_policy(
            group_id,
            source_document,
            semantics_version=semantics_version,
            published_by=published_by,
        )

    async def list_policies(self, group_key: str) -> list[RoutePolicyRecord]:
        if self.prisma is None:
            return []
        lookup = self._group_lookup(group_key)
        rows = await self.prisma.query_raw(
            f"""
            SELECT p.route_policy_id, p.route_group_id, p.version, p.semantics_version, p.status,
                   p.policy_json, p.published_at, p.published_by, p.created_at, p.updated_at
            FROM deltallm_routepolicy p
            JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
            WHERE g.{lookup.column} = $1
            ORDER BY p.version DESC
            """,
            lookup.value,
        )
        return [to_policy_record(row) for row in rows]

    async def _lock_group_id(
        self,
        group_key: str,
        *,
        classifier_id: str | None = None,
        target_version: int | None = None,
    ) -> str | None:
        return await lock_policy_group(
            self.prisma,
            self._group_lookup(group_key),
            classifier_id=classifier_id,
            target_version=target_version,
        )

    async def _latest_policy_document(
        self,
        group_id: str,
        *,
        status: str,
    ) -> StoredRoutePolicyDocument | None:
        rows = await self.prisma.query_raw(
            """
            SELECT policy_json, semantics_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = $2
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
            status,
        )
        if not rows:
            return None
        return StoredRoutePolicyDocument(
            policy_json=parse_policy_json(rows[0].get("policy_json")),
            semantics_version=int(rows[0].get("semantics_version") or 1),
        )

    async def _policy_document_by_id(self, policy_id: str) -> dict[str, Any] | None:
        rows = await self.prisma.query_raw(
            """
            SELECT policy_json
            FROM deltallm_routepolicy
            WHERE route_policy_id = $1
            LIMIT 1
            """,
            policy_id,
        )
        return parse_policy_json(rows[0].get("policy_json")) if rows else None

    async def _load_policy_validation_context(
        self,
        group_id: str,
        *,
        classifier_id: str | None = None,
    ) -> RoutePolicyValidationContext:
        rows = await self.prisma.query_raw(
            """
            SELECT
                g.group_key,
                g.mode AS group_mode,
                m.deployment_id,
                m.enabled,
                d.model_info,
                d.deltallm_params->>'model' AS provider_model,
                d.deltallm_params->>'provider' AS provider_name,
                CASE
                    WHEN d.deployment_id IS NULL THEN NULL
                    ELSE COALESCE(d.model_info->>'mode', 'chat')
                END AS deployment_mode
            FROM deltallm_routegroup g
            LEFT JOIN deltallm_routegroupmember m ON m.route_group_id = g.route_group_id
            LEFT JOIN deltallm_modeldeployment d ON d.deployment_id = m.deployment_id
            WHERE g.route_group_id = $1
            ORDER BY m.created_at ASC, m.deployment_id ASC
            """,
            group_id,
        )
        if not rows:
            raise ValueError("route group no longer exists")
        inventory = {
            str(row.get("deployment_id") or ""): PolicyMemberInventoryItem(
                deployment_id=str(row.get("deployment_id") or ""),
                enabled=bool(row.get("enabled", True)),
                workload_mode=(
                    str(row["deployment_mode"]) if row.get("deployment_mode") is not None else None
                ),
                model_info=parse_policy_json(row.get("model_info")),
                provider_model=row.get("provider_model"),
                provider_name=row.get("provider_name"),
            )
            for row in rows
            if str(row.get("deployment_id") or "")
        }
        deployments = dict(inventory)
        if classifier_id is not None and classifier_id not in deployments:
            target_rows = await self.prisma.query_raw(
                """
                SELECT deployment_id, model_info,
                       deltallm_params->>'model' AS provider_model,
                       deltallm_params->>'provider' AS provider_name,
                       COALESCE(model_info->>'mode', 'chat') AS deployment_mode
                FROM deltallm_modeldeployment WHERE deployment_id = $1
                """,
                classifier_id,
            )
            if target_rows:
                target = target_rows[0]
                deployments[classifier_id] = PolicyMemberInventoryItem(
                    deployment_id=classifier_id,
                    workload_mode=str(target.get("deployment_mode") or ""),
                    model_info=parse_policy_json(target.get("model_info")),
                    provider_model=target.get("provider_model"),
                    provider_name=target.get("provider_name"),
                )
        return RoutePolicyValidationContext(
            group_key=str(rows[0].get("group_key") or ""),
            group_mode=str(rows[0].get("group_mode") or ""),
            inventory=inventory,
            deployments=deployments,
        )

    def _validate_policy_document(
        self,
        policy_json: dict[str, Any],
        *,
        context: RoutePolicyValidationContext,
        semantics_version: int,
        stored_document: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        validator = validate_stored_route_policy if stored_document else validate_route_policy
        normalized, warnings = validator(
            policy_json,
            available_members=context.inventory,
            available_deployments=context.deployments,
            semantics_version=semantics_version,
            workload_mode=context.group_mode,
        )
        effective_members = merge_policy_members(
            [
                {
                    "deployment_id": member.deployment_id,
                    "enabled": member.enabled,
                }
                for member in context.inventory.values()
            ],
            normalized.get("members") if "members" in normalized else None,
            semantics_version=semantics_version,
        )
        active_ids = [
            str(member.get("deployment_id") or "")
            for member in effective_members
            if bool(member.get("enabled", True))
        ]
        validate_route_group_member_modes(
            group_key=context.group_key,
            group_mode=context.group_mode,
            member_ids=active_ids,
            deployment_modes={
                member_id: member.workload_mode
                for member_id, member in context.inventory.items()
                if member.workload_mode is not None
            },
        )
        return normalized, warnings

    async def _validate_published_policy_after_group_change(self, group_id: str) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT policy_json, semantics_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'published'
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
        )
        if not rows:
            return
        try:
            context = await self._load_policy_validation_context(
                group_id,
                classifier_id=selector_deployment_id(
                    parse_policy_json(rows[0].get("policy_json")),
                    int(rows[0].get("semantics_version") or 1),
                ),
            )
            normalized, _ = self._validate_policy_document(
                parse_policy_json(rows[0].get("policy_json")),
                context=context,
                semantics_version=int(rows[0].get("semantics_version") or 1),
                stored_document=True,
            )
            if int(rows[0].get("semantics_version") or 1) >= SELECTOR_POLICY_SEMANTICS_VERSION:
                from src.router.selection.activation import validate_selector_activation_inventory

                # Existing active policies must remain qualified during member/model
                # edits. This reuses the already locked transaction, not readiness.
                validate_selector_activation_inventory(
                    normalized,
                    context.inventory,
                    context.deployments,
                )
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"route-group change would invalidate the published policy: {exc}"
            ) from exc

    async def _replace_published_policy(
        self,
        group_id: str,
        policy_json: dict[str, Any],
        *,
        semantics_version: int,
        published_by: str | None,
    ) -> RoutePolicyRecord | None:
        await self._archive_published(group_id)
        record = await self._insert_policy(
            group_id,
            "published",
            policy_json,
            semantics_version=semantics_version,
            published_by=published_by,
        )
        if record is None:
            raise RuntimeError("failed to publish route policy")
        await self._bump_runtime_revision()
        return record

    async def _archive_published(self, group_id: str) -> None:
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_routepolicy
            SET status = 'archived', updated_at = NOW()
            WHERE route_group_id = $1
              AND status = 'published'
            """,
            group_id,
        )

    async def _insert_policy(
        self,
        group_id: str,
        policy_status: str,
        policy_json: dict[str, Any],
        *,
        semantics_version: int,
        published_by: str | None,
    ) -> RoutePolicyRecord | None:
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routepolicy (
                route_policy_id, route_group_id, version, semantics_version, status, policy_json,
                published_at, published_by, created_at, updated_at
            )
            SELECT gen_random_uuid()::text, $1, COALESCE(MAX(version), 0) + 1,
                   $3, $2, $4::jsonb,
                   CASE WHEN $2 = 'published' THEN NOW() ELSE NULL END,
                   CASE WHEN $2 = 'published' THEN $5 ELSE NULL END,
                   NOW(), NOW()
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
            RETURNING route_policy_id, route_group_id, version, semantics_version, status, policy_json,
                      published_at, published_by, created_at, updated_at
            """,
            group_id,
            policy_status,
            semantics_version,
            json.dumps(policy_json),
            published_by,
        )
        return to_policy_record(rows[0]) if rows else None

    async def _update_draft(
        self,
        draft_id: str,
        policy_json: dict[str, Any],
        *,
        semantics_version: int,
    ) -> RoutePolicyRecord | None:
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_routepolicy
            SET policy_json = $2::jsonb, semantics_version = $3, updated_at = NOW()
            WHERE route_policy_id = $1
              AND status = 'draft'
            RETURNING route_policy_id, route_group_id, version, semantics_version, status, policy_json,
                      published_at, published_by, created_at, updated_at
            """,
            draft_id,
            json.dumps(policy_json),
            semantics_version,
        )
        return to_policy_record(rows[0]) if rows else None
