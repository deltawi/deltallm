from datetime import UTC, datetime
import json
from uuid import UUID

from pydantic import AwareDatetime

from src.billing.routing_costs import RoutingCostAggregate, aggregate_routing_costs
from src.billing.selector_charge import FrozenBillingContract
from src.db.reporting import ReportingDatabase, _run_reporting_query
from src.db.routing_costs import RoutingCostQuery, routing_cost_observation
from src.services.spend_reporting_cache import SpendReportingCache


class RoutingCostCursor(FrozenBillingContract):
    created_at: AwareDatetime
    operation_id: UUID


class RoutingCostPage(FrozenBillingContract):
    generated_at: AwareDatetime
    summary: RoutingCostAggregate
    has_more: bool
    next_cursor: RoutingCostCursor | None


async def load_routing_cost_page(
    database: ReportingDatabase, allocation: SpendReportingCache, query: RoutingCostQuery
) -> RoutingCostPage:
    rows = await _run_reporting_query(database, allocation, query.sql, *query.params)
    has_more = len(rows) > query.limit
    page = rows[: query.limit]
    cursor = None
    if has_more:
        cursor = RoutingCostCursor.model_validate_json(
            json.dumps(
                {
                    "created_at": str(page[-1]["created_at"]),
                    "operation_id": str(page[-1]["operation_id"]),
                }
            )
        )
    return RoutingCostPage(
        generated_at=datetime.now(UTC),
        summary=aggregate_routing_costs(
            tuple(routing_cost_observation(row) for row in page), truncated=has_more
        ),
        has_more=has_more,
        next_cursor=cursor,
    )
