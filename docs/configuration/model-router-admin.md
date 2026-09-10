# Model router administration

Model selectors are optional. A group without a selector keeps its existing routing
behavior. There is no shadow mode, background evaluation, or second activation switch.

## Choose, assign, publish

1. Open a chat **Model Group → Advanced → Routing Policy**.
2. Under **Model selector**, choose a configured chat deployment. It does not
   need to be a group member. Search by name, provider or deployment ID.
3. Assign the group's answer members to lanes, for example OSS-20B to `economy`
   and MiniMax to `quality`. A tiny external selector receives no answer lane.
   Existing assignments are preserved; new ones start at **Choose lane**.
   Names and prices alone do not prove capability.
4. Select **Publish** and confirm the data-path and cost consequences. Successful
   publication activates routing directly. **Validate**, **Save Draft**, and
   **Evaluate selector** are optional, not a wizard or publication prerequisite.

The selector sees a bounded projection of uncached request text. Confirm that its
provider may receive that data. It adds one provider call and latency. Customers
pay the selector's actual provider cost even when an answer later fails; public
response usage remains answer-only. Whole-response cache hits skip the selector.

The defaults are two lanes, highest-capability `quality` as the safe default,
a 750 ms timeout, and an 8,000-character input limit. Advanced settings allow
2–8 lanes, consecutive ranks starting at zero, descriptions of 1–512 characters,
timeouts of 100–5,000 ms, and input limits of 256–32,768 characters. Every
selected answer member needs a lane; every lane needs an enabled member.
Routing may escalate to higher ranks, never silently downgrade.

Choosing a selector enables known-capacity context filtering when previously
disabled. Publication still uses the canonical server-side qualification checks:
enabled chat members, positive context limits, explicit chat capabilities,
classifier pricing and RPM/TPM limits, plus healthy selector billing/outbox and
shared admission dependencies. A selector report does not replace these checks.
See [router configuration](router.md#model-router-policy-contract) for runtime details.

If no selector is offered, check the search and configured chat deployments.
The picker distinguishes missing targets, unavailable inventory and permission denial.
Incomplete metadata stays visible with a repair warning; publication remains blocked.
Unknown modes are not guessed. Inline errors identify
invalid ranks, missing assignments, unsupported workloads and invalid classifiers;
publication also returns authoritative server errors. Correct the model metadata
or dependency health instead of disabling qualification.

Choosing or replacing a selector never changes answer membership, enabled flags,
weights, priorities or existing lanes. A deployment answers only if explicitly
included as an answer member; an existing dual-role model stays dual-role.
Disabling its answer membership does not disable the independent selector hop.

Only configuration administrators can attach or enumerate selectors. A caller
authorized for the group may use its internal selector without gaining permission
to call that deployment directly. Placement/data-handling restrictions still apply.
Switch or remove published selector references before deleting their deployment
or making its metadata incompatible, including for temporarily disabled groups.

All APIs and Batch workers must run the independent-selector release before
publishing an external reference. See the
[rollout decision](../project/model-router-independent-selector.md#rollout-and-rollback).

## Disable, import/export, canary and rollback

Choose **None — use the routing strategy only**, then publish to disable the
selector. The policy sends explicit `selector: null`, including after validation;
omission on a partial update would preserve an existing selector.
For example, publishing raw JSON containing only `{"strategy":"least-busy"}`
leaves the published selector unchanged. The confirmation warns that
classification and customer charges continue; only explicit removal is described
as disabling the selector.
Choosing **None** in the guided editor records explicit removal even when the
imported JSON did not contain a selector. Validation and JSON/draft round trips
retain that choice; choosing a model again replaces it with selector configuration.

The collapsed **Effective Policy Preview** area shows the complete policy for copying.
Use **Raw JSON** to paste an exported policy, then validate and publish. Switching
back to the guided editor preserves historical/server-owned opaque fields and
the explicit removal intent. Arbitrary new fields in selector-authored policies
still fail the backend's strict contract. Import into another group requires its
own valid member IDs and eligibility; an export is not authorization.

Active-policy and revision-history summaries show the classifier, limits, default
lane and member assignments. **Restore** targets the selected history revision
and revalidates it through the existing rollback service; an old selector revision
must still qualify under today's deployment metadata.

For a canary, create a separate group and bind it through the existing tenant,
team or key controls. Normal publication changes the whole group's policy;
PR 5 does not introduce percentage rollout. Before rolling back to binaries
without selector execution, disable active selectors and drain in-flight work.

## Optional fixture evaluation

Evaluation analyzes supplied test results; it does **not** call a provider,
estimate missing usage, charge a key, or change routing. Collect labeled examples
and outputs through your approved testing workflow, then paste a JSON sample
array into **Evaluate selector** and explicitly select **Analyze results**.
The current edited selector supplies the lane contract.

The same deterministic implementation is available locally:

```sh
uv run python -m src.services.selector_evaluation_cli fixtures.json
```

The local file and admin API use this complete request shape:

```json
{
  "selector": {
    "kind": "llm-tier",
    "classifier_deployment_id": "dep-mini",
    "lanes": [
      {"id": "economy", "rank": 0, "description": "Routine tasks"},
      {"id": "quality", "rank": 1, "description": "Complex tasks"}
    ],
    "default_lane": "quality",
    "timeout_ms": 750,
    "max_input_chars": 8000
  },
  "samples": [
    {
      "expected_lane": "economy",
      "output": "{\"lane\":\"economy\"}",
      "latency_ms": 120,
      "answer_quality_score": 0.98,
      "costs": {
        "selector_provider_cost": "0.0001",
        "selector_customer_charge": "0.0001",
        "answer_provider_cost": "0.001",
        "answer_customer_charge": "0.001",
        "baseline_answer_provider_cost": "0.005",
        "measurable_penalty": "0"
      }
    }
  ]
}
```

`POST /ui/api/route-policy-evaluations` requires authenticated admin
`CONFIG_READ` permission. The body is limited to 256 KiB and 1–100 samples,
with a five-second upload timeout. Each sample has exactly one `output` or
`failure` (`selector_timeout`, `provider_error`, or `capacity_denied`).
Expected lanes must belong to the selector. Optional prompt text is limited to
32,768 characters and output fixtures to 1,024 characters; the runtime parser's
tighter output bound still applies when replaying them.

Optional timings must be finite, nonnegative and at most 300,000 ms; answer quality
is a supplied score from 0 to 1. Costs are nonnegative exact decimal **strings**
(up to 20 integer and 18 fractional digits), or null when unknown.
Per-operation differences and report totals must fit the existing exact-money range.
Invalid fields/labels/totals return sanitized 400, missing/insufficient authentication
401/403, slow uploads 408, oversized bodies 413, and required-audit failure 503.

Reports contain per-lane precision/recall, confusion counts, route distribution,
safe-default causes/rate, supplied latency p50/p95, supplied answer-quality mean
and cost coverage. They reuse the runtime's strict output parser: malformed or
unknown output selects the safe-default lane. Precision/recall is unavailable
when its denominator is zero. Lane agreement is not proof of answer quality.

No prompts or raw outputs are returned or included in evaluation audit.
Only the action and bounded sample-count/basis metadata are audited through the
existing synchronous audit owner. Responses are `no-store`; the UI retains at
most its current in-memory report, cancels stale work, and clears protected results
on principal/group changes. Changed samples or policy mark prior results stale.
Remote **paid model execution** is not implemented by this analysis endpoint.

## Optional recorded-cost reports

Select **Selector costs → Load latest costs** for an explicit read of the last
24 hours, 7 days or 31 days. Each page summarizes up to 100 operations, not the
whole period. **Older operations** continues the same time window.
The cursor and exact start/end timestamps belong to the last successfully loaded
page. A failed refresh leaves that page and its pagination window intact. If you
change the selected window, load it successfully before paginating, or switch
back to the previous window to continue its report. Reports never refresh
automatically.

`GET /ui/api/spend/routing-costs` requires existing spend permissions and
server-derived visibility. Send timezone-aware `start` (inclusive) and `end`
(exclusive), at most 31 days apart; optional `model_group` filters the billed
group. `limit` defaults to 100 and is bounded at 1,000. Continue with both
`before_created_at` and `before_operation_id` from `next_cursor`, keeping
the original filters. Existing organization/team/self views and the scoped
reporting feature gate still apply. Client filters cannot broaden authorization.

This read shares the existing bounded spend-reporting allocation and SQL
deadlines. Invalid queries return 422, denial 401/403, and capacity/timeouts or
unavailability 503. It performs no provider calls, ledger writes or reconciliation.
The report joins selector operations to existing answer/selector spend events.
Soft-budget journal `unattempted` answer state is not proof of a free answer:
the actual answer receipt is authoritative. Missing or unpriced receipts remain
unavailable/pending; late receipts become visible on the next explicit refresh.

Exact totals are known subtotals with per-component coverage counts. The UI shows
unavailable, pending, partial, empty and error states separately; it does not turn
missing spend into zero. Answer-model distribution is based on recorded events.

Net savings is explicitly a **counterfactual estimate**:

`baseline answer provider cost − actual answer provider cost − selector provider cost − measurable penalty`.

Only rows with all four inputs contribute. Missing baseline or cache/model-switch
penalty makes savings unavailable, not zero. Current production operations do not
necessarily contain this counterfactual evidence. Fixture reports can analyze
supplied evidence, but neither report establishes actual workload savings.

## Suggested acceptance criteria

Choose thresholds before testing with representative, held-out traffic. As
starting guidance (not enforced defaults or a production-quality claim), require:

- no more than a one-percentage-point regression on your scored answer-quality
  metric, with separate checks for difficult/tool-bearing requests;
- positive net provider savings after selector cost and measured switching/cache
  penalties, with complete evidence and a workload-relevant margin;
- selector p95 within the added latency/TTFT allowance for your endpoint, for
  example 200 ms if that is its allocated budget, and below the configured timeout;
- a safe-default rate below 1%, investigating timeout, capacity and parser causes
  separately rather than optimizing the percentage by lowering safety.

Small fixture sets are useful smoke tests, not statistical certification.
Canary and observe real quality, total cost and latency before broad activation.
Internal Batch chat uses the same published selector automatically, with an independent
decision per item. There is no additional Batch activation switch. Selected items run
individually rather than in upstream microbatches; see the
[Batch tradeoff and rollout notes](../features/batching.md#model-selectors-in-chat-batches).
Whole-feature production quality and savings still require representative operator evidence.

PR 5 adds no settings, migrations, pools or deployment topology. Architectural
ownership and capacity notes are in the [admin design](../project/model-router-admin-design.md).
