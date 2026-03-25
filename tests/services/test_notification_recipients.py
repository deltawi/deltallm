from __future__ import annotations

import pytest

from src.services.notification_recipients import NotificationRecipientResolver


class _RecipientDB:
    async def query_raw(self, query: str, *args):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if "from deltallm_verificationtoken vt" in normalized:
            return [
                {
                    "owner_account_id": "acct-owner",
                    "team_id": "team-1",
                    "organization_id": "org-1",
                }
            ]
        if "from deltallm_platformaccount" in normalized and "where account_id = $1" in normalized:
            if args[0] == "acct-owner":
                return [{"email": "Owner@Example.com"}]
            return []
        if "from deltallm_teammembership tm" in normalized:
            return [{"email": "team-admin@example.com"}, {"email": "shared@example.com"}]
        if "from deltallm_organizationmembership om" in normalized:
            return [{"email": "billing@example.com"}, {"email": "shared@example.com"}]
        if "from deltallm_teamtable" in normalized:
            return [{"organization_id": "org-1"}]
        return []


class _FallbackRecipientDB:
    async def query_raw(self, query: str, *args):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if "from deltallm_verificationtoken vt" in normalized:
            return [
                {
                    "owner_account_id": "acct-missing",
                    "team_id": "team-1",
                    "organization_id": "org-1",
                }
            ]
        if "from deltallm_platformaccount" in normalized and "where account_id = $1" in normalized:
            return []
        if "from deltallm_teammembership tm" in normalized:
            return [{"email": "team-admin@example.com"}, {"email": "shared@example.com"}]
        if "from deltallm_organizationmembership om" in normalized:
            return [{"email": "billing@example.com"}, {"email": "shared@example.com"}]
        if "from deltallm_teamtable" in normalized:
            return [{"organization_id": "org-1"}]
        return []


@pytest.mark.asyncio
async def test_resolve_budget_recipients_prefers_key_owner() -> None:
    resolver = NotificationRecipientResolver(_RecipientDB())

    recipients = await resolver.resolve_budget_recipients(entity_type="key", entity_id="token-1")

    assert recipients.policy == "key_owner"
    assert recipients.emails == ("owner@example.com",)
    assert recipients.team_id == "team-1"
    assert recipients.organization_id == "org-1"


@pytest.mark.asyncio
async def test_resolve_budget_recipients_falls_back_and_dedupes() -> None:
    resolver = NotificationRecipientResolver(_FallbackRecipientDB())

    recipients = await resolver.resolve_budget_recipients(entity_type="key", entity_id="token-1")

    assert recipients.policy == "key_fallback"
    assert recipients.emails == (
        "team-admin@example.com",
        "shared@example.com",
        "billing@example.com",
    )


@pytest.mark.asyncio
async def test_resolve_team_budget_recipients_includes_team_and_org_admins() -> None:
    resolver = NotificationRecipientResolver(_FallbackRecipientDB())

    recipients = await resolver.resolve_budget_recipients(entity_type="team", entity_id="team-1")

    assert recipients.policy == "team_admins_and_org_admins"
    assert recipients.emails == (
        "team-admin@example.com",
        "shared@example.com",
        "billing@example.com",
    )
