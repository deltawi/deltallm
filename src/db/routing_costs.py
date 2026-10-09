from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
import json

from pydantic import ValidationError

from src.billing.pricing.routing_costs import RoutingCostObservation
from src.billing.charges.operation_reservation import ComponentState
from src.billing.charges.selector_charge import SelectorPriceSnapshot, SelectorTokenReceipt
from src.billing.spend.spend_read import SPEND_READ_SOURCE
from src.db.routing_cost_sql import routing_cost_sql
from src.services.spend_visibility import SpendVisibility, apply_spend_visibility


@dataclass(frozen=True, slots=True)
class RoutingCostQuery:
    sql: str
    params: tuple[object, ...]
    limit: int


def routing_cost_query(
    *,
    visibility: SpendVisibility,
    start: datetime,
    end: datetime,
    limit: int = 100,
    before: tuple[datetime, str] | None = None,
    model_group: str | None = None,
) -> RoutingCostQuery:
    """Execute through the existing bounded, protected reporting query owner.

    The operation page is bounded before unique legacy/event-sequence joins. The returned
    observations are not a full-history total; aggregate coverage retains that fact.
    """
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or not timedelta(0) < end - start <= timedelta(days=31)
    ):
        raise ValueError("routing cost reports require an aware range of at most 31 days")
    if not 1 <= limit <= 1000:
        raise ValueError("routing cost report limit must be bounded")
    source = replace(
        SPEND_READ_SOURCE,
        table="deltallm_billing_operations",
        owner_account_column="(snapshot #>> '{attribution,owner_account_id}')",
    )
    params: list[object] = [start, end]
    clauses = ["created_at >= $1::timestamptz", "created_at < $2::timestamptz"]
    apply_spend_visibility(clauses=clauses, params=params, visibility=visibility, source=source)
    if model_group is not None:
        if not model_group or len(model_group) > 256:
            raise ValueError("invalid model-group filter")
        params.append(model_group)
        clauses.append(f"model = ${len(params)}::text")
    if before is not None:
        params.extend(before)
        clauses.append(
            f"(created_at,operation_id)<(${len(params) - 1}::timestamptz,${len(params)}::text)"
        )
    params.append(limit + 1)
    sql = routing_cost_sql(clauses, limit_parameter=len(params))
    return RoutingCostQuery(sql, tuple(params), limit)


def routing_cost_observation(row: dict[str, object]) -> RoutingCostObservation:
    amounts = {}
    for name in (
        "selector_provider_cost",
        "selector_customer_charge",
        "answer_provider_cost",
        "answer_customer_charge",
        "measurable_penalty",
    ):
        value = row.get(name)
        amounts[name] = None if value is None else Decimal(str(value))
    return RoutingCostObservation(
        answer_model=row.get("answer_model"),
        selector_state=ComponentState(str(row["selector_state"]))
        if row.get("selector_state") is not None
        else None,
        answer_state=ComponentState(str(row["answer_state"]))
        if row.get("answer_state") is not None
        else None,
        baseline_answer_provider_cost=_reference_cost(row),
        **amounts,
    )


def _reference_cost(row: dict[str, object]) -> Decimal | None:
    pricing = row.get("reference_answer_pricing")
    if pricing is None or row.get("status") != "success":
        return None
    try:
        frozen = SelectorPriceSnapshot.model_validate_json(json.dumps(pricing))
        receipt = SelectorTokenReceipt(
            prompt_tokens=row.get("input_tokens"),
            completion_tokens=row.get("output_tokens"),
            total_tokens=row.get("total_tokens"),
            cached_input_tokens=0,
        )
        return frozen.cost(receipt)
    except (ValueError, ValidationError):
        return None
