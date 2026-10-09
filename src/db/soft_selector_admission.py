from __future__ import annotations

from typing import TYPE_CHECKING

from src.billing.money import money_string
from src.billing.operation_reservation import BillingOperationUnavailable, SoftSelectorOperation

if TYPE_CHECKING:
    from prisma import Prisma


async def check_soft_selector_admission(tx: Prisma, operation: SoftSelectorOperation) -> None:
    """One bounded ownership/budget snapshot; no account locks or spending holds.

    This is the operation journal's soft counterpart to adjust_operation_hold.
    Missing scopes/counters fail closed. A concurrently admitted operation may
    overshoot; missing rollups are never repaired by scanning history inline.
    """
    owner = operation.attribution
    rows = await tx.query_raw(
        """
        SELECT k.token FROM deltallm_verificationtoken k
        LEFT JOIN deltallm_usertable u ON u.user_id=$2
        LEFT JOIN deltallm_teamtable t ON t.team_id=$3
        LEFT JOIN deltallm_organizationtable o ON o.organization_id=$4
        LEFT JOIN deltallm_teammodelspend m ON m.team_id=$3 AND m.model=$6
        WHERE k.token=$1
          AND k.user_id IS NOT DISTINCT FROM $2::text
          AND k.team_id IS NOT DISTINCT FROM $3::text
          AND k.owner_account_id IS NOT DISTINCT FROM $5::text
          AND t.organization_id IS NOT DISTINCT FROM $4::text
          AND ($2::text IS NULL OR (u.user_id IS NOT NULL AND u.blocked IS NOT TRUE))
          AND ($3::text IS NULL OR (t.team_id IS NOT NULL AND t.blocked IS NOT TRUE))
          AND ($4::text IS NULL OR (o.organization_id IS NOT NULL AND o.lifecycle_state='active'))
          AND (k.expires IS NULL OR k.expires>CURRENT_TIMESTAMP)
          AND NOT COALESCE(COALESCE(k.spend_exact,k.spend::numeric,0)+$7::numeric>k.max_budget::numeric,false)
          AND NOT COALESCE(COALESCE(u.spend_exact,u.spend::numeric,0)+$7::numeric>u.max_budget::numeric,false)
          AND NOT COALESCE(COALESCE(t.spend_exact,t.spend::numeric,0)+$7::numeric>t.max_budget::numeric,false)
          AND NOT COALESCE(COALESCE(o.spend_exact,o.spend::numeric,0)+$7::numeric>o.max_budget::numeric,false)
          AND (t.model_max_budget->>$6 IS NULL OR COALESCE(m.spend_exact,m.spend::numeric)
                +$7::numeric<=(t.model_max_budget->>$6)::numeric)
        """,
        owner.api_key,
        owner.user_id,
        owner.team_id,
        owner.organization_id,
        owner.owner_account_id,
        owner.model_group,
        money_string(operation.admission_allowance),
    )
    if not rows:
        raise BillingOperationUnavailable()
