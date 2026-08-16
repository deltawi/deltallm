# Tiers

Tiers are reusable model packages for organizations.

A tier answers:

- which models an organization can use
- what customer price applies for those models
- what RPM and TPM limits apply
- which shared capacity pool protects scarce model capacity

Use tiers when you want to manage customer plans such as `starter`, `growth`, or `enterprise` without configuring every organization one by one.

## Simple Mental Model

Think of a tier as a plan.

Example plans:

| Tier | Models | Limits | Pricing | Best for |
| --- | --- | --- | --- | --- |
| Starter | Low-cost models only | Low RPM and TPM | Standard pricing | Trials and small customers |
| Growth | Low-cost models plus selected premium models | Medium RPM and TPM | Discounted or custom pricing | Paying teams |
| Enterprise | Approved premium models | High RPM and TPM | Contract pricing | Large customers |

Instead of editing every organization separately, create the tier once and assign it to organizations.

## Main Concepts

### Tier

The package itself.

Example: `Growth`.

### Tier Version

The editable definition of the tier.

Use draft versions for changes, then activate one when ready. This prevents accidental live changes while an admin is still editing model access, pricing, or limits.

### Version lifecycle in the admin UI

The Tiers catalog shows compact status badges that describe the actual lifecycle state:

| Badge | Meaning |
| --- | --- |
| **Live** | The tier has an active version used by assignments |
| **Draft** | The tier has an editable version that is not live |
| **Live** + **Draft** | An active version is serving traffic while a separate draft is being prepared |

There is no separate “work in progress” state. Disabled is an availability state and is shown independently from the version badges.

Open a tier to work in its version workspace:

- The version rail keeps the live version, drafts, and paginated archive history visible while you switch between **Models & limits**, **Pricing**, and **Capacity pools**.
- Active and archived versions are immutable. Choose **New draft** to clone the active version, or **Restore as draft** to clone an archived version into a new editable version. Restoring never rewrites history or moves the live pointer.
- When more than one draft exists, choose one explicitly. The UI shows its creator, update time, and source version instead of silently opening another admin's work.
- Activating a draft first shows a change preview and assignment impact. The activation is accepted only if both the draft revision and active-version pointer still match the preview.
- If another admin changes the same draft, the save is rejected and the editor keeps the unsaved fields open for comparison with the latest server values.

Tier creation creates the tier and Draft v1 atomically. Retrying the same submit after a lost response reuses the same idempotency key and returns the original tier instead of creating a duplicate.

The catalog, model policies, pricing rows, capacity pools, and archived versions use server-backed pagination. Page-size controls are bounded, filters reset to the first page, and the capacity-pool picker performs a bounded lookup for the selected callable rather than loading the entire pool catalog.

### Model Policy

The rule for one model inside a tier.

For each model, choose:

- whether the model is allowed
- the customer-facing price
- RPM and TPM limits
- optional batch and cache pricing
- optional capacity pool

Example `Growth` tier model policy:

| Model | Allowed | RPM | TPM | Capacity pool |
| --- | --- | ---: | ---: | --- |
| `gpt-4o-mini` | Yes | 1000 | 1,000,000 | None |
| `gpt-4o` | Yes | 100 | 250,000 | `growth-premium-pool` |
| `claude-opus` | No | - | - | - |

### Organization Assignment

The link between an organization and a tier.

Example:

- Organization: `Acme`
- Primary tier: `Growth`
- Optional add-on tier: `GPT-4o Launch Promo`

The primary tier is the normal package. Add-on tiers are useful for exceptions without creating a custom tier for every organization.

An enabled assignment whose end time is still in the future, including a scheduled assignment that has not started yet, can only reference an enabled tier with an active version. Before disabling a tier, disable or end all of its live and scheduled assignments. Disabled or expired assignments remain as history and may continue to reference a disabled tier.

### Creating Organizations

When tier policy is enabled, the organization creation drawer starts with the service tier. The selected primary assignment follows the tier's active version by default. DeltaLLM creates the organization, primary assignment, and cache-invalidation outbox record in one database transaction, so an assignment failure cannot leave a partially created organization.

Creation behavior follows the effective runtime mode:

| Mode | New organization behavior |
| --- | --- |
| `enforce` | A primary tier is required |
| `shadow` | A primary tier is recommended by default; its allowed models are mirrored into legacy Asset Access, and an explicit legacy migration exception remains available |
| `disabled` | Legacy direct Asset Access remains available |

Use a custom tier or clone an existing tier when one customer needs different model access, model limits, pricing, or capacity. Do not recreate that policy with organization fields. While an active tier is authoritative in `enforce`, the API rejects new organization-level per-model limit maps and organization Asset Access writes; organization-wide RPM/TPM/RPH/RPD/TPD hard caps remain editable.

If an organization already had legacy per-model RPM/TPM maps before its tier was assigned, its Service Policy card shows a warning because those safety caps still apply alongside the tier. First reproduce any required limits on the tier, preview and activate them, then use **Clear legacy model caps**. The confirmation warns that clearing them before the tier is ready can increase allowed traffic.

`shadow` evaluates the staged tier but does not enforce it. For a newly created tier-first organization, DeltaLLM atomically snapshots the selected active version's allowed callable targets into legacy Asset Access so requests continue to work. That legacy mirror remains editable and authoritative during rollout; it intentionally does not follow later tier activations, allowing the preview and mismatch telemetry to expose policy changes before enforcement. Creating a new legacy organization through the API in this mode requires `"legacy_policy_exception": true`, matching the explicit migration checkbox in the drawer. `disabled` also keeps legacy Asset Access authoritative even if an assignment has already been staged. Once mode is `enforce`, the tier becomes authoritative and the organization Asset Access editor is hidden.

The optional organization RPM, TPM, RPH, RPD, and TPD fields are global hard caps. They apply across all models, teams, and keys in addition to the tier's per-model controls. Leave them blank when no extra organization-wide ceiling is needed. Budgets, budget resets, and audit-content storage also remain organization settings.

## Tiers and Asset Access

Tiers and Asset Access answer different questions.

| Control | Main question | Typical use |
| --- | --- | --- |
| Tier assignment | What model package is this organization on? | Plans such as `starter`, `growth`, or `enterprise` |
| Asset Access | Which runtime scope is allowed to use which callable target? | Organization bootstrap, route groups, and team/key/user restrictions |

For model access, an active tier assignment can define the organization's model package. This is useful when you want tiers to replace manual per-organization model grants.

Team, API key, and runtime user Asset Access can still narrow the tier package. They do not expand it.

Simple rule:

1. If the organization has no active tier policy, normal Asset Access controls model visibility.
2. If the organization has an active tier policy, the tier's model policies define the organization's model package.
3. Direct team, API key, and runtime user restrictions are applied on top of the tier package.
4. Deny rules win.

Examples:

| Tier allows | Lower-scope Asset Access | Final access |
| --- | --- | --- |
| `gpt-4o-mini`, `gpt-4o` | No team/key/user restriction | `gpt-4o-mini`, `gpt-4o` |
| `gpt-4o-mini`, `gpt-4o` | Team restricted to `gpt-4o-mini` | `gpt-4o-mini` only |
| `gpt-4o-mini` | API key restricted to `gpt-4o` | No model access |
| Tier denies `gpt-4o` | Asset Access grants `gpt-4o` | Denied |
| No active tier policy | Organization Asset Access grants `gpt-4o-mini` | `gpt-4o-mini` |

Use tiers for organization-level product packaging. Use Asset Access for route groups and for narrower team, key, or user controls.

## Capacity Pools

A capacity pool is a shared bucket of usage.

Use it when many organizations share the same scarce or expensive model capacity.

Imagine a premium model where your provider only gives you:

- `1,000 RPM`
- `2,000,000 TPM`

Without a capacity pool, you might accidentally promise too much:

| Organization | Org limit |
| --- | ---: |
| Org A | 500 RPM |
| Org B | 500 RPM |
| Org C | 500 RPM |

Total possible usage is `1,500 RPM`, but the provider only gives you `1,000 RPM`.

With a capacity pool:

| Scope | Limit |
| --- | ---: |
| Org A | 500 RPM |
| Org B | 500 RPM |
| Org C | 500 RPM |
| Shared pool | 1,000 RPM |

Now each organization has its own limit, but all Growth organizations together still stay under the shared provider capacity.

A simple analogy:

- organization model limit = each apartment's tap
- capacity pool = the building's main water pipe

Both limits matter. A request is allowed only if the organization has room and the shared pool has room.

Use capacity pools for:

- premium models
- scarce provider quota
- GPU-backed models
- limited concurrency
- shared enterprise or growth capacity

Do not create pools for every cheap/default model. For common models, organization-level RPM and TPM limits are usually enough.

### Capacity Pool Strategies

| Strategy | Behavior | Typical use |
| --- | --- | --- |
| `hard_cap` | Enforces only the shared pool RPM, TPM, and parallel-request ceilings | A simple provider or GPU quota |
| `weighted_fair` | Lets active organizations borrow idle capacity below saturation, then applies a share based on assignment weight | Shared premium capacity where every active customer must make progress |
| `reserved_burst` | Uses weighted fair sharing and multiplies each organization's saturated share by the configured burst multiplier; the pool hard cap still wins | Plans that include a bounded burst entitlement |

For weighted strategies, `saturation_threshold` is the pool utilization ratio above which per-organization shares begin to apply. It defaults to `0.85`. At or below that point an organization can borrow otherwise-idle capacity. Above it, the runtime calculates:

```text
organization share = pool limit * effective organization weight / total active weight
```

`reserved_burst` then multiplies that share by `burst_multiplier`. The shared pool limit is never increased, so a burst cannot exceed the provider-facing hard cap.

An organization is active for 10 seconds by default after its latest request to that pool. Operators can tune this from 1 to 300 seconds with `tier_capacity_fair_share_active_ttl_seconds`. The weight comes from its effective tier assignment. RPM and TPM are tracked independently in their existing 60-second windows; parallel-request capacity remains a hard cap.

A fair-share denial returns a normal `429` with scope `tier_pool_fair_share_rpm` or `tier_pool_fair_share_tpm`. The `reason` distinguishes `weighted_share_exceeded` from the absolute `pool_capacity_exceeded` ceiling.

Static `hard_cap` pools also publish allowed and denied capacity metrics, saturation, and dashboard limit-hit heatmap entries. A rejected request is recorded atomically with its decision and does not consume RPM or TPM counter capacity.

Prometheus capacity metrics aggregate by pool, model, tier, scope, and outcome and intentionally omit organization IDs. Use the capacity dashboard for bounded per-organization diagnostics.

### Capacity Operations

Platform admins can inspect current pool utilization and temporarily boost one organization's effective weight:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/tier-capacity/dashboard` | Pool saturation, active organizations, top consumers, boosts, and a limit-hit heatmap |
| `POST` | `/ui/api/tier-capacity/boosts` | Apply a `1`-to-`100` weight multiplier with a Redis TTL of at most seven days |
| `DELETE` | `/ui/api/tier-capacity/boosts` | Remove a temporary boost |

Boost creation and deletion are written to the audit log and attributed to the affected organization. A boost is runtime state: it expires automatically and does not modify the active tier version. See the [Organization Tiers Rollout Runbook](../deployment/organization-tiers-rollout.md) for API examples, metrics, and troubleshooting.

The dashboard reports `live_data.status` as `healthy`, `partial`, or `unavailable`. When Redis cannot supply a live section, its numeric values are `null` and the UI shows an em dash; they are never presented as zero. The active pool configuration remains available from the tier snapshot.

## Recommended User Journey

1. Open **AI Gateway > Tiers**
2. Create a tier, such as `Starter`, `Growth`, or `Enterprise`
3. Create or choose a draft version
4. Add model policies for the models in that package
5. Set prices, RPM, TPM, and capacity pools per model
6. Review the activation preview and activate the draft
7. Create an organization and select the active tier, or open a legacy organization and assign it from **Service Policy**
8. Add only the optional organization-wide budget and hard-cap guardrails that are needed
9. Review the effective policy preview
10. Run a simulation for an example request

The preview answers: what can this organization actually use?

At runtime, DeltaLLM applies prompt templates, pre-call callbacks, and guardrails before it validates the final request and enforces tier model access, budgets, and rate limits. These transformations run once per request, including cacheable requests, so a rewritten model or prompt cannot bypass policy or use a stale cache identity. Streaming and cached responses retain any parallel-request lease until their final response body is sent.

The simulation answers: would this request be allowed in an empty rate-limit window, what would it cost across the configured routes, and which tier, capacity-pool, legacy model, or organization hard cap would block it? Select the workload type that matches the configured routes, then enter the relevant usage: input and output tokens for chat; input tokens for embedding or rerank; input and generated images for image generation; text tokens, characters, audio tokens, or seconds for speech; and text tokens, audio tokens, or seconds for transcription. Embedding and rerank simulations reject non-zero completion tokens instead of counting an incompatible usage dimension.

The calculated price has three states:

- **Available**: every configured route has applicable pricing. The result is exact when all candidates agree and a range when their prices differ.
- **Partial**: at least one route can be priced and at least one cannot. The displayed exact value or range covers only the priced routes and is not a complete route quote.
- **Unavailable**: no reliable quote can be produced. Typical reasons include no configured routes, no applicable pricing, missing workload usage, mixed route workload types, or a selected workload type that does not match the routes.

An explicit zero price remains an available `$0` quote when it matches the supplied usage unit. An absent price is not treated as zero. For known token models without a regular deployment or tier token override, the built-in model catalog price is used and reported with source `default`; cache-only or batch-only metadata does not replace the regular sync quote. Once a regular input or output override is configured, every token dimension used by the simulation must resolve from that configured pricing chain, so an incomplete override is reported as unpriced instead of mixing it with the catalog. Unknown token models without complete applicable pricing are unavailable. Configure an explicit zero in the model or tier pricing when a route is intentionally free.

When the callable model is an alias, catalog fallback uses the resolved provider model. Runtime spend metadata records both names so operators can reconcile customer policy against provider cost without exposing the provider name as the public callable target.

`pricing_sources` lists only sources that contributed fields to the calculated amount and can contain more than one value when tier and deployment fields are combined. Small positive token prices are displayed with additional decimal precision so they are not mistaken for zero.

Displayed amounts are totals for the requested `request_count`. The quote contract also returns `per_request_amount`, or per-request minimum and maximum values for a range, so callers do not have to infer whether an amount is aggregate. For image requests with `input_images > 0`, an input-image price must resolve; an output-only image price is not silently applied as a zero input price.

Transcription quotes apply the same provider billing rules as live traffic. For example, a provider minimum billable duration can make the quoted duration cost higher than the raw audio length, and routes using different providers can produce a range. The simulation does not query live routing health, current Redis counters, fair-share activity, or in-flight requests.

## Efficient Tier Design

Start with a small number of base tiers:

- `starter`
- `growth`
- `enterprise`

Use add-on tiers for exceptions:

- temporary premium model access
- launch discounts
- customer-specific model access
- short-term capacity boosts

Keep capacity pools focused on scarce capacity. A tier can have many model policies, but only premium or constrained models usually need a pool.

Use draft versions for every meaningful change. Activate only after previewing the tier and simulating representative requests.

## Pricing by Model Type

Pricing is not always token pricing. Choose the pricing shape that matches the model.

| Model type | Common pricing fields | Example |
| --- | --- | --- |
| Chat | Input token, output token, cached token, batch token | `0.000001/input token`, `0.000003/output token` |
| Embeddings | Input token | `0.00000002/input token` |
| Image generation | Generated output image, optional input image, request price | `0.04/output image` |
| Text-to-speech | Text token, character, audio token, second, or request price | `0.00005/character` |
| Transcription | Text token, second, audio token, or request price | `0.00006/second` |
| Rerank | Token or request price | `0.000001/input token` or `0.01/request` |

Use token pricing for chat and embeddings. For image generation, `output_cost_per_image` prices generated images; `input_cost_per_image` prices explicit input images and remains the generated-image fallback for existing configurations. Use character pricing for most text-to-speech models when the provider bills by input text length. Use second pricing for transcription when the provider bills by audio duration.

Flat request pricing is useful when the upstream provider charges per request or when you sell a fixed internal package.

The Tier editor keeps advanced supported pricing fields available, but only the common fields for the selected pricing profile are shown first. Existing hidden pricing values are preserved unless you clear them.

## Important Rules

- A tier can define the organization's model package.
- Tiers do not replace organization, team, user, or key rate and budget limits.
- Direct team, API key, and runtime user restrictions can still narrow tier access.
- Deny or restriction rules should be treated as hard boundaries.
- The active version affects assigned organizations.
- Draft versions are for editing and review.
- Shared capacity pools protect the platform when several organizations burst at the same time.

## Practical Example

You want to sell a Growth plan:

- `gpt-4o-mini` is available with high limits
- `gpt-4o` is available with lower limits
- `gpt-4o` uses `growth-premium-pool`
- customer price is lower than pay-as-you-go

Create one `Growth` tier, activate it, and assign it to every Growth customer.

When a customer upgrades to Enterprise, assign the Enterprise tier instead of manually changing model access, prices, and limits on that organization.

## Related Pages

- [Models](models.md)
- [Organizations](organizations.md)
- [Usage & Spend](usage.md)
- [Rate Limiting](../features/rate-limiting.md)
- [Organization Tiers Rollout](../deployment/organization-tiers-rollout.md)
