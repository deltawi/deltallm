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

Use draft versions for changes, then publish when ready. This prevents accidental live changes while an admin is still editing model access, pricing, or limits.

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

## Recommended User Journey

1. Open **AI Gateway > Tiers**
2. Create a tier, such as `Starter`, `Growth`, or `Enterprise`
3. Create or edit a draft version
4. Add model policies for the models in that package
5. Set prices, RPM, TPM, and capacity pools per model
6. Publish the version
7. Open an organization detail page
8. Assign the tier to the organization
9. Review the effective policy preview
10. Run a simulation for an example request

The preview answers: what can this organization actually use?

The simulation answers: would this request be allowed, what would it cost, and which limit would block it?

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

Use draft versions for every meaningful change. Publish only after previewing the tier and simulating representative requests.

## Pricing by Model Type

Pricing is not always token pricing. Choose the pricing shape that matches the model.

| Model type | Common pricing fields | Example |
| --- | --- | --- |
| Chat | Input token, output token, cached token, batch token | `0.000001/input token`, `0.000003/output token` |
| Embeddings | Input token | `0.00000002/input token` |
| Image generation | Image price, request price | `0.04/image` |
| Text-to-speech | Character, audio token, second, or request price | `0.00005/character` |
| Transcription | Second, audio token, or request price | `0.00006/second` |
| Rerank | Token or request price | `0.000001/input token` or `0.01/request` |

Use token pricing for chat and embeddings. Use image pricing for image models. Use character pricing for most text-to-speech models when the provider bills by input text length. Use second pricing for transcription when the provider bills by audio duration.

Flat request pricing is useful when the upstream provider charges per request or when you sell a fixed internal package.

The Tier editor keeps advanced supported pricing fields available, but only the common fields for the selected pricing profile are shown first. Existing hidden pricing values are preserved unless you clear them.

## Important Rules

- A tier can define the organization's model package.
- Tiers do not replace organization, team, user, or key rate and budget limits.
- Direct team, API key, and runtime user restrictions can still narrow tier access.
- Deny or restriction rules should be treated as hard boundaries.
- Published versions affect assigned organizations.
- Draft versions are for editing and review.
- Shared capacity pools protect the platform when several organizations burst at the same time.

## Practical Example

You want to sell a Growth plan:

- `gpt-4o-mini` is available with high limits
- `gpt-4o` is available with lower limits
- `gpt-4o` uses `growth-premium-pool`
- customer price is lower than pay-as-you-go

Create one `Growth` tier, publish it, and assign it to every Growth customer.

When a customer upgrades to Enterprise, assign the Enterprise tier instead of manually changing model access, prices, and limits on that organization.

## Related Pages

- [Models](models.md)
- [Organizations](organizations.md)
- [Usage & Spend](usage.md)
- [Rate Limiting](../features/rate-limiting.md)
