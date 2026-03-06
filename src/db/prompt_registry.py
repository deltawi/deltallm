from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_json_object(value: Any) -> dict[str, Any]:
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


@dataclass
class PromptTemplateRecord:
    prompt_template_id: str
    template_key: str
    name: str
    description: str | None = None
    owner_scope: str | None = None
    metadata: dict[str, Any] | None = None
    version_count: int = 0
    label_count: int = 0
    binding_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PromptVersionRecord:
    prompt_version_id: str
    prompt_template_id: str
    template_key: str
    version: int
    status: str
    template_body: dict[str, Any]
    variables_schema: dict[str, Any] | None = None
    model_hints: dict[str, Any] | None = None
    route_preferences: dict[str, Any] | None = None
    published_at: datetime | None = None
    published_by: str | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PromptLabelRecord:
    prompt_label_id: str
    prompt_template_id: str
    template_key: str
    label: str
    prompt_version_id: str
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PromptBindingRecord:
    prompt_binding_id: str
    scope_type: str
    scope_id: str
    prompt_template_id: str
    template_key: str
    label: str
    priority: int
    enabled: bool
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PromptResolvedRecord:
    prompt_template_id: str
    template_key: str
    prompt_version_id: str
    version: int
    status: str
    label: str | None
    template_body: dict[str, Any]
    variables_schema: dict[str, Any] | None = None
    model_hints: dict[str, Any] | None = None
    route_preferences: dict[str, Any] | None = None


class PromptRegistryRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_templates(
        self,
        *,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[PromptTemplateRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            clauses.append(
                f"(t.template_key ILIKE ${len(params)} OR t.name ILIKE ${len(params)} OR COALESCE(t.description, '') ILIKE ${len(params)})"
            )

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_prompttemplate t {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        params.extend([limit, offset])
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                t.prompt_template_id,
                t.template_key,
                t.name,
                t.description,
                t.owner_scope,
                t.metadata,
                t.created_at,
                t.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptversion v
                    WHERE v.prompt_template_id = t.prompt_template_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptlabel l
                    WHERE l.prompt_template_id = t.prompt_template_id
                ) AS label_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptbinding b
                    WHERE b.prompt_template_id = t.prompt_template_id
                ) AS binding_count
            FROM deltallm_prompttemplate t
            {where_sql}
            ORDER BY t.created_at DESC, t.template_key ASC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [self._to_template_record(row) for row in rows], total

    async def get_template(self, template_key: str) -> PromptTemplateRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                t.prompt_template_id,
                t.template_key,
                t.name,
                t.description,
                t.owner_scope,
                t.metadata,
                t.created_at,
                t.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptversion v
                    WHERE v.prompt_template_id = t.prompt_template_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptlabel l
                    WHERE l.prompt_template_id = t.prompt_template_id
                ) AS label_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptbinding b
                    WHERE b.prompt_template_id = t.prompt_template_id
                ) AS binding_count
            FROM deltallm_prompttemplate t
            WHERE t.template_key = $1
            LIMIT 1
            """,
            template_key,
        )
        if not rows:
            return None
        return self._to_template_record(rows[0])

    async def create_template(
        self,
        *,
        template_key: str,
        name: str,
        description: str | None,
        owner_scope: str | None,
        metadata: dict[str, Any] | None,
    ) -> PromptTemplateRecord:
        if self.prisma is None:
            return PromptTemplateRecord(
                prompt_template_id="",
                template_key=template_key,
                name=name,
                description=description,
                owner_scope=owner_scope,
                metadata=metadata,
            )
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_prompttemplate (
                prompt_template_id, template_key, name, description, owner_scope, metadata, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::jsonb, NOW(), NOW())
            RETURNING prompt_template_id, template_key, name, description, owner_scope, metadata, created_at, updated_at,
                0::int AS version_count, 0::int AS label_count, 0::int AS binding_count
            """,
            template_key,
            name,
            description,
            owner_scope,
            json.dumps(metadata) if metadata is not None else None,
        )
        return self._to_template_record(rows[0])

    async def update_template(
        self,
        template_key: str,
        *,
        name: str,
        description: str | None,
        owner_scope: str | None,
        metadata: dict[str, Any] | None,
    ) -> PromptTemplateRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_prompttemplate
            SET name = $2,
                description = $3,
                owner_scope = $4,
                metadata = $5::jsonb,
                updated_at = NOW()
            WHERE template_key = $1
            RETURNING prompt_template_id, template_key, name, description, owner_scope, metadata, created_at, updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptversion v
                    WHERE v.prompt_template_id = deltallm_prompttemplate.prompt_template_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptlabel l
                    WHERE l.prompt_template_id = deltallm_prompttemplate.prompt_template_id
                ) AS label_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_promptbinding b
                    WHERE b.prompt_template_id = deltallm_prompttemplate.prompt_template_id
                ) AS binding_count
            """,
            template_key,
            name,
            description,
            owner_scope,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        return self._to_template_record(rows[0])

    async def delete_template(self, template_key: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_prompttemplate
            WHERE template_key = $1
            RETURNING prompt_template_id
            """,
            template_key,
        )
        return bool(rows)

    async def list_versions(self, template_key: str) -> list[PromptVersionRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                v.prompt_version_id,
                v.prompt_template_id,
                t.template_key,
                v.version,
                v.status,
                v.template_body,
                v.variables_schema,
                v.model_hints,
                v.route_preferences,
                v.published_at,
                v.published_by,
                v.archived_at,
                v.created_at,
                v.updated_at
            FROM deltallm_promptversion v
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = v.prompt_template_id
            WHERE t.template_key = $1
            ORDER BY v.version DESC
            """,
            template_key,
        )
        return [self._to_version_record(row) for row in rows]

    async def create_version(
        self,
        template_key: str,
        *,
        template_body: dict[str, Any],
        variables_schema: dict[str, Any] | None,
        model_hints: dict[str, Any] | None,
        route_preferences: dict[str, Any] | None,
        status: str = "draft",
    ) -> PromptVersionRecord | None:
        if self.prisma is None:
            return None
        template_id = await self._resolve_template_id(template_key)
        if template_id is None:
            return None
        version_rows = await self.prisma.query_raw(
            """
            SELECT COALESCE(MAX(version), 0)::int AS max_version
            FROM deltallm_promptversion
            WHERE prompt_template_id = $1
            """,
            template_id,
        )
        next_version = int((version_rows[0] if version_rows else {}).get("max_version") or 0) + 1
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_promptversion (
                prompt_version_id, prompt_template_id, version, status, template_body, variables_schema, model_hints, route_preferences, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, NOW(), NOW())
            RETURNING
                prompt_version_id,
                prompt_template_id,
                $8::text AS template_key,
                version,
                status,
                template_body,
                variables_schema,
                model_hints,
                route_preferences,
                published_at,
                published_by,
                archived_at,
                created_at,
                updated_at
            """,
            template_id,
            next_version,
            status,
            json.dumps(template_body),
            json.dumps(variables_schema) if variables_schema is not None else None,
            json.dumps(model_hints) if model_hints is not None else None,
            json.dumps(route_preferences) if route_preferences is not None else None,
            template_key,
        )
        return self._to_version_record(rows[0]) if rows else None

    async def publish_version(
        self,
        template_key: str,
        *,
        version: int,
        published_by: str | None = None,
    ) -> PromptVersionRecord | None:
        if self.prisma is None:
            return None
        template_id = await self._resolve_template_id(template_key)
        if template_id is None:
            return None

        target_rows = await self.prisma.query_raw(
            """
            SELECT prompt_version_id
            FROM deltallm_promptversion
            WHERE prompt_template_id = $1
              AND version = $2
            LIMIT 1
            """,
            template_id,
            version,
        )
        if not target_rows:
            return None
        target_id = str(target_rows[0].get("prompt_version_id") or "")
        if not target_id:
            return None

        await self.prisma.execute_raw(
            """
            UPDATE deltallm_promptversion
            SET status = 'archived',
                archived_at = NOW(),
                updated_at = NOW()
            WHERE prompt_template_id = $1
              AND status = 'published'
            """,
            template_id,
        )

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_promptversion
            SET status = 'published',
                published_at = NOW(),
                published_by = $2,
                archived_at = NULL,
                updated_at = NOW()
            WHERE prompt_version_id = $1
            RETURNING
                prompt_version_id,
                prompt_template_id,
                $3::text AS template_key,
                version,
                status,
                template_body,
                variables_schema,
                model_hints,
                route_preferences,
                published_at,
                published_by,
                archived_at,
                created_at,
                updated_at
            """,
            target_id,
            published_by,
            template_key,
        )
        return self._to_version_record(rows[0]) if rows else None

    async def list_labels(self, template_key: str) -> list[PromptLabelRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                l.prompt_label_id,
                l.prompt_template_id,
                t.template_key,
                l.label,
                l.prompt_version_id,
                v.version,
                l.created_at,
                l.updated_at
            FROM deltallm_promptlabel l
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = l.prompt_template_id
            JOIN deltallm_promptversion v ON v.prompt_version_id = l.prompt_version_id
            WHERE t.template_key = $1
            ORDER BY l.label ASC
            """,
            template_key,
        )
        return [self._to_label_record(row) for row in rows]

    async def assign_label(
        self,
        template_key: str,
        *,
        label: str,
        version: int,
    ) -> PromptLabelRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH target AS (
                SELECT t.prompt_template_id, t.template_key, v.prompt_version_id, v.version
                FROM deltallm_prompttemplate t
                JOIN deltallm_promptversion v ON v.prompt_template_id = t.prompt_template_id
                WHERE t.template_key = $1
                  AND v.version = $2
                LIMIT 1
            )
            INSERT INTO deltallm_promptlabel (
                prompt_label_id, prompt_template_id, label, prompt_version_id, created_at, updated_at
            )
            SELECT gen_random_uuid()::text, target.prompt_template_id, $3, target.prompt_version_id, NOW(), NOW()
            FROM target
            ON CONFLICT (prompt_template_id, label)
            DO UPDATE SET
                prompt_version_id = EXCLUDED.prompt_version_id,
                updated_at = NOW()
            RETURNING prompt_label_id, prompt_template_id, label, prompt_version_id, created_at, updated_at
            """,
            template_key,
            version,
            label,
        )
        if not rows:
            return None
        row = rows[0]
        version_rows = await self.prisma.query_raw(
            """
            SELECT v.version
            FROM deltallm_promptversion v
            WHERE v.prompt_version_id = $1
            LIMIT 1
            """,
            row.get("prompt_version_id"),
        )
        version_value = int((version_rows[0] if version_rows else {}).get("version") or 0)
        return self._to_label_record(
            {
                **row,
                "template_key": template_key,
                "version": version_value,
            }
        )

    async def delete_label(self, template_key: str, label: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_promptlabel l
            USING deltallm_prompttemplate t
            WHERE t.prompt_template_id = l.prompt_template_id
              AND t.template_key = $1
              AND l.label = $2
            RETURNING l.prompt_label_id
            """,
            template_key,
            label,
        )
        return bool(rows)

    async def list_bindings(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        template_key: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[PromptBindingRecord], int]:
        if self.prisma is None:
            return [], 0
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type:
            params.append(scope_type)
            clauses.append(f"b.scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"b.scope_id = ${len(params)}")
        if template_key:
            params.append(template_key)
            clauses.append(f"t.template_key = ${len(params)}")
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM deltallm_promptbinding b
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = b.prompt_template_id
            {where_sql}
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        params.extend([limit, offset])
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                b.prompt_binding_id,
                b.scope_type,
                b.scope_id,
                b.prompt_template_id,
                t.template_key,
                b.label,
                b.priority,
                b.enabled,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_promptbinding b
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = b.prompt_template_id
            {where_sql}
            ORDER BY b.scope_type ASC, b.scope_id ASC, b.priority ASC, b.created_at ASC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [self._to_binding_record(row) for row in rows], total

    async def upsert_binding(
        self,
        *,
        scope_type: str,
        scope_id: str,
        template_key: str,
        label: str,
        priority: int,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> PromptBindingRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH target AS (
                SELECT prompt_template_id, template_key
                FROM deltallm_prompttemplate
                WHERE template_key = $3
                LIMIT 1
            )
            INSERT INTO deltallm_promptbinding (
                prompt_binding_id, scope_type, scope_id, prompt_template_id, label, priority, enabled, metadata, created_at, updated_at
            )
            SELECT gen_random_uuid()::text, $1, $2, target.prompt_template_id, $4, $5, $6, $7::jsonb, NOW(), NOW()
            FROM target
            ON CONFLICT (scope_type, scope_id, prompt_template_id, label)
            DO UPDATE SET
                priority = EXCLUDED.priority,
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING prompt_binding_id, scope_type, scope_id, prompt_template_id, label, priority, enabled, metadata, created_at, updated_at
            """,
            scope_type,
            scope_id,
            template_key,
            label,
            priority,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        row = rows[0]
        return self._to_binding_record({**row, "template_key": template_key})

    async def delete_binding(self, binding_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_promptbinding
            WHERE prompt_binding_id = $1
            RETURNING prompt_binding_id
            """,
            binding_id,
        )
        return bool(rows)

    async def resolve_binding(self, *, scope_type: str, scope_id: str) -> PromptBindingRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                b.prompt_binding_id,
                b.scope_type,
                b.scope_id,
                b.prompt_template_id,
                t.template_key,
                b.label,
                b.priority,
                b.enabled,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_promptbinding b
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = b.prompt_template_id
            WHERE b.scope_type = $1
              AND b.scope_id = $2
              AND b.enabled = TRUE
            ORDER BY b.priority ASC, b.created_at ASC
            LIMIT 1
            """,
            scope_type,
            scope_id,
        )
        if not rows:
            return None
        return self._to_binding_record(rows[0])

    async def resolve_prompt(
        self,
        *,
        template_key: str,
        label: str | None = None,
        version: int | None = None,
    ) -> PromptResolvedRecord | None:
        if self.prisma is None:
            return None
        if version is not None:
            rows = await self.prisma.query_raw(
                """
                SELECT
                    t.prompt_template_id,
                    t.template_key,
                    v.prompt_version_id,
                    v.version,
                    v.status,
                    NULL::text AS label,
                    v.template_body,
                    v.variables_schema,
                    v.model_hints,
                    v.route_preferences
                FROM deltallm_prompttemplate t
                JOIN deltallm_promptversion v ON v.prompt_template_id = t.prompt_template_id
                WHERE t.template_key = $1
                  AND v.version = $2
                LIMIT 1
                """,
                template_key,
                version,
            )
        else:
            target_label = label or "production"
            rows = await self.prisma.query_raw(
                """
                SELECT
                    t.prompt_template_id,
                    t.template_key,
                    v.prompt_version_id,
                    v.version,
                    v.status,
                    l.label,
                    v.template_body,
                    v.variables_schema,
                    v.model_hints,
                    v.route_preferences
                FROM deltallm_prompttemplate t
                JOIN deltallm_promptlabel l ON l.prompt_template_id = t.prompt_template_id
                JOIN deltallm_promptversion v ON v.prompt_version_id = l.prompt_version_id
                WHERE t.template_key = $1
                  AND l.label = $2
                LIMIT 1
                """,
                template_key,
                target_label,
            )
        if not rows:
            return None
        row = rows[0]
        return PromptResolvedRecord(
            prompt_template_id=str(row.get("prompt_template_id") or ""),
            template_key=str(row.get("template_key") or ""),
            prompt_version_id=str(row.get("prompt_version_id") or ""),
            version=int(row.get("version") or 0),
            status=str(row.get("status") or ""),
            label=str(row.get("label")) if row.get("label") is not None else None,
            template_body=_parse_json_object(row.get("template_body")),
            variables_schema=_parse_json_object(row.get("variables_schema")) if row.get("variables_schema") is not None else None,
            model_hints=_parse_json_object(row.get("model_hints")) if row.get("model_hints") is not None else None,
            route_preferences=_parse_json_object(row.get("route_preferences")) if row.get("route_preferences") is not None else None,
        )

    async def list_binding_scopes_for_template(self, template_key: str) -> list[tuple[str, str]]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT b.scope_type, b.scope_id
            FROM deltallm_promptbinding b
            JOIN deltallm_prompttemplate t ON t.prompt_template_id = b.prompt_template_id
            WHERE t.template_key = $1
            GROUP BY b.scope_type, b.scope_id
            """,
            template_key,
        )
        out: list[tuple[str, str]] = []
        for row in rows:
            scope_type = str(row.get("scope_type") or "").strip()
            scope_id = str(row.get("scope_id") or "").strip()
            if scope_type and scope_id:
                out.append((scope_type, scope_id))
        return out

    async def create_render_log(
        self,
        *,
        request_id: str | None,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
        model: str | None,
        prompt_template_id: str | None,
        prompt_version_id: str | None,
        prompt_key: str | None,
        label: str | None,
        status: str,
        latency_ms: int | None,
        error_code: str | None,
        error_message: str | None,
        variables: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_promptrenderlog (
                prompt_render_log_id,
                request_id,
                api_key,
                user_id,
                team_id,
                organization_id,
                route_group_key,
                model,
                prompt_template_id,
                prompt_version_id,
                prompt_key,
                label,
                status,
                latency_ms,
                error_code,
                error_message,
                variables,
                metadata,
                created_at
            ) VALUES (
                gen_random_uuid()::text,
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17::jsonb,NOW()
            )
            """,
            request_id,
            api_key,
            user_id,
            team_id,
            organization_id,
            route_group_key,
            model,
            prompt_template_id,
            prompt_version_id,
            prompt_key,
            label,
            status,
            latency_ms,
            error_code,
            error_message,
            json.dumps(variables) if variables is not None else None,
            json.dumps(metadata) if metadata is not None else None,
        )

    async def _resolve_template_id(self, template_key: str) -> str | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT prompt_template_id
            FROM deltallm_prompttemplate
            WHERE template_key = $1
            LIMIT 1
            """,
            template_key,
        )
        if not rows:
            return None
        value = str(rows[0].get("prompt_template_id") or "").strip()
        return value or None

    def _to_template_record(self, row: dict[str, Any]) -> PromptTemplateRecord:
        return PromptTemplateRecord(
            prompt_template_id=str(row.get("prompt_template_id") or ""),
            template_key=str(row.get("template_key") or ""),
            name=str(row.get("name") or ""),
            description=str(row.get("description")) if row.get("description") is not None else None,
            owner_scope=str(row.get("owner_scope")) if row.get("owner_scope") is not None else None,
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            version_count=int(row.get("version_count") or 0),
            label_count=int(row.get("label_count") or 0),
            binding_count=int(row.get("binding_count") or 0),
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    def _to_version_record(self, row: dict[str, Any]) -> PromptVersionRecord:
        return PromptVersionRecord(
            prompt_version_id=str(row.get("prompt_version_id") or ""),
            prompt_template_id=str(row.get("prompt_template_id") or ""),
            template_key=str(row.get("template_key") or ""),
            version=int(row.get("version") or 0),
            status=str(row.get("status") or ""),
            template_body=_parse_json_object(row.get("template_body")),
            variables_schema=_parse_json_object(row.get("variables_schema")) if row.get("variables_schema") is not None else None,
            model_hints=_parse_json_object(row.get("model_hints")) if row.get("model_hints") is not None else None,
            route_preferences=_parse_json_object(row.get("route_preferences")) if row.get("route_preferences") is not None else None,
            published_at=_parse_datetime(row.get("published_at")),
            published_by=str(row.get("published_by")) if row.get("published_by") is not None else None,
            archived_at=_parse_datetime(row.get("archived_at")),
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    def _to_label_record(self, row: dict[str, Any]) -> PromptLabelRecord:
        return PromptLabelRecord(
            prompt_label_id=str(row.get("prompt_label_id") or ""),
            prompt_template_id=str(row.get("prompt_template_id") or ""),
            template_key=str(row.get("template_key") or ""),
            label=str(row.get("label") or ""),
            prompt_version_id=str(row.get("prompt_version_id") or ""),
            version=int(row.get("version") or 0),
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    def _to_binding_record(self, row: dict[str, Any]) -> PromptBindingRecord:
        return PromptBindingRecord(
            prompt_binding_id=str(row.get("prompt_binding_id") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            prompt_template_id=str(row.get("prompt_template_id") or ""),
            template_key=str(row.get("template_key") or ""),
            label=str(row.get("label") or ""),
            priority=int(row.get("priority") or 0),
            enabled=bool(row.get("enabled", True)),
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
