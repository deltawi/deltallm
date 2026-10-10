from __future__ import annotations

import hashlib

from src.auth.external_contracts import VerifiedExternalAssertion
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.db.identity.external.external_auth_records import ExternalSubjectRecord


class ExternalSubjectRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def get(self, subject_id: str, *, lock: bool = False) -> ExternalSubjectRecord | None:
        lock_mode = "FOR UPDATE" if lock else ""
        rows = await self.db.query_raw(
            f"SELECT * FROM deltallm_externalauthsubject WHERE subject_id = $1 {lock_mode}",
            subject_id,
        )
        return ExternalSubjectRecord.model_validate(rows[0]) if rows else None

    async def lock_identity(
        self, assertion: VerifiedExternalAssertion
    ) -> ExternalSubjectRecord | None:
        canonical = assertion.claims.identity_issuer + "\x00" + assertion.claims.sub
        # The read needs a new snapshot after the lock wait. One combined query
        # could miss a subject committed by the previous lock owner.
        await self.db.query_raw(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))::text AS locked",
            "external-subject:" + hashlib.sha256(canonical.encode()).hexdigest(),
        )
        rows = await self.db.query_raw(
            """
            SELECT * FROM deltallm_externalauthsubject
            WHERE identity_issuer = $1 AND subject = $2 FOR UPDATE
            """,
            assertion.claims.identity_issuer,
            assertion.claims.sub,
        )
        return ExternalSubjectRecord.model_validate(rows[0]) if rows else None

    async def create_shell(
        self, assertion: VerifiedExternalAssertion, *, state: str = "pending"
    ) -> ExternalSubjectRecord:
        claims = assertion.claims
        rows = await self.db.query_raw(
            """
            INSERT INTO deltallm_externalauthsubject (
                subject_id, integration_id, binding_id, identity_issuer, subject, state, updated_at)
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, NOW()) RETURNING *
            """,
            assertion.integration_id,
            claims.binding_id,
            claims.identity_issuer,
            claims.sub,
            state,
        )
        return ExternalSubjectRecord.model_validate(rows[0])

    async def activate(
        self,
        subject: ExternalSubjectRecord,
        *,
        account_id: str,
        identity_id: str,
        runtime_user_id: str,
    ) -> ExternalSubjectRecord:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthsubject SET account_id = $2, identity_id = $3,
                runtime_user_id = $4, state = 'active', version = version + 1, updated_at = NOW()
            WHERE subject_id = $1 AND state = 'pending' RETURNING *
            """,
            subject.subject_id,
            account_id,
            identity_id,
            runtime_user_id,
        )
        if not rows:
            raise ValueError("External subject is no longer pending")
        return ExternalSubjectRecord.model_validate(rows[0])

    async def set_state(
        self, subject_id: str, *, state: str, version: int | None = None
    ) -> ExternalSubjectRecord | None:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthsubject SET state = $2, epoch = epoch + 1,
                version = version + 1, updated_at = NOW()
            WHERE subject_id = $1 AND ($3::int IS NULL OR version = $3) RETURNING *
            """,
            subject_id,
            state,
            version,
        )
        return ExternalSubjectRecord.model_validate(rows[0]) if rows else None

    async def bind_runtime(
        self, subject_id: str, runtime_user_id: str
    ) -> ExternalSubjectRecord | None:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthsubject SET runtime_user_id = $2, version = version + 1, updated_at = NOW()
            WHERE subject_id = $1 AND state = 'pending' AND (runtime_user_id IS NULL OR runtime_user_id = $2) RETURNING *
            """,
            subject_id,
            runtime_user_id,
        )
        return ExternalSubjectRecord.model_validate(rows[0]) if rows else None
