from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ORG_BUDGET_ROLES = ("org_owner", "org_admin", "org_billing")
_TEAM_BUDGET_ROLES = ("team_admin",)


@dataclass(frozen=True)
class NotificationRecipients:
    emails: tuple[str, ...]
    policy: str
    owner_account_id: str | None = None
    team_id: str | None = None
    organization_id: str | None = None


class NotificationRecipientResolver:
    def __init__(self, db_client: Any | None) -> None:
        self.db = db_client

    async def resolve_budget_recipients(self, *, entity_type: str, entity_id: str) -> NotificationRecipients:
        if self.db is None or not entity_id:
            return NotificationRecipients(emails=(), policy="unresolved")

        if entity_type == "key":
            return await self._resolve_key_budget_recipients(entity_id)
        if entity_type == "team":
            return await self._resolve_team_budget_recipients(entity_id)
        if entity_type == "org":
            return await self._resolve_org_budget_recipients(entity_id)
        return NotificationRecipients(emails=(), policy="unsupported_scope")

    async def resolve_key_lifecycle_recipients(
        self,
        *,
        owner_account_id: str | None,
        team_id: str | None,
        organization_id: str | None,
    ) -> NotificationRecipients:
        owner_email = await self._load_active_account_email(owner_account_id)
        if owner_email is not None:
            return NotificationRecipients(
                emails=(owner_email,),
                policy="key_owner",
                owner_account_id=owner_account_id,
                team_id=team_id,
                organization_id=organization_id,
            )

        fallback = await self._resolve_team_fallback_emails(team_id=team_id, organization_id=organization_id)
        return NotificationRecipients(
            emails=fallback,
            policy="key_fallback",
            owner_account_id=owner_account_id,
            team_id=team_id,
            organization_id=organization_id,
        )

    async def get_account_email(self, account_id: str | None) -> str | None:
        return await self._load_active_account_email(account_id)

    async def _resolve_key_budget_recipients(self, token_hash: str) -> NotificationRecipients:
        rows = await self.db.query_raw(
            """
            SELECT
                vt.owner_account_id,
                COALESCE(vt.team_id, u.team_id) AS team_id,
                t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_usertable u ON u.user_id = vt.user_id
            LEFT JOIN deltallm_teamtable t ON t.team_id = COALESCE(vt.team_id, u.team_id)
            WHERE vt.token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return NotificationRecipients(emails=(), policy="missing_key")

        row = dict(rows[0])
        owner_account_id = _as_str(row.get("owner_account_id"))
        team_id = _as_str(row.get("team_id"))
        organization_id = _as_str(row.get("organization_id"))

        owner_email = await self._load_active_account_email(owner_account_id)
        if owner_email is not None:
            return NotificationRecipients(
                emails=(owner_email,),
                policy="key_owner",
                owner_account_id=owner_account_id,
                team_id=team_id,
                organization_id=organization_id,
            )

        fallback = await self._resolve_team_fallback_emails(team_id=team_id, organization_id=organization_id)
        return NotificationRecipients(
            emails=fallback,
            policy="key_fallback",
            owner_account_id=owner_account_id,
            team_id=team_id,
            organization_id=organization_id,
        )

    async def _resolve_team_budget_recipients(self, team_id: str) -> NotificationRecipients:
        rows = await self.db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        organization_id = _as_str((rows[0] if rows else {}).get("organization_id"))
        emails = await self._resolve_team_fallback_emails(team_id=team_id, organization_id=organization_id)
        return NotificationRecipients(
            emails=emails,
            policy="team_admins_and_org_admins",
            team_id=team_id,
            organization_id=organization_id,
        )

    async def _resolve_org_budget_recipients(self, organization_id: str) -> NotificationRecipients:
        emails = await self._load_org_role_emails(organization_id, _ORG_BUDGET_ROLES)
        return NotificationRecipients(
            emails=emails,
            policy="org_admins_and_billing",
            organization_id=organization_id,
        )

    async def _resolve_team_fallback_emails(self, *, team_id: str | None, organization_id: str | None) -> tuple[str, ...]:
        team_admins = await self._load_team_role_emails(team_id, _TEAM_BUDGET_ROLES) if team_id else ()
        org_admins = await self._load_org_role_emails(organization_id, _ORG_BUDGET_ROLES) if organization_id else ()
        return _dedupe_emails((*team_admins, *org_admins))

    async def _load_active_account_email(self, account_id: str | None) -> str | None:
        if not account_id:
            return None
        rows = await self.db.query_raw(
            """
            SELECT email
            FROM deltallm_platformaccount
            WHERE account_id = $1
              AND is_active = TRUE
            LIMIT 1
            """,
            account_id,
        )
        if not rows:
            return None
        return _normalize_email((rows[0] if rows else {}).get("email"))

    async def _load_team_role_emails(self, team_id: str | None, roles: tuple[str, ...]) -> tuple[str, ...]:
        if not team_id:
            return ()
        rows = await self.db.query_raw(
            """
            SELECT pa.email
            FROM deltallm_teammembership tm
            JOIN deltallm_platformaccount pa ON pa.account_id = tm.account_id
            WHERE tm.team_id = $1
              AND tm.role = ANY($2::text[])
              AND pa.is_active = TRUE
            ORDER BY lower(pa.email) ASC
            """,
            team_id,
            list(roles),
        )
        return _dedupe_emails(_normalize_email(row.get("email")) for row in rows)

    async def _load_org_role_emails(self, organization_id: str | None, roles: tuple[str, ...]) -> tuple[str, ...]:
        if not organization_id:
            return ()
        rows = await self.db.query_raw(
            """
            SELECT pa.email
            FROM deltallm_organizationmembership om
            JOIN deltallm_platformaccount pa ON pa.account_id = om.account_id
            WHERE om.organization_id = $1
              AND om.role = ANY($2::text[])
              AND pa.is_active = TRUE
            ORDER BY lower(pa.email) ASC
            """,
            organization_id,
            list(roles),
        )
        return _dedupe_emails(_normalize_email(row.get("email")) for row in rows)


def _normalize_email(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or "@" not in normalized:
        return None
    return normalized


def _dedupe_emails(values: Any) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        email = _normalize_email(value)
        if email is None or email in seen:
            continue
        seen.add(email)
        ordered.append(email)
    return tuple(ordered)


def _as_str(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
