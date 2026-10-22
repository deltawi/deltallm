"""`GET /v1/key`: what the calling API key may spend and how fast, read with the key itself."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.accounting_budget import apply_accounting_balances
from src.billing.accounting.accounting_protocol import AccountingScope
from src.middleware.auth import require_api_key
from src.models.errors import InvalidRequestError
from src.models.responses import UserAPIKeyAuth
from src.api.admin.endpoints.common import to_json_value

router = APIRouter(prefix="/v1", tags=["key"])

_LIMIT_FIELDS = {
    "rpm": "rpm_limit",
    "tpm": "tpm_limit",
    "output_tpm": "output_tpm_limit",
    "rph": "rph_limit",
    "rpd": "rpd_limit",
    "tpd": "tpd_limit",
}


@router.get("/key", dependencies=[Depends(require_api_key)])
async def key_info(request: Request) -> dict[str, Any]:
    auth: UserAPIKeyAuth = request.state.user_api_key
    if (auth.metadata or {}).get("auth_source") != "api_key":
        raise InvalidRequestError(
            message="This endpoint describes DeltaLLM API keys; the request used another credential"
        )
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        )
    rows = await db.query_raw(
        """
        SELECT
            vt.token,
            vt.key_name,
            vt.team_id,
            vt.spend,
            vt.max_budget,
            vt.budget_duration,
            vt.budget_reset_at,
            vt.rpm_limit,
            vt.tpm_limit,
            vt.output_tpm_limit,
            vt.rph_limit,
            vt.rpd_limit,
            vt.tpd_limit,
            vt.expires,
            vt.created_at
        FROM deltallm_verificationtoken vt
        WHERE vt.token = $1
        LIMIT 1
        """,
        auth.api_key,
    )
    if not rows:
        # Authentication already succeeded, so the key was removed in between.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    row = dict(rows[0])
    await apply_accounting_balances(request, [row], AccountingScope.API_KEY)
    return _key_payload(to_json_value(row), auth)


def _key_payload(row: dict[str, Any], auth: UserAPIKeyAuth) -> dict[str, Any]:
    spend = float(row.get("spend") or 0.0)
    max_budget = row.get("max_budget")
    return {
        "object": "key",
        "name": row.get("key_name"),
        "team_id": row.get("team_id"),
        "organization_id": auth.organization_id,
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires"),
        "budget": {
            "max_budget": max_budget,
            "spend": spend,
            "remaining": (max(0.0, float(max_budget) - spend) if max_budget is not None else None),
            "duration": row.get("budget_duration"),
            "reset_at": row.get("budget_reset_at"),
        },
        "limits": {name: row.get(column) for name, column in _LIMIT_FIELDS.items()},
    }
