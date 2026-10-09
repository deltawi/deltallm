from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db.routing.routing_runtime import RoutingRuntimeRevisionRepository
from src.services.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
)


class ManagedAssetNotFoundError(LookupError):
    pass


class ManagedAssetPolicyConflictError(RuntimeError):
    pass


class ManagedAssetAudienceNotFoundError(ValueError):
    pass


class ManagedAssetSnapshotLimitError(RuntimeError):
    pass


_RESOURCE_TABLES: dict[AssetKind, tuple[str, str]] = {
    AssetKind.NAMED_CREDENTIAL: ("deltallm_namedcredential", "credential_id"),
    AssetKind.MODEL: ("deltallm_model", "model_id"),
    AssetKind.ROUTE_GROUP: ("deltallm_routegroup", "route_group_id"),
    AssetKind.MCP_SERVER: ("deltallm_mcpserver", "mcp_server_id"),
    AssetKind.PROMPT_TEMPLATE: ("deltallm_prompttemplate", "prompt_template_id"),
}

_RUNTIME_AUTHORIZATION_KINDS = frozenset(
    {
        AssetKind.MODEL,
        AssetKind.ROUTE_GROUP,
        AssetKind.MCP_SERVER,
        AssetKind.PROMPT_TEMPLATE,
    }
)

DEFAULT_MANAGED_ASSET_POLICY_LIMIT = 50_000


def _is_foreign_key_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return "foreign key" in message or "foreign_key_violation" in message or "p2003" in message


@dataclass(frozen=True, slots=True)
class ManagedAssetReconciliationResult:
    named_credentials_linked: int = 0
    models_linked: int = 0
    model_deployments_linked: int = 0
    route_groups_linked: int = 0
    mcp_servers_linked: int = 0
    prompt_templates_linked: int = 0

    @property
    def total_changes(self) -> int:
        return (
            self.named_credentials_linked
            + self.models_linked
            + self.model_deployments_linked
            + self.route_groups_linked
            + self.mcp_servers_linked
            + self.prompt_templates_linked
        )


@dataclass(frozen=True, slots=True)
class ManagedAssetLinkHealth:
    missing_named_credentials: int = 0
    missing_models: int = 0
    missing_model_deployments: int = 0
    missing_route_groups: int = 0
    missing_mcp_servers: int = 0
    missing_prompt_templates: int = 0
    kind_mismatches: int = 0
    orphaned_policies: int = 0

    @property
    def missing_links(self) -> int:
        return (
            self.missing_named_credentials
            + self.missing_models
            + self.missing_model_deployments
            + self.missing_route_groups
            + self.missing_mcp_servers
            + self.missing_prompt_templates
        )

    @property
    def ready(self) -> bool:
        return self.missing_links == 0 and self.kind_mismatches == 0


class ManagedAssetAccessRepository:
    def __init__(
        self,
        prisma_client: Any | None = None,
        *,
        use_transactions: bool = True,
        max_snapshot_policies: int = DEFAULT_MANAGED_ASSET_POLICY_LIMIT,
    ) -> None:
        self.prisma = prisma_client
        self._use_transactions = use_transactions
        self.max_snapshot_policies = max(1, int(max_snapshot_policies))

    def with_db(self, prisma_client: Any) -> ManagedAssetAccessRepository:
        return ManagedAssetAccessRepository(
            prisma_client,
            use_transactions=False,
            max_snapshot_policies=self.max_snapshot_policies,
        )

    async def reconcile_missing_links(
        self,
        *,
        batch_size: int = 250,
    ) -> ManagedAssetReconciliationResult:
        """Adopt rows written by an older binary as platform-managed assets.

        Creator ownership is intentionally never inferred. A legacy row has no reliable
        creator-access intent, so assigning it to a user would be an unsafe privilege change.
        """

        if self.prisma is None:
            return ManagedAssetReconciliationResult()
        normalized_batch_size = max(1, min(int(batch_size), 10_000))
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).reconcile_missing_links(
                    batch_size=normalized_batch_size
                )

        named_credentials = await self._reconcile_resource_links(
            table_name="deltallm_namedcredential",
            id_column="credential_id",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            batch_size=normalized_batch_size,
            created_by_column="created_by_account_id",
        )
        models = await self._reconcile_resource_links(
            table_name="deltallm_model",
            id_column="model_id",
            asset_kind=AssetKind.MODEL,
            batch_size=normalized_batch_size,
        )
        route_groups = await self._reconcile_resource_links(
            table_name="deltallm_routegroup",
            id_column="route_group_id",
            asset_kind=AssetKind.ROUTE_GROUP,
            batch_size=normalized_batch_size,
        )
        mcp_servers = await self._reconcile_resource_links(
            table_name="deltallm_mcpserver",
            id_column="mcp_server_id",
            asset_kind=AssetKind.MCP_SERVER,
            batch_size=normalized_batch_size,
            created_by_column="created_by_account_id",
        )
        prompt_templates = await self._reconcile_resource_links(
            table_name="deltallm_prompttemplate",
            id_column="prompt_template_id",
            asset_kind=AssetKind.PROMPT_TEMPLATE,
            batch_size=normalized_batch_size,
        )

        # The database compatibility trigger owns logical-model creation because it can
        # serialize concurrent legacy inserts by model name without inventing ownership.
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT deployment_id
                FROM deltallm_modeldeployment
                WHERE model_id IS NULL
                ORDER BY created_at ASC, deployment_id ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_modeldeployment AS deployment
            SET model_id = NULL,
                updated_at = NOW()
            FROM candidates
            WHERE deployment.deployment_id = candidates.deployment_id
            RETURNING deployment.deployment_id
            """,
            normalized_batch_size,
        )
        model_deployments = len(rows)
        if models or model_deployments:
            await RoutingRuntimeRevisionRepository(self.prisma).bump_revision()

        return ManagedAssetReconciliationResult(
            named_credentials_linked=named_credentials,
            models_linked=models,
            model_deployments_linked=model_deployments,
            route_groups_linked=route_groups,
            mcp_servers_linked=mcp_servers,
            prompt_templates_linked=prompt_templates,
        )

    async def get_link_health(self) -> ManagedAssetLinkHealth:
        if self.prisma is None:
            return ManagedAssetLinkHealth()
        rows = await self.prisma.query_raw(
            """
            SELECT
              (SELECT COUNT(*)::int FROM deltallm_namedcredential
               WHERE managed_asset_id IS NULL) AS missing_named_credentials,
              (SELECT COUNT(*)::int FROM deltallm_model
               WHERE managed_asset_id IS NULL) AS missing_models,
              (SELECT COUNT(*)::int FROM deltallm_modeldeployment
               WHERE model_id IS NULL) AS missing_model_deployments,
              (SELECT COUNT(*)::int FROM deltallm_routegroup
               WHERE managed_asset_id IS NULL) AS missing_route_groups,
              (SELECT COUNT(*)::int FROM deltallm_mcpserver
               WHERE managed_asset_id IS NULL) AS missing_mcp_servers,
              (SELECT COUNT(*)::int FROM deltallm_prompttemplate
               WHERE managed_asset_id IS NULL) AS missing_prompt_templates,
              (
                SELECT COUNT(*)::int
                FROM (
                  SELECT credential.managed_asset_id
                  FROM deltallm_namedcredential AS credential
                  JOIN deltallm_managedasset AS asset
                    ON asset.asset_id = credential.managed_asset_id
                  WHERE asset.asset_kind <> 'named_credential'
                  UNION ALL
                  SELECT model.managed_asset_id
                  FROM deltallm_model AS model
                  JOIN deltallm_managedasset AS asset
                    ON asset.asset_id = model.managed_asset_id
                  WHERE asset.asset_kind <> 'model'
                  UNION ALL
                  SELECT route_group.managed_asset_id
                  FROM deltallm_routegroup AS route_group
                  JOIN deltallm_managedasset AS asset
                    ON asset.asset_id = route_group.managed_asset_id
                  WHERE asset.asset_kind <> 'route_group'
                  UNION ALL
                  SELECT server.managed_asset_id
                  FROM deltallm_mcpserver AS server
                  JOIN deltallm_managedasset AS asset
                    ON asset.asset_id = server.managed_asset_id
                  WHERE asset.asset_kind <> 'mcp_server'
                  UNION ALL
                  SELECT prompt.managed_asset_id
                  FROM deltallm_prompttemplate AS prompt
                  JOIN deltallm_managedasset AS asset
                    ON asset.asset_id = prompt.managed_asset_id
                  WHERE asset.asset_kind <> 'prompt_template'
                  UNION ALL
                  SELECT deployment.model_id
                  FROM deltallm_modeldeployment AS deployment
                  JOIN deltallm_model AS model ON model.model_id = deployment.model_id
                  WHERE model.model_name <> deployment.model_name
                ) AS mismatches
              ) AS kind_mismatches,
              (
                SELECT COUNT(*)::int
                FROM deltallm_managedasset AS asset
                WHERE NOT EXISTS (
                  SELECT 1 FROM deltallm_namedcredential AS resource
                  WHERE asset.asset_kind = 'named_credential'
                    AND resource.managed_asset_id = asset.asset_id
                  UNION ALL
                  SELECT 1 FROM deltallm_model AS resource
                  WHERE asset.asset_kind = 'model'
                    AND resource.managed_asset_id = asset.asset_id
                  UNION ALL
                  SELECT 1 FROM deltallm_routegroup AS resource
                  WHERE asset.asset_kind = 'route_group'
                    AND resource.managed_asset_id = asset.asset_id
                  UNION ALL
                  SELECT 1 FROM deltallm_mcpserver AS resource
                  WHERE asset.asset_kind = 'mcp_server'
                    AND resource.managed_asset_id = asset.asset_id
                  UNION ALL
                  SELECT 1 FROM deltallm_prompttemplate AS resource
                  WHERE asset.asset_kind = 'prompt_template'
                    AND resource.managed_asset_id = asset.asset_id
                )
              ) AS orphaned_policies
            """
        )
        row = rows[0] if rows else {}
        return ManagedAssetLinkHealth(
            missing_named_credentials=int(row.get("missing_named_credentials") or 0),
            missing_models=int(row.get("missing_models") or 0),
            missing_model_deployments=int(row.get("missing_model_deployments") or 0),
            missing_route_groups=int(row.get("missing_route_groups") or 0),
            missing_mcp_servers=int(row.get("missing_mcp_servers") or 0),
            missing_prompt_templates=int(row.get("missing_prompt_templates") or 0),
            kind_mismatches=int(row.get("kind_mismatches") or 0),
            orphaned_policies=int(row.get("orphaned_policies") or 0),
        )

    async def _reconcile_resource_links(
        self,
        *,
        table_name: str,
        id_column: str,
        asset_kind: AssetKind,
        batch_size: int,
        created_by_column: str | None = None,
    ) -> int:
        created_by_join = ""
        created_by_value = "NULL::text"
        if created_by_column is not None:
            created_by_join = (
                "LEFT JOIN deltallm_platformaccount AS account "
                f"ON account.account_id = resource.{created_by_column}"
            )
            created_by_value = "account.account_id"
        rows = await self.prisma.query_raw(
            f"""
            WITH candidates AS MATERIALIZED (
                SELECT
                    resource.{id_column} AS resource_id,
                    gen_random_uuid()::text AS asset_id,
                    {created_by_value} AS created_by_account_id,
                    resource.created_at,
                    resource.updated_at
                FROM {table_name} AS resource
                {created_by_join}
                WHERE resource.managed_asset_id IS NULL
                ORDER BY resource.created_at ASC, resource.{id_column} ASC
                LIMIT $1
                FOR UPDATE OF resource SKIP LOCKED
            ), inserted_assets AS (
                INSERT INTO deltallm_managedasset (
                    asset_id,
                    asset_kind,
                    governance_source,
                    created_by_account_id,
                    created_at,
                    updated_at
                )
                SELECT
                    asset_id,
                    $2,
                    'platform',
                    created_by_account_id,
                    created_at,
                    updated_at
                FROM candidates
                RETURNING asset_id
            )
            UPDATE {table_name} AS resource
            SET managed_asset_id = candidates.asset_id
            FROM candidates
            JOIN inserted_assets
              ON inserted_assets.asset_id = candidates.asset_id
            WHERE resource.{id_column} = candidates.resource_id
              AND resource.managed_asset_id IS NULL
            RETURNING resource.{id_column}
            """,
            batch_size,
            asset_kind.value,
        )
        return len(rows)

    async def create_policy(
        self,
        policy: AssetAccessPolicy,
        *,
        created_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        if self.prisma is None:
            return policy
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).create_policy(
                    policy,
                    created_by_account_id=created_by_account_id,
                )

        await self.validate_audience_subjects(policy)
        asset = policy.asset
        await self.prisma.query_raw(
            """
            INSERT INTO deltallm_managedasset (
                asset_id,
                asset_kind,
                governance_source,
                owner_account_id,
                created_by_account_id,
                policy_version,
                state,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
            """,
            asset.asset_id,
            asset.asset_kind.value,
            asset.governance_source.value,
            asset.owner_account_id,
            created_by_account_id,
            asset.policy_version,
            asset.state,
        )
        for grant in policy.grants:
            await self._insert_grant(grant, created_by_account_id=created_by_account_id)
        if asset.asset_kind in _RUNTIME_AUTHORIZATION_KINDS:
            await RoutingRuntimeRevisionRepository(self.prisma).bump_revision()
        return policy

    async def get_policy(
        self, asset_id: str, *, for_update: bool = False
    ) -> AssetAccessPolicy | None:
        if self.prisma is None:
            return None
        lock_sql = "FOR UPDATE OF asset" if for_update else ""
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM deltallm_managedasset AS asset
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE asset.asset_id = $1
            {lock_sql}
            """,
            asset_id,
        )
        if not rows:
            return None
        return self._policy_from_rows(rows)

    async def get_accessible_policy(
        self,
        asset_id: str,
        principal: AssetPrincipal,
    ) -> AssetAccessPolicy | None:
        if self.prisma is None:
            return None
        params: list[Any] = [asset_id]
        access_sql = self._access_predicate(params, principal)
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM deltallm_managedasset AS asset
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE asset.asset_id = $1
              AND asset.state = 'active'
              AND ({access_sql})
            """,
            *params,
        )
        if not rows:
            return None
        return self._policy_from_rows(rows)

    async def get_policy_for_resource(
        self,
        asset_kind: AssetKind,
        resource_id: str,
        *,
        principal: AssetPrincipal | None = None,
    ) -> AssetAccessPolicy | None:
        if self.prisma is None:
            return None
        table_name, id_column = _RESOURCE_TABLES[asset_kind]
        params: list[Any] = [resource_id, asset_kind.value]
        access_sql = self._access_predicate(params, principal) if principal is not None else "TRUE"
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM {table_name} AS resource
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = resource.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE resource.{id_column} = $1
              AND asset.asset_kind = $2
              AND asset.state = 'active'
              AND ({access_sql})
            """,
            *params,
        )
        if not rows:
            return None
        return self._policy_from_rows(rows)

    async def accessible_resource_ids(
        self,
        asset_kind: AssetKind,
        resource_ids: set[str],
        principal: AssetPrincipal,
    ) -> set[str]:
        if self.prisma is None or not resource_ids:
            return set()
        table_name, id_column = _RESOURCE_TABLES[asset_kind]
        params: list[Any] = [sorted(resource_ids), asset_kind.value]
        access_sql = self._access_predicate(params, principal)
        rows = await self.prisma.query_raw(
            f"""
            SELECT DISTINCT resource.{id_column} AS resource_id
            FROM {table_name} AS resource
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = resource.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE resource.{id_column} = ANY($1::text[])
              AND asset.asset_kind = $2
              AND asset.state = 'active'
              AND ({access_sql})
            """,
            *params,
        )
        return {str(row.get("resource_id") or "") for row in rows if row.get("resource_id")}

    async def owned_resource_ids(
        self,
        asset_kind: AssetKind,
        resource_ids: set[str],
        account_id: str | None,
    ) -> set[str]:
        """Return the requested active resources owned by one account."""

        if self.prisma is None or not resource_ids or not account_id:
            return set()
        table_name, id_column = _RESOURCE_TABLES[asset_kind]
        rows = await self.prisma.query_raw(
            f"""
            SELECT DISTINCT resource.{id_column} AS resource_id
            FROM {table_name} AS resource
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = resource.managed_asset_id
            WHERE resource.{id_column} = ANY($1::text[])
              AND asset.asset_kind = $2
              AND asset.state = 'active'
              AND asset.owner_account_id = $3
            """,
            sorted(resource_ids),
            asset_kind.value,
            account_id,
        )
        return {str(row.get("resource_id") or "") for row in rows if row.get("resource_id")}

    async def list_accessible_policies(
        self,
        asset_kind: AssetKind,
        principal: AssetPrincipal,
    ) -> list[AssetAccessPolicy]:
        if self.prisma is None:
            return []

        params: list[Any] = [asset_kind.value]
        access_sql = self._access_predicate(params, principal)

        rows = await self.prisma.query_raw(
            f"""
            WITH selected_assets AS (
                SELECT asset.asset_id
                FROM deltallm_managedasset AS asset
                WHERE asset.asset_kind = $1
                  AND asset.state = 'active'
                  AND ({access_sql})
                ORDER BY asset.created_at ASC, asset.asset_id ASC
                LIMIT ${len(params) + 1}
            )
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM deltallm_managedasset AS asset
            JOIN selected_assets ON selected_assets.asset_id = asset.asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            ORDER BY asset.created_at ASC, asset.asset_id ASC
            """,
            *params,
            self.max_snapshot_policies + 1,
        )
        policies = self._policies_from_rows(rows)
        if len(policies) > self.max_snapshot_policies:
            raise ManagedAssetSnapshotLimitError(
                f"managed asset snapshot exceeds the safety limit of {self.max_snapshot_policies}"
            )
        return policies

    async def model_access_index(
        self,
        model_names: set[str],
        principal: AssetPrincipal,
    ) -> tuple[dict[str, AssetAccessPolicy], set[str]]:
        """Return visible policies and creator-owned names for one runtime snapshot.

        Runtime model discovery already produces the candidate names. Restricting the
        policy query to those names avoids loading the global managed-model catalog on
        every list, detail, and health request.
        """

        if self.prisma is None or not model_names:
            return {}, set()

        params: list[Any] = [sorted(model_names)]
        access_sql = self._access_predicate(params, principal)
        platform_visible_sql = "TRUE" if principal.is_platform_admin else "FALSE"
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                model.model_name,
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role,
                (
                  (asset.governance_source = 'platform' AND {platform_visible_sql})
                  OR (asset.governance_source = 'creator' AND ({access_sql}))
                ) AS is_visible
            FROM deltallm_model AS model
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = model.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE model.model_name = ANY($1::text[])
              AND asset.asset_kind = 'model'
              AND asset.state = 'active'
            ORDER BY model.model_name ASC
            """,
            *params,
        )
        creator_names = {
            str(row.get("model_name") or "")
            for row in rows
            if row.get("governance_source") == GovernanceSource.CREATOR.value
        }
        visible: dict[str, AssetAccessPolicy] = {}
        rows_by_model: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("is_visible") is True:
                rows_by_model.setdefault(str(row.get("model_name") or ""), []).append(row)
        for model_name, model_rows in rows_by_model.items():
            visible[model_name] = self._policy_from_rows(model_rows)
        return visible, creator_names

    async def validate_audience_subjects(self, policy: AssetAccessPolicy) -> None:
        """Reject missing grant targets before attempting a policy write."""

        if self.prisma is None:
            return
        team_ids = sorted(
            {
                str(grant.subject_id)
                for grant in policy.grants
                if grant.subject_type is AssetSubjectType.TEAM and grant.subject_id
            }
        )
        organization_ids = sorted(
            {
                str(grant.subject_id)
                for grant in policy.grants
                if grant.subject_type is AssetSubjectType.ORGANIZATION and grant.subject_id
            }
        )
        if not team_ids and not organization_ids:
            return
        rows = await self.prisma.query_raw(
            """
            SELECT 'team' AS subject_type, requested.subject_id
            FROM unnest($1::text[]) AS requested(subject_id)
            WHERE NOT EXISTS (
              SELECT 1
              FROM deltallm_teamtable AS team
              WHERE team.team_id = requested.subject_id
            )
            UNION ALL
            SELECT 'organization' AS subject_type, requested.subject_id
            FROM unnest($2::text[]) AS requested(subject_id)
            WHERE NOT EXISTS (
              SELECT 1
              FROM deltallm_organizationtable AS organization
              WHERE organization.organization_id = requested.subject_id
            )
            ORDER BY subject_type, subject_id
            """,
            team_ids,
            organization_ids,
        )
        if rows:
            first = rows[0]
            subject_type = str(first.get("subject_type") or "audience")
            subject_id = str(first.get("subject_id") or "")
            raise ManagedAssetAudienceNotFoundError(
                f"Selected {subject_type} audience does not exist: {subject_id}"
            )

    async def principal_for_account(self, account_id: str) -> AssetPrincipal:
        if self.prisma is None:
            return AssetPrincipal(account_id=account_id)
        rows = await self.prisma.query_raw(
            """
            SELECT
              ARRAY(
                SELECT membership.team_id
                FROM deltallm_teammembership AS membership
                WHERE membership.account_id = $1
                ORDER BY membership.team_id
              ) AS team_ids,
              ARRAY(
                SELECT membership.organization_id
                FROM deltallm_organizationmembership AS membership
                WHERE membership.account_id = $1
                ORDER BY membership.organization_id
              ) AS organization_ids
            """,
            account_id,
        )
        row = rows[0] if rows else {}
        return AssetPrincipal(
            account_id=account_id,
            team_ids=frozenset(str(item) for item in (row.get("team_ids") or [])),
            organization_ids=frozenset(str(item) for item in (row.get("organization_ids") or [])),
        )

    async def organization_id_for_team(self, team_id: str) -> str | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        value = (rows[0] if rows else {}).get("organization_id")
        return str(value) if value is not None else None

    async def search_audience_options(
        self,
        subject_type: AssetSubjectType,
        principal: AssetPrincipal,
        *,
        search: str = "",
        selected_ids: tuple[str, ...] = (),
        limit: int = 3,
    ) -> list[dict[str, str]]:
        """Return selected labels plus a bounded, principal-scoped search page."""

        if self.prisma is None or subject_type is AssetSubjectType.PUBLIC:
            return []
        selected = sorted({item for item in selected_ids if item})[:100]
        bounded_limit = max(1, min(int(limit), 20))
        normalized_search = search.strip()
        if subject_type is AssetSubjectType.TEAM:
            table_name = "deltallm_teamtable"
            id_column = "team_id"
            label_expression = "COALESCE(team_alias, team_id)"
            allowed_ids = sorted(principal.team_ids)
        else:
            table_name = "deltallm_organizationtable"
            id_column = "organization_id"
            label_expression = "COALESCE(organization_name, organization_id)"
            allowed_ids = sorted(principal.organization_ids)
        rows = await self.prisma.query_raw(
            f"""
            WITH selected AS (
                SELECT {id_column} AS id, {label_expression} AS label, TRUE AS pinned
                FROM {table_name}
                WHERE {id_column} = ANY($1::text[])
                  AND ($4::boolean OR {id_column} = ANY($5::text[]))
            ), matches AS (
                SELECT {id_column} AS id, {label_expression} AS label, FALSE AS pinned
                FROM {table_name}
                WHERE NOT ({id_column} = ANY($1::text[]))
                  AND ($4::boolean OR {id_column} = ANY($5::text[]))
                  AND (
                    $2::text = ''
                    OR {id_column} ILIKE ('%' || $2::text || '%')
                    OR {label_expression} ILIKE ('%' || $2::text || '%')
                  )
                ORDER BY {label_expression} ASC, {id_column} ASC
                LIMIT $3
            )
            SELECT id, label, pinned
            FROM (
                SELECT * FROM selected
                UNION ALL
                SELECT * FROM matches
            ) AS options
            ORDER BY pinned DESC, label ASC, id ASC
            """,
            selected,
            normalized_search,
            bounded_limit,
            principal.is_platform_admin,
            allowed_ids,
        )
        return [
            {"id": str(row.get("id") or ""), "label": str(row.get("label") or row.get("id") or "")}
            for row in rows
            if row.get("id")
        ]

    async def list_credential_policies_for_model(
        self, managed_asset_id: str
    ) -> list[AssetAccessPolicy]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM deltallm_model AS model
            JOIN deltallm_modeldeployment AS deployment ON deployment.model_id = model.model_id
            JOIN deltallm_namedcredential AS credential
              ON credential.credential_id = deployment.named_credential_id
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = credential.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE model.managed_asset_id = $1
              AND asset.state = 'active'
            ORDER BY asset.asset_id
            """,
            managed_asset_id,
        )
        return self._policies_from_rows(rows)

    async def list_credential_binding_policies_for_model(
        self, managed_asset_id: str
    ) -> list[tuple[AssetAccessPolicy, str | None]]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role,
                deployment.credential_binding_mode
            FROM deltallm_model AS model
            JOIN deltallm_modeldeployment AS deployment ON deployment.model_id = model.model_id
            JOIN deltallm_namedcredential AS credential
              ON credential.credential_id = deployment.named_credential_id
            JOIN deltallm_managedasset AS asset
              ON asset.asset_id = credential.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE model.managed_asset_id = $1
              AND asset.state = 'active'
              AND deployment.credential_binding_state = 'active'
            ORDER BY asset.asset_id
            """,
            managed_asset_id,
        )
        modes: dict[str, str | None] = {}
        for row in rows:
            asset_id = str(row.get("asset_id") or "")
            mode = (
                str(row.get("credential_binding_mode"))
                if row.get("credential_binding_mode") is not None
                else None
            )
            if asset_id not in modes or mode == "audience_scoped":
                modes[asset_id] = mode
        return [
            (policy, modes.get(policy.asset.asset_id)) for policy in self._policies_from_rows(rows)
        ]

    async def list_model_policies_for_credential(
        self, credential_asset_id: str
    ) -> list[AssetAccessPolicy]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role
            FROM deltallm_namedcredential AS credential
            JOIN deltallm_modeldeployment AS deployment
              ON deployment.named_credential_id = credential.credential_id
            JOIN deltallm_model AS model ON model.model_id = deployment.model_id
            JOIN deltallm_managedasset AS asset ON asset.asset_id = model.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE credential.managed_asset_id = $1
              AND asset.governance_source = 'creator'
              AND asset.state = 'active'
            ORDER BY asset.asset_id
            """,
            credential_asset_id,
        )
        return self._policies_from_rows(rows)

    async def list_model_binding_policies_for_credential(
        self, credential_asset_id: str
    ) -> list[tuple[AssetAccessPolicy, str | None]]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                asset.asset_id,
                asset.asset_kind,
                asset.governance_source,
                asset.owner_account_id,
                asset.policy_version,
                asset.state,
                asset_grant.subject_type,
                asset_grant.team_id,
                asset_grant.organization_id,
                asset_grant.access_role,
                deployment.credential_binding_mode
            FROM deltallm_namedcredential AS credential
            JOIN deltallm_modeldeployment AS deployment
              ON deployment.named_credential_id = credential.credential_id
            JOIN deltallm_model AS model ON model.model_id = deployment.model_id
            JOIN deltallm_managedasset AS asset ON asset.asset_id = model.managed_asset_id
            LEFT JOIN deltallm_assetgrant AS asset_grant
              ON asset_grant.managed_asset_id = asset.asset_id
            WHERE credential.managed_asset_id = $1
              AND asset.governance_source = 'creator'
              AND asset.state = 'active'
              AND deployment.credential_binding_state = 'active'
            ORDER BY asset.asset_id
            """,
            credential_asset_id,
        )
        modes: dict[str, str | None] = {}
        for row in rows:
            asset_id = str(row.get("asset_id") or "")
            mode = (
                str(row.get("credential_binding_mode"))
                if row.get("credential_binding_mode") is not None
                else None
            )
            if asset_id not in modes or mode == "audience_scoped":
                modes[asset_id] = mode
        return [
            (policy, modes.get(policy.asset.asset_id)) for policy in self._policies_from_rows(rows)
        ]

    async def replace_grants(
        self,
        policy: AssetAccessPolicy,
        *,
        expected_policy_version: int,
        changed_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        if self.prisma is None:
            return AssetAccessPolicy(
                asset=ManagedAsset(
                    asset_id=policy.asset.asset_id,
                    asset_kind=policy.asset.asset_kind,
                    governance_source=policy.asset.governance_source,
                    owner_account_id=policy.asset.owner_account_id,
                    policy_version=expected_policy_version + 1,
                    state=policy.asset.state,
                ),
                grants=policy.grants,
            )
        if self._use_transactions and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).replace_grants(
                    policy,
                    expected_policy_version=expected_policy_version,
                    changed_by_account_id=changed_by_account_id,
                )

        current = await self.get_policy(policy.asset.asset_id, for_update=True)
        if current is None:
            raise ManagedAssetNotFoundError(policy.asset.asset_id)
        if current.asset.policy_version != expected_policy_version:
            raise ManagedAssetPolicyConflictError(
                f"asset policy changed; current version is {current.asset.policy_version}"
            )

        await self.validate_audience_subjects(policy)
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_managedasset
            SET policy_version = policy_version + 1,
                updated_at = NOW()
            WHERE asset_id = $1 AND policy_version = $2
            RETURNING policy_version
            """,
            policy.asset.asset_id,
            expected_policy_version,
        )
        if not rows:
            raise ManagedAssetPolicyConflictError("asset policy changed")

        await self.prisma.query_raw(
            "DELETE FROM deltallm_assetgrant WHERE managed_asset_id = $1",
            policy.asset.asset_id,
        )
        for grant in policy.grants:
            await self._insert_grant(
                grant,
                created_by_account_id=changed_by_account_id,
            )
        if policy.asset.asset_kind in _RUNTIME_AUTHORIZATION_KINDS:
            await RoutingRuntimeRevisionRepository(self.prisma).bump_revision()

        return AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id=policy.asset.asset_id,
                asset_kind=policy.asset.asset_kind,
                governance_source=policy.asset.governance_source,
                owner_account_id=policy.asset.owner_account_id,
                policy_version=int(rows[0]["policy_version"]),
                state=policy.asset.state,
            ),
            grants=policy.grants,
        )

    async def replace_grant(
        self,
        policy: AssetAccessPolicy,
        *,
        expected_policy_version: int,
        changed_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        """Compatibility wrapper for the original single-grant repository API."""

        return await self.replace_grants(
            policy,
            expected_policy_version=expected_policy_version,
            changed_by_account_id=changed_by_account_id,
        )

    async def delete_policy(self, asset_id: str) -> bool:
        if self.prisma is None:
            return True
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_managedasset
            WHERE asset_id = $1
            RETURNING asset_id, asset_kind
            """,
            asset_id,
        )
        if rows:
            try:
                asset_kind = AssetKind(str(rows[0].get("asset_kind") or ""))
            except ValueError:
                asset_kind = None
            if asset_kind in _RUNTIME_AUTHORIZATION_KINDS:
                await RoutingRuntimeRevisionRepository(self.prisma).bump_revision()
        return bool(rows)

    async def _insert_grant(
        self,
        grant: AssetGrant,
        *,
        created_by_account_id: str | None,
    ) -> None:
        if self.prisma is None:
            return
        try:
            await self.prisma.query_raw(
                """
                INSERT INTO deltallm_assetgrant (
                    managed_asset_id,
                    subject_type,
                    team_id,
                    organization_id,
                    access_role,
                    created_by_account_id,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                """,
                grant.managed_asset_id,
                grant.subject_type.value,
                grant.subject_id if grant.subject_type is AssetSubjectType.TEAM else None,
                grant.subject_id if grant.subject_type is AssetSubjectType.ORGANIZATION else None,
                grant.access_role.value,
                created_by_account_id,
            )
        except Exception as exc:
            if _is_foreign_key_violation(exc):
                raise ManagedAssetAudienceNotFoundError(
                    f"Selected {grant.subject_type.value} audience no longer exists"
                ) from exc
            raise

    @staticmethod
    def _append_placeholders(params: list[Any], values: list[str]) -> str:
        placeholders: list[str] = []
        for value in values:
            params.append(value)
            placeholders.append(f"${len(params)}")
        return ", ".join(placeholders)

    @classmethod
    def _access_predicate(cls, params: list[Any], principal: AssetPrincipal) -> str:
        if principal.is_platform_admin:
            return "TRUE"
        grant_predicates: list[str] = ["access_grant.subject_type = 'public'"]
        predicates: list[str] = []
        if principal.account_id:
            params.append(principal.account_id)
            predicates.append(f"asset.owner_account_id = ${len(params)}")
        if principal.team_ids:
            placeholders = cls._append_placeholders(params, sorted(principal.team_ids))
            grant_predicates.append(
                f"(access_grant.subject_type = 'team' AND access_grant.team_id IN ({placeholders}))"
            )
        if principal.organization_ids:
            placeholders = cls._append_placeholders(params, sorted(principal.organization_ids))
            grant_predicates.append(
                "(access_grant.subject_type = 'organization' "
                f"AND access_grant.organization_id IN ({placeholders}))"
            )
        predicates.append(
            "EXISTS (SELECT 1 FROM deltallm_assetgrant AS access_grant "
            "WHERE access_grant.managed_asset_id = asset.asset_id AND ("
            + " OR ".join(grant_predicates)
            + "))"
        )
        return " OR ".join(predicates)

    @classmethod
    def _policies_from_rows(cls, rows: list[dict[str, Any]]) -> list[AssetAccessPolicy]:
        rows_by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rows_by_asset.setdefault(str(row.get("asset_id") or ""), []).append(row)
        return [cls._policy_from_rows(asset_rows) for asset_rows in rows_by_asset.values()]

    @staticmethod
    def _policy_from_rows(rows: list[dict[str, Any]]) -> AssetAccessPolicy:
        if not rows:
            raise ValueError("policy rows are required")
        row = rows[0]
        asset = ManagedAsset(
            asset_id=str(row.get("asset_id") or ""),
            asset_kind=AssetKind(str(row.get("asset_kind") or "")),
            governance_source=GovernanceSource(str(row.get("governance_source") or "")),
            owner_account_id=str(row.get("owner_account_id"))
            if row.get("owner_account_id") is not None
            else None,
            policy_version=int(row.get("policy_version") or 1),
            state=str(row.get("state") or "active"),
        )
        grants: list[AssetGrant] = []
        seen_subjects: set[tuple[AssetSubjectType, str | None]] = set()
        for grant_row in rows:
            subject_type_raw = grant_row.get("subject_type")
            if subject_type_raw is None:
                continue
            subject_type = AssetSubjectType(str(subject_type_raw))
            subject_id = None
            if subject_type is AssetSubjectType.TEAM:
                subject_id = str(grant_row.get("team_id") or "") or None
            elif subject_type is AssetSubjectType.ORGANIZATION:
                subject_id = str(grant_row.get("organization_id") or "") or None
            subject = (subject_type, subject_id)
            if subject in seen_subjects:
                continue
            seen_subjects.add(subject)
            grants.append(
                AssetGrant(
                    managed_asset_id=asset.asset_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    access_role=AssetAccessRole(str(grant_row.get("access_role") or "")),
                )
            )
        subject_order = {
            AssetSubjectType.TEAM: 0,
            AssetSubjectType.ORGANIZATION: 1,
            AssetSubjectType.PUBLIC: 2,
        }
        grants.sort(key=lambda grant: (subject_order[grant.subject_type], grant.subject_id or ""))
        return AssetAccessPolicy(asset=asset, grants=tuple(grants))

    @classmethod
    def _policy_from_row(cls, row: dict[str, Any]) -> AssetAccessPolicy:
        """Compatibility helper for repository tests built around one result row."""

        return cls._policy_from_rows([row])
