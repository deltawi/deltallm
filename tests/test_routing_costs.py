from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.billing.routing_costs import RoutingCostObservation, aggregate_routing_costs
from src.db.routing_costs import routing_cost_query, routing_cost_observation
from src.services.spend_visibility import SpendVisibility


def cost(**updates):
    return RoutingCostObservation(
        **{
            "answer_model": "small",
            "selector_provider_cost": Decimal(".01"),
            "selector_customer_charge": Decimal(".01"),
            "answer_provider_cost": Decimal(".10"),
            "answer_customer_charge": Decimal(".15"),
            "baseline_answer_provider_cost": Decimal(".20"),
            "measurable_penalty": Decimal(".02"),
            **updates,
        }
    )


def test_net_savings_are_exact_net_of_selector_and_penalty_not_customer_markup():
    report = aggregate_routing_costs((cost(), cost()))
    assert report.net_savings_exact == "0.140000000000000000"
    assert report.selector_customer_charge_exact == "0.020000000000000000"
    assert report.answer_customer_charge_exact == "0.300000000000000000"
    assert report.operation_count == report.savings_covered_count == 2
    assert report.answer_distribution == (("small", 2),)
    assert not report.partial


@pytest.mark.parametrize(
    "field",
    [
        "baseline_answer_provider_cost",
        "selector_provider_cost",
        "answer_provider_cost",
        "measurable_penalty",
    ],
)
def test_missing_evidence_produces_unknown_savings_not_invented_zero(field):
    report = aggregate_routing_costs((cost(**{field: None}),))
    assert report.net_savings_exact is None
    assert report.savings_covered_count == 0
    assert report.partial


def test_negative_savings_are_not_clamped_and_truncation_is_visible():
    report = aggregate_routing_costs(
        (cost(baseline_answer_provider_cost=Decimal(0)),), truncated=True
    )
    assert report.net_savings_exact == "-0.130000000000000000"
    assert report.partial


@pytest.mark.parametrize(
    "visibility,expected",
    [
        (SpendVisibility(False, organization_ids=("org-a",)), "organization_id"),
        (SpendVisibility(False, team_ids=("team-a",)), "team_id"),
        (
            SpendVisibility(False, owner_account_id="owner-a", self_organization_ids=("org-a",)),
            "owner_account_id",
        ),
        (SpendVisibility(False), "1 = 0"),
    ],
)
def test_cost_page_uses_canonical_visibility_before_bounded_unique_event_joins(
    visibility, expected
):
    end = datetime.now(UTC)
    query = routing_cost_query(
        visibility=visibility, start=end - timedelta(days=1), end=end, limit=50
    )
    assert expected in query.sql
    assert query.params[-1] == 51
    assert "WITH page AS MATERIALIZED" in query.sql
    assert query.sql.index("LIMIT") < query.sql.index("LEFT JOIN")
    assert "org-a" not in query.sql and "owner-a" not in query.sql
    assert "s.id=o.selector_event_id" in query.sql
    assert "a.id=o.operation_id" in query.sql


def test_cost_aggregation_is_bounded():
    with pytest.raises(ValueError, match="bounded"):
        aggregate_routing_costs((cost(),) * 1001)


def test_savings_baseline_uses_only_frozen_server_pricing_with_reported_answer_tokens():
    from tests.test_operation_reservation import make_operation
    from src.billing.selector_charge import SelectorTokenReceipt

    price = make_operation().selector.pricing
    row = dict(
        reference_answer_pricing=price.model_dump(mode="json"),
        input_tokens=100,
        output_tokens=5,
        total_tokens=105,
        status="success",
        baseline_answer_provider_cost="999",
        answer_model="small",
    )
    observation = routing_cost_observation(row)
    assert observation.baseline_answer_provider_cost == price.cost(
        SelectorTokenReceipt(
            prompt_tokens=100, completion_tokens=5, total_tokens=105, cached_input_tokens=0
        )
    )
    assert observation.net_savings is None
    row["total_tokens"] = None
    assert routing_cost_observation(row).baseline_answer_provider_cost is None
    row["total_tokens"], row["status"] = 105, "failure"
    assert routing_cost_observation(row).baseline_answer_provider_cost is None


def test_pending_reconciliation_is_visible_separately_from_zero_cost():
    from src.billing.operation_reservation import ComponentState

    report = aggregate_routing_costs(
        (
            cost(
                selector_state=ComponentState.PENDING,
                selector_provider_cost=None,
                selector_customer_charge=None,
            ),
        )
    )
    assert report.pending_reconciliation_count == 1
    assert report.partial and report.net_savings_exact is None
