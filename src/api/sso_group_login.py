"""SSO callback edge for group sync: read the claim, gate sign-in, and finish side effects."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request, status

from src.auth.sso_group_claims import extract_claim_values
from src.auth.sso_group_settings import SSOGroupSyncSettings
from src.services.identity.sso_group_access import SSOGroupAccessPlan, plan_group_access
from src.services.identity.sso_group_sync import SSOGroupSyncResult

logger = logging.getLogger(__name__)

SSO_GROUP_ACCESS_DENIED_DETAIL = (
    "Your SSO account has no mapped DeltaLLM group. Ask an administrator for access."
)


def resolve_sso_group_access(
    general_settings: Any, callback_payload: Mapping[str, Any]
) -> SSOGroupAccessPlan | None:
    """Return the access plan for this sign-in, or `None` when group sync does not apply.

    A missing claim gives `None` so that an IdP scope or claim-map mistake never
    removes access. `require_mapped_value` turns a missing claim, or a claim with no
    mapped value, into a denied sign-in.
    """

    settings = getattr(general_settings, "sso_group_sync", None)
    if not isinstance(settings, SSOGroupSyncSettings) or not settings.enabled:
        return None
    raw_sources = callback_payload.get("claim_sources")
    sources = [item for item in raw_sources or () if isinstance(item, Mapping)]
    values = extract_claim_values(sources, settings.claim)
    if values is None:
        if settings.require_mapped_value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=SSO_GROUP_ACCESS_DENIED_DETAIL
            )
        logger.warning(
            "sso group sync skipped: claim %r not in ID token or userinfo", settings.claim
        )
        return None
    plan = plan_group_access(settings, values)
    if settings.require_mapped_value and not plan.matched_values:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=SSO_GROUP_ACCESS_DENIED_DETAIL
        )
    return plan


def group_access_kwargs(plan: SSOGroupAccessPlan | None) -> dict[str, SSOGroupAccessPlan]:
    """Identity service keyword arguments; empty when sync does not apply."""
    return {"group_access": plan} if plan is not None else {}


async def finish_sso_group_sync(request: Request, login: Any) -> dict[str, Any] | None:
    """Invalidate caches of keys that sync deleted, and return the audit summary."""

    result = getattr(login, "group_sync", None)
    if not isinstance(result, SSOGroupSyncResult):
        return None
    key_service = getattr(request.app.state, "key_service", None)
    for token_hash in result.revoked_key_hashes:
        try:
            if key_service is not None:
                await key_service.invalidate_key_cache_by_hash(token_hash)
        except Exception:
            logger.exception("sso group sync: key cache invalidation failed")
    return result.audit_summary()
