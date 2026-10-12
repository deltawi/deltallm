from __future__ import annotations

import json

from src.db.identity.external.external_auth_records import (
    ExternalBindingRecord,
    ExternalBindingTeamRecord,
    ExternalSubjectTeamRecord,
)
from src.db.identity.platform_accounts import PlatformAccountDatabase


class ExternalTeamRepository:
    """Persist additional teams of a binding and the subject grants on them.

    The binding's own team is the implicit default team and has no row here.
    """

    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def lock_attachment(
        self, binding_id: str, team_id: str
    ) -> ExternalBindingTeamRecord | None:
        rows = await self.db.query_raw(
            """
            SELECT * FROM deltallm_externalauthbindingteam
            WHERE binding_id = $1 AND team_id = $2 FOR UPDATE
            """,
            binding_id,
            team_id,
        )
        return ExternalBindingTeamRecord.model_validate(rows[0]) if rows else None

    async def attach(
        self, binding_id: str, team_id: str, *, version: int
    ) -> ExternalBindingTeamRecord | None:
        rows = await self.db.query_raw(
            """
            INSERT INTO deltallm_externalauthbindingteam (binding_id, team_id, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (binding_id, team_id) DO UPDATE SET state = 'active',
                version = deltallm_externalauthbindingteam.version + 1, updated_at = NOW()
            WHERE deltallm_externalauthbindingteam.state = 'detached'
              AND deltallm_externalauthbindingteam.version = $3
            RETURNING *
            """,
            binding_id,
            team_id,
            version,
        )
        return ExternalBindingTeamRecord.model_validate(rows[0]) if rows else None

    async def detach(
        self, attachment: ExternalBindingTeamRecord, *, version: int
    ) -> ExternalBindingTeamRecord | None:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthbindingteam SET state = 'detached',
                version = version + 1, updated_at = NOW()
            WHERE binding_id = $1 AND team_id = $2 AND state = 'active' AND version = $3
            RETURNING *
            """,
            attachment.binding_id,
            attachment.team_id,
            version,
        )
        return ExternalBindingTeamRecord.model_validate(rows[0]) if rows else None

    async def list_attachments(
        self, binding_id: str, *, after: str | None, limit: int
    ) -> list[ExternalBindingTeamRecord]:
        rows = await self.db.query_raw(
            """
            SELECT * FROM deltallm_externalauthbindingteam
            WHERE binding_id = $1 AND ($2::text IS NULL OR team_id > $2)
            ORDER BY team_id LIMIT $3
            """,
            binding_id,
            after,
            min(max(limit, 1), 101),
        )
        return [ExternalBindingTeamRecord.model_validate(row) for row in rows]

    async def lock_grant(self, subject_id: str, team_id: str) -> ExternalSubjectTeamRecord | None:
        rows = await self.db.query_raw(
            """
            SELECT * FROM deltallm_externalauthsubjectteam
            WHERE subject_id = $1 AND team_id = $2 FOR UPDATE
            """,
            subject_id,
            team_id,
        )
        return ExternalSubjectTeamRecord.model_validate(rows[0]) if rows else None

    async def lock_usable_grant(
        self, subject_id: str, binding: ExternalBindingRecord, team_id: str
    ) -> ExternalSubjectTeamRecord | None:
        """Lock an active grant whose attachment and team still belong to the binding."""

        rows = await self.db.query_raw(
            """
            SELECT g.* FROM deltallm_externalauthsubjectteam g
            JOIN deltallm_externalauthbindingteam bt ON bt.binding_id = g.binding_id AND bt.team_id = g.team_id
            JOIN deltallm_teamtable t ON t.team_id = g.team_id
            JOIN deltallm_organizationtable o ON o.organization_id = t.organization_id
            WHERE g.subject_id = $1 AND g.binding_id = $2 AND g.team_id = $3 AND g.team_id <> $4
              AND g.state = 'active' AND bt.state = 'active'
              AND t.organization_id = $5 AND o.lifecycle_state = 'active'
            FOR UPDATE OF g FOR SHARE OF bt, t, o
            """,
            subject_id,
            binding.binding_id,
            team_id,
            binding.team_id,
            binding.organization_id,
        )
        return ExternalSubjectTeamRecord.model_validate(rows[0]) if rows else None

    async def grant(
        self, *, subject_id: str, binding_id: str, team_id: str, version: int
    ) -> ExternalSubjectTeamRecord | None:
        rows = await self.db.query_raw(
            """
            INSERT INTO deltallm_externalauthsubjectteam (subject_id, binding_id, team_id, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (subject_id, team_id) DO UPDATE SET state = 'active',
                version = deltallm_externalauthsubjectteam.version + 1, updated_at = NOW()
            WHERE deltallm_externalauthsubjectteam.state = 'removed'
              AND deltallm_externalauthsubjectteam.version = $4
            RETURNING *
            """,
            subject_id,
            binding_id,
            team_id,
            version,
        )
        return ExternalSubjectTeamRecord.model_validate(rows[0]) if rows else None

    async def restore_access(self, grant: ExternalSubjectTeamRecord) -> None:
        """Undo a removal for a grant whose runtime user already exists."""

        await self.db.execute_raw(
            """
            WITH unblocked AS (
                UPDATE deltallm_usertable SET blocked = false, updated_at = NOW()
                WHERE user_id = $3 AND team_id = $2 RETURNING user_id
            ) INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, updated_at)
            SELECT gen_random_uuid(), s.account_id, $2, 'team_developer', NOW()
            FROM deltallm_externalauthsubject s CROSS JOIN (SELECT count(*) FROM unblocked) AS restored
            WHERE s.subject_id = $1 AND s.account_id IS NOT NULL
            ON CONFLICT (account_id, team_id) DO NOTHING
            """,
            grant.subject_id,
            grant.team_id,
            grant.runtime_user_id,
        )

    async def provision_runtime(
        self, grant: ExternalSubjectTeamRecord, *, account_id: str
    ) -> ExternalSubjectTeamRecord | None:
        """Create the grant's runtime user and team membership on first use.

        The runtime user has no email: `deltallm_usertable.user_email` is unique and
        the default team's runtime user already holds the account email.
        """

        rows = await self.db.query_raw(
            """
            WITH runtime AS (
                INSERT INTO deltallm_usertable (user_id, user_role, team_id, models, updated_at)
                SELECT gen_random_uuid()::text, 'internal_user', $2, ARRAY[]::text[], NOW()
                WHERE EXISTS (
                    SELECT 1 FROM deltallm_externalauthsubjectteam
                    WHERE subject_id = $1 AND team_id = $2 AND runtime_user_id IS NULL)
                RETURNING user_id
            ), membership AS (
                INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, updated_at)
                SELECT gen_random_uuid(), $3, $2, 'team_developer', NOW() FROM runtime
                ON CONFLICT (account_id, team_id) DO NOTHING RETURNING membership_id
            ) UPDATE deltallm_externalauthsubjectteam g SET runtime_user_id = r.user_id, updated_at = NOW()
            FROM runtime r, (SELECT count(*) FROM membership) AS added
            WHERE g.subject_id = $1 AND g.team_id = $2 AND g.runtime_user_id IS NULL
            RETURNING g.*
            """,
            grant.subject_id,
            grant.team_id,
            account_id,
        )
        return ExternalSubjectTeamRecord.model_validate(rows[0]) if rows else None

    async def withdraw(
        self,
        *,
        binding_id: str,
        team_id: str,
        subject_id: str | None = None,
        version: int | None = None,
    ) -> list[ExternalSubjectTeamRecord]:
        """Remove one grant, or every active grant on a team, in one statement.

        Removal bumps the grant epoch, deletes the team membership, blocks the
        team's runtime user so its keys fail billing admission, and revokes the
        team's child sessions. Keys stay in place so a new grant can restore them.
        """

        rows = await self.db.query_raw(
            """
            WITH removed AS (
                UPDATE deltallm_externalauthsubjectteam g SET state = 'removed', epoch = epoch + 1,
                    version = version + 1, updated_at = NOW()
                WHERE g.binding_id = $1 AND g.team_id = $2 AND g.state = 'active'
                  AND ($3::text IS NULL OR (g.subject_id = $3 AND g.version = $4))
                RETURNING g.*
            ), memberships AS (
                DELETE FROM deltallm_teammembership m
                USING removed r JOIN deltallm_externalauthsubject s ON s.subject_id = r.subject_id
                WHERE m.account_id = s.account_id AND m.team_id = r.team_id
                RETURNING m.membership_id
            ), blocked AS (
                UPDATE deltallm_usertable u SET blocked = true, updated_at = NOW()
                FROM removed r WHERE u.user_id = r.runtime_user_id
                RETURNING u.user_id
            ), revoked AS (
                UPDATE deltallm_platformsession c SET revoked_at = NOW(), updated_at = NOW()
                FROM deltallm_externalauthparentsession p, removed r
                WHERE c.revoked_at IS NULL AND c.external_parent_id = p.parent_id
                  AND p.subject_id = r.subject_id AND c.external_team_id = r.team_id
                RETURNING c.session_id
            ) SELECT removed.* FROM removed,
                (SELECT count(*) FROM memberships) AS deleted_memberships,
                (SELECT count(*) FROM blocked) AS blocked_users,
                (SELECT count(*) FROM revoked) AS revoked_sessions
            """,
            binding_id,
            team_id,
            subject_id,
            version,
        )
        return [ExternalSubjectTeamRecord.model_validate(row) for row in rows]

    async def list_subjects(
        self, binding_id: str, *, team_id: str | None, after: str | None, limit: int
    ) -> list[dict[str, object]]:
        rows = await self.db.query_raw(
            """
            SELECT s.subject_id, s.identity_issuer, s.subject, s.state, s.version, s.account_id,
                s.runtime_user_id, COALESCE((
                    SELECT jsonb_agg(to_jsonb(g) ORDER BY g.team_id)
                    FROM deltallm_externalauthsubjectteam g WHERE g.subject_id = s.subject_id
                ), '[]'::jsonb) AS team_grants
            FROM deltallm_externalauthsubject s
            JOIN deltallm_externalauthbinding b ON b.binding_id = s.binding_id
            WHERE s.binding_id = $1 AND ($3::text IS NULL OR s.subject_id > $3)
              AND ($2::text IS NULL OR $2 = b.team_id OR EXISTS (
                SELECT 1 FROM deltallm_externalauthsubjectteam filtered
                WHERE filtered.subject_id = s.subject_id AND filtered.team_id = $2))
            ORDER BY s.subject_id LIMIT $4
            """,
            binding_id,
            team_id,
            after,
            min(max(limit, 1), 101),
        )
        subjects = []
        for row in rows:
            item = dict(row)
            grants = item["team_grants"]
            item["team_grants"] = json.loads(grants) if isinstance(grants, str) else grants
            subjects.append(item)
        return subjects
