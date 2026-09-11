from collections import Counter
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field

from src.billing.money import canonical_money, money_string
from src.billing.operation_reservation import ComponentState
from src.billing.selector_charge import FrozenBillingContract, Identifier, Price


class RoutingCostObservation(FrozenBillingContract):
    selector_state: ComponentState | None = None
    answer_state: ComponentState | None = None
    answer_model: Identifier | None = None
    selector_provider_cost: Price | None = None
    selector_customer_charge: Price | None = None
    answer_provider_cost: Price | None = None
    answer_customer_charge: Price | None = None
    baseline_answer_provider_cost: Price | None = None
    measurable_penalty: Price | None = None

    @property
    def net_savings(self) -> Decimal | None:
        values = (
            self.baseline_answer_provider_cost,
            self.answer_provider_cost,
            self.selector_provider_cost,
            self.measurable_penalty,
        )
        if any(value is None for value in values):
            return None
        baseline, answer, selector, penalty = values
        with localcontext() as context:
            context.prec = 80
            return canonical_money(baseline - answer - selector - penalty)


class RoutingCostAggregate(FrozenBillingContract):
    savings_kind: Literal["counterfactual_estimate"] = "counterfactual_estimate"
    operation_count: int = Field(ge=0)
    complete_cost_count: int = Field(ge=0)
    pending_reconciliation_count: int = Field(ge=0)
    savings_covered_count: int = Field(ge=0)
    selector_provider_cost_count: int = Field(ge=0)
    selector_customer_charge_count: int = Field(ge=0)
    answer_provider_cost_count: int = Field(ge=0)
    answer_customer_charge_count: int = Field(ge=0)
    selector_provider_cost_exact: str
    selector_customer_charge_exact: str
    answer_provider_cost_exact: str
    answer_customer_charge_exact: str
    net_savings_exact: str | None
    partial: bool
    answer_distribution: tuple[tuple[str, int], ...]


def aggregate_routing_costs(
    rows: tuple[RoutingCostObservation, ...], *, truncated: bool = False
) -> RoutingCostAggregate:
    """Bounded, control-plane aggregation. Null means unknown, not free work.

    A counterfactual baseline and penalty must be explicitly measured/frozen by the
    future answer owner. No duplicate answer or selector calls estimate savings.
    """
    if len(rows) > 1000:
        raise ValueError("routing cost aggregation requires a bounded page")
    fields = (
        "selector_provider_cost",
        "selector_customer_charge",
        "answer_provider_cost",
        "answer_customer_charge",
    )
    totals = {field: Decimal(0) for field in fields}
    counts = {field: 0 for field in fields}
    complete = covered = 0
    savings = Decimal(0)
    distribution: Counter[str] = Counter()
    with localcontext() as context:
        context.prec = 80
        for row in rows:
            values = row.model_dump()
            for field in fields:
                if values[field] is not None:
                    totals[field] += values[field]
                    counts[field] += 1
            complete += all(values[field] is not None for field in fields)
            if row.net_savings is not None:
                covered += 1
                savings += row.net_savings
            if row.answer_model is not None:
                distribution[row.answer_model] += 1
    return RoutingCostAggregate(
        operation_count=len(rows),
        complete_cost_count=complete,
        pending_reconciliation_count=sum(
            row.selector_state is ComponentState.PENDING
            or row.answer_state is ComponentState.PENDING
            for row in rows
        ),
        savings_covered_count=covered,
        **{f"{field}_count": counts[field] for field in fields},
        selector_provider_cost_exact=money_string(totals["selector_provider_cost"]),
        selector_customer_charge_exact=money_string(totals["selector_customer_charge"]),
        answer_provider_cost_exact=money_string(totals["answer_provider_cost"]),
        answer_customer_charge_exact=money_string(totals["answer_customer_charge"]),
        net_savings_exact=money_string(savings) if covered else None,
        partial=truncated or complete != len(rows) or covered != len(rows),
        answer_distribution=tuple(sorted(distribution.items())),
    )
