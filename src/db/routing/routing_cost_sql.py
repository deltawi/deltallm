"""Build one bounded operation page with exact owner-checked component joins."""

from collections.abc import Sequence

from src.billing.spend.spend_read import SPEND_READ_SOURCE

_ANSWER_FIELDS = (
    "usage_snapshot",
    "unpriced_reason",
    "deployment_model",
    "provider_cost_exact",
    "spend_exact",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "status",
)
_ANSWER_RECEIPT = """(v.usage_snapshot->>'kind'='reported' OR (
    jsonb_typeof(v.usage_snapshot->'prompt_tokens')='number'
    AND jsonb_typeof(v.usage_snapshot->'completion_tokens')='number'
    AND v.unpriced_reason IS NULL
))"""
_SELECTOR_STATE = """CASE WHEN o.accounting_protocol='primary' THEN
    CASE WHEN o.snapshot #>> '{pricing_snapshot,selector_event_id}' IS NULL THEN 'unattempted'
         WHEN ns.operation_id IS NULL THEN 'pending'
         WHEN ns.accounting_state='released' THEN 'unattempted'
         WHEN ns.accounting_state='finalized' AND sn.accounting_sequence IS NOT NULL THEN 'settled'
         ELSE 'pending' END
    ELSE o.selector_state END"""
_SELECTOR_PROVIDER = """CASE WHEN o.accounting_protocol='primary' THEN
    CASE WHEN o.snapshot #>> '{pricing_snapshot,selector_event_id}' IS NULL
              OR ns.accounting_state='released' THEN '0'
         ELSE sn.provider_cost_exact::text END
    WHEN o.selector_state='unattempted' THEN '0'
    ELSE COALESCE(s.provider_cost_exact::text,o.selector_receipt->>'provider_cost_exact') END"""
_SELECTOR_CUSTOMER = _SELECTOR_PROVIDER.replace("provider_cost_exact", "spend_exact").replace(
    "o.selector_receipt->>'spend_exact'", "o.selector_receipt->>'cost_exact'"
)

_SQL = """
WITH page AS MATERIALIZED (
    SELECT * FROM deltallm_billing_operations WHERE {where}
    AND (accounting_protocol IS DISTINCT FROM 'primary'
        OR snapshot #>> '{{attribution,call_type}}' IS DISTINCT FROM 'model_router_selector')
    ORDER BY created_at DESC,operation_id DESC LIMIT ${limit}
)
SELECT o.operation_id,o.created_at,{selector_state} AS selector_state,
    CASE WHEN o.snapshot->>'budget_mode'='soft_selector:v1' THEN
        CASE WHEN {answer_receipt} THEN 'settled' ELSE 'pending' END
        ELSE o.answer_state END AS answer_state,
    v.deployment_model AS answer_model,
    {selector_provider} AS selector_provider_cost,
    {selector_customer} AS selector_customer_charge,
    CASE WHEN {answer_receipt} THEN v.provider_cost_exact::text
        WHEN o.answer_state='unattempted'
          AND o.snapshot->>'budget_mode' IS DISTINCT FROM 'soft_selector:v1'
        THEN '0' END AS answer_provider_cost,
    CASE WHEN {answer_receipt} THEN v.spend_exact::text
        WHEN o.answer_state='unattempted'
          AND o.snapshot->>'budget_mode' IS DISTINCT FROM 'soft_selector:v1'
        THEN '0' END AS answer_customer_charge,
    o.snapshot->'reference_answer_pricing' AS reference_answer_pricing,
    o.snapshot->>'measurable_switch_penalty' AS measurable_penalty,
    v.input_tokens,v.output_tokens,v.total_tokens,v.status
FROM page o LEFT JOIN {spend_table} s ON s.id=o.selector_event_id
    AND o.accounting_protocol IS NULL
LEFT JOIN deltallm_spendlog_events a ON a.id=o.operation_id
    AND o.accounting_protocol IS NULL
LEFT JOIN deltallm_accounting_usage_facts_v2 n
    ON n.accounting_sequence=o.final_event_sequence
    AND o.accounting_protocol='primary'
    AND n.protocol_generation=o.accounting_generation
    AND n.operation_id=o.operation_id AND n.api_key=o.api_key
    AND n.organization_id IS NOT DISTINCT FROM o.organization_id
    AND n.team_id IS NOT DISTINCT FROM o.team_id
    AND n.user_id IS NOT DISTINCT FROM o.user_id
    AND n.owner_account_id IS NOT DISTINCT FROM
        (o.snapshot #>> '{{attribution,owner_account_id}}')
LEFT JOIN deltallm_billing_operations ns
    ON ns.operation_id=o.snapshot #>> '{{pricing_snapshot,selector_event_id}}'
    AND o.accounting_protocol='primary' AND ns.accounting_protocol='primary'
    AND ns.accounting_generation=o.accounting_generation
    AND ns.snapshot #>> '{{attribution,call_type}}'='model_router_selector'
    AND ns.snapshot #>> '{{pricing_snapshot,parent_event_id}}'=o.operation_id
    AND ns.api_key=o.api_key AND ns.model=o.model
    AND ns.organization_id IS NOT DISTINCT FROM o.organization_id
    AND ns.team_id IS NOT DISTINCT FROM o.team_id
    AND ns.user_id IS NOT DISTINCT FROM o.user_id
    AND ns.snapshot #>> '{{attribution,owner_account_id}}'
        IS NOT DISTINCT FROM o.snapshot #>> '{{attribution,owner_account_id}}'
LEFT JOIN deltallm_accounting_usage_facts_v2 sn
    ON sn.accounting_sequence=ns.final_event_sequence
    AND sn.protocol_generation=ns.accounting_generation AND sn.operation_id=ns.operation_id
    AND sn.api_key=ns.api_key AND sn.model=ns.model
    AND sn.organization_id IS NOT DISTINCT FROM ns.organization_id
    AND sn.team_id IS NOT DISTINCT FROM ns.team_id
    AND sn.user_id IS NOT DISTINCT FROM ns.user_id
    AND sn.owner_account_id IS NOT DISTINCT FROM ns.snapshot #>> '{{attribution,owner_account_id}}'
LEFT JOIN LATERAL (SELECT {answer_fields}) v ON TRUE
ORDER BY o.created_at DESC,o.operation_id DESC
"""


def routing_cost_sql(clauses: Sequence[str], *, limit_parameter: int) -> str:
    answer_fields = ",".join(
        f"CASE WHEN o.accounting_protocol='primary' THEN n.{field} ELSE a.{field} END AS {field}"
        for field in _ANSWER_FIELDS
    )
    return _SQL.format(
        where=" AND ".join(clauses),
        limit=limit_parameter,
        answer_fields=answer_fields,
        answer_receipt=_ANSWER_RECEIPT,
        spend_table=SPEND_READ_SOURCE.table,
        selector_state=_SELECTOR_STATE,
        selector_provider=_SELECTOR_PROVIDER,
        selector_customer=_SELECTOR_CUSTOMER,
    )
