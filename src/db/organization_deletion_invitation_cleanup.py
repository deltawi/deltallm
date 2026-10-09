from __future__ import annotations

import json
from typing import Any

from src.db.organization_deletion_records import parse_json_object


class OrganizationDeletionInvitationCleanup:
    def __init__(self, prisma_client: Any) -> None:
        self.prisma = prisma_client

    async def clean_page(self, organization_id: str, *, page_size: int) -> int:
        rows = await self.prisma.query_raw(
            """
            SELECT invitation_id, message_email_id, metadata
            FROM deltallm_platforminvitation i
            WHERE i.status IN ('pending', 'sent') AND (
                EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(i.metadata->'organization_invites', '[]'::jsonb)) item WHERE item->>'organization_id' = $1) OR
                EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(i.metadata->'team_invites', '[]'::jsonb)) item WHERE item->>'organization_id' = $1)
            )
            ORDER BY created_at ASC, invitation_id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT $2
            """,
            organization_id,
            page_size,
        )
        for row in rows:
            await self._clean_row(self.prisma, dict(row), organization_id)
        return len(rows)

    @staticmethod
    async def _clean_row(
        tx: Any,
        row: dict[str, object],
        organization_id: str,
    ) -> None:
        metadata = parse_json_object(row.get("metadata"))
        org_invites = _without_organization(
            metadata.get("organization_invites"),
            organization_id,
        )
        team_invites = _without_organization(
            metadata.get("team_invites"),
            organization_id,
        )
        metadata["organization_invites"] = org_invites
        metadata["team_invites"] = team_invites
        invitation_id = str(row.get("invitation_id") or "")
        if org_invites or team_invites:
            await _update_remaining_scopes(
                tx,
                invitation_id=invitation_id,
                metadata=metadata,
                org_invites=org_invites,
                team_invites=team_invites,
            )
            return
        await _cancel_invitation(
            tx,
            invitation_id=invitation_id,
            message_email_id=str(row.get("message_email_id") or ""),
            metadata=metadata,
        )


async def _update_remaining_scopes(
    tx: Any,
    *,
    invitation_id: str,
    metadata: dict[str, object],
    org_invites: list[dict[str, object]],
    team_invites: list[dict[str, object]],
) -> None:
    await tx.execute_raw(
        """
        UPDATE deltallm_platforminvitation
        SET metadata = $2::jsonb, invite_scope_type = $3, updated_at = NOW()
        WHERE invitation_id = $1
        """,
        invitation_id,
        json.dumps(metadata),
        _invitation_scope_type(org_invites, team_invites),
    )


async def _cancel_invitation(
    tx: Any,
    *,
    invitation_id: str,
    message_email_id: str,
    metadata: dict[str, object],
) -> None:
    await tx.execute_raw(
        """
        UPDATE deltallm_platforminvitation
        SET metadata = $2::jsonb, status = 'cancelled', cancelled_at = NOW(),
            updated_at = NOW()
        WHERE invitation_id = $1
        """,
        invitation_id,
        json.dumps(metadata),
    )
    await tx.execute_raw(
        """
        UPDATE deltallm_emailtoken
        SET consumed_at = COALESCE(consumed_at, NOW()), updated_at = NOW()
        WHERE invitation_id = $1 AND consumed_at IS NULL
        """,
        invitation_id,
    )
    if message_email_id:
        await tx.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'cancelled', last_error = 'Organization deletion requested',
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'queued'
            """,
            message_email_id,
        )


def _without_organization(value: object, organization_id: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item for key, item in entry.items()}
        for entry in value
        if isinstance(entry, dict) and str(entry.get("organization_id") or "") != organization_id
    ]


def _invitation_scope_type(
    organization_invites: list[dict[str, object]],
    team_invites: list[dict[str, object]],
) -> str:
    if organization_invites and team_invites:
        return "mixed"
    return "team" if team_invites else "organization"


__all__ = ["OrganizationDeletionInvitationCleanup"]
