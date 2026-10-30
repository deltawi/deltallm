# Choose What to Turn On

DeltaLLM starts with every optional feature off and safe built-in defaults.
This page lists the settings that turn features on, what each one needs, and three ready-made profiles.
Pick the profile closest to your instance, copy its file, then turn on single features from the table.

Every setting you leave out of your config uses its built-in default.
The [complete settings reference](../configuration/general-settings-reference.md) lists all of them.

## Pick a profile

| Profile | Use it for | Start from |
|---|---|---|
| Starter | A laptop, a trial, or a first server | [`config.example.yaml`](https://github.com/deltawi/deltallm/blob/main/config.example.yaml) with Docker Compose `--profile single`, or the Helm chart with `values-eval.yaml` |
| Small team | One server or a one-replica Helm release serving a team, with the Model Selector, Realtime, and the Batch API available | [`config.small-team.example.yaml`](https://github.com/deltawi/deltallm/blob/main/config.small-team.example.yaml) |
| Production | Several replicas behind a load balancer, with external PostgreSQL and Redis | Helm with `values-production.yaml`; see [Kubernetes](kubernetes.md) and the [production checklist](production-checklist.md) |

The Production profile has optional strict checks. Add `values-production-strict.yaml` after `values-production.yaml` (and an overlay with your real provider quotas) and the chart refuses to render a production release unless ingress admission, preflight capacity, both outboxes, spend operation intents or accounting v2, `redis_degraded_mode: fail_closed`, `budget_enforcement_query_mode: combined`, `model_deployment_source: db_only` without config bootstrap, and external migrations are all set, and it checks the declared database, Redis, file-descriptor and provider capacity. Without the strict overlay these settings are the same, but nothing stops a release that changes them.

## Feature switches

"Default" is the built-in value.
A profile column shows the value that profile's file sets, or "default" when it keeps the built-in value.
"Restart" is "Yes" when the gateway reads the setting only when a process starts, or refuses to change it while running.
Settings marked "No" take effect when you change them from the Admin UI or the config API.
"Billing" says whether the setting changes what is charged, or how charges are written.

<!-- feature-switches:start (generated; edit scripts/docs/feature_switches.yaml) -->

| Feature | Setting | Default | Starter | Small team | Production (Helm) | Restart | Billing | Needs |
|---|---|---|---|---|---|---|---|---|
| Provider health checks | `background_health_checks` | off | on | on | on | Yes | No | Nothing. Calls each provider's model list, no tokens. |
| [Where models come from](../configuration/models.md) | `model_deployment_source` | `hybrid` | `db_only` | `db_only` | `db_only` | No | No | `db_only` manages models in the Admin UI; production requires it with `model_deployment_bootstrap_from_config: false`. |
| [Durable spend writes (outbox)](telemetry-ingestion-rollout.md) | `spend_ingestion_mode` | `legacy` | default | `outbox` | `outbox` | Yes | Changes how charges are written, not amounts | 10 more PostgreSQL connections per process and its worker. Required by the Model Selector and Realtime. |
| [Durable audit writes (outbox)](telemetry-ingestion-rollout.md) | `audit_ingestion_mode` | `legacy` | default | `outbox` | `outbox` | Yes | No | Same telemetry pools as spend outbox. Required by external customer sign-in. |
| [Spend operation intents](spend-recovery.md) | `spend_operation_intents_enabled` | off | default | default | on | Yes | Changes how charges are written, not amounts | Spend outbox and its worker. |
| [Accounting v2](accounting-v2.md) | `accounting_protocol_enabled` | off | default | default | default | Yes | Replaces how charges are written | Accounting worker role in Helm. |
| [Response cache](../features/caching.md) | `cache_enabled` | off | default | default | default | No | Yes, a hit is charged like a miss today | `cache_backend: redis` with more than one process. |
| [Response validation](../configuration/router.md#response-validation) | `router_settings.response_validation.enabled` | on | default | default | default | No | No, callers pay only for the answer they get | Nothing. Retries empty or broken answers. |
| [JSON healing](../configuration/router.md#response-validation) | `router_settings.response_validation.json_healing` | off | default | default | default | No | No | Response validation. Rewrites JSON-mode answers. |
| [Batch API](../features/batching.md) | `embeddings_batch_enabled` | off | default | on | default | Yes | Batch usage is billed like other requests | `embeddings_batch_storage_backend: s3` with more than one process or pod. |
| [Batch webhooks](batch-webhook-rollout.md) | `batch_webhook_enabled` | off | default | default | default | Yes | No | `batch_webhook_encryption_key`. |
| [Realtime sessions](../guides/realtime.md) | `realtime.enabled` | off | default | default | default | Yes | Realtime sessions are billed | Redis and spend outbox (or accounting v2). |
| [Email](../configuration/general.md#email-settings) | `email_enabled` | off | default | default | default | Yes | No | An SMTP, Resend, or SendGrid account and `email_base_url`. |
| [Budget and key notifications](governance.md) | `governance_notifications_enabled` | off | default | default | default | No | No | Email. |
| [Single sign-on](../features/authentication.md) | `enable_sso` | off | default | default | default | Yes | No | An OIDC or SAML identity provider and Redis. |
| [Password sign-in](../features/authentication.md) | `password_login_enabled` | on | default | default | default | No | No | `false` (SSO-only) requires SSO. The master key always works. |
| [External customer sign-in](../configuration/external-customer-auth.md) | `external_auth.enabled` | off | default | default | default | Yes | No | Audit outbox, cache invalidation worker, `api_key_auth_cache_ttl_seconds` ≤ 60. |
| [Organization deletion requests](../features/organization-deletion.md) | `organization_deletion_requests_enabled` | off | default | default | default | Yes | No | The deletion readiness check must pass (startup fails otherwise) and every replica must run lifecycle protocol v2. |
| [Ingress admission](ingress-admission.md) | `gateway_ingress_enabled` | off | default | default | on | Yes | No | Limits sized from a load test. |
| [Preflight capacity](ingress-admission.md) | `gateway_preflight_capacity_enabled` | off | default | default | on | No | No | Redis. |
| Behavior when Redis is down | `redis_degraded_mode` | `fail_open` | default | default | `fail_closed` | Yes | Changes limits, not prices | `fail_closed` refuses requests whose limits need Redis. |
| [Budget checks](budget-prompt-performance.md) | `budget_enforcement_query_mode` | `legacy` | default | default | `combined` | No | Changes how budgets are enforced | Run `shadow` first and check for mismatches. |
| [Organization tiers](organization-tiers-rollout.md) | `tier_policy_mode` | `disabled` | default | default | default | Yes | Changes limits, not prices | Tiers defined in the Admin UI; start with `shadow`. |
| [Database migrations](database-migrations.md) | `migration_mode` | `startup` | default | default | `external` | Yes | No | `external` needs a migration Job before each rollout. |

<!-- feature-switches:end -->

## Before you turn on

**Durable spend and audit writes (outbox).**
Each process opens two more PostgreSQL pools (`telemetry_db_pool_size` and `telemetry_worker_db_pool_size`, 5 connections each by default).
A process then needs about 38 connections instead of 28.
Check PostgreSQL `max_connections` for every process you run, plus one restart overlap, before switching.
See [Telemetry ingestion rollout](telemetry-ingestion-rollout.md).

**Model Selector.**
The selector is configured per route group, not with a global switch.
It needs the spend outbox and its worker, model capabilities, and token prices on the classifier.
Customers pay the classifier's provider cost.
See [Router settings](../configuration/router.md).

**Batch API with more than one process.**
`local` artifact storage only works when one process serves the API and runs the batch worker.
Set `embeddings_batch_storage_backend: s3` before you add a replica or split the batch worker.
The Helm chart refuses a split batch worker with local storage.

**SSO-only sign-in.**
`password_login_enabled: false` needs working SSO.
Keep the master key somewhere safe: it always signs in, even when SSO is down.

**MCP tool approvals.**
MCP servers and tool approval rules are set per server and per tool in the Admin UI, not in the config file.
A new tool needs no approval until you set one.
See [MCP](../features/mcp.md).

**Retries and cooldowns.**
The profiles use `num_retries: 1` and `allowed_fails: 2`.
`allowed_fails: 0` takes a deployment out of rotation on its first rate-limit answer, which stops all traffic for a model with a single deployment for `cooldown_time` seconds.
See [Router settings](../configuration/router.md).

## Helm values

The chart's `values.yaml` keeps only the feature switches, connection pools, and accounting settings under `config.general_settings`.
Every other setting is rendered from `files/config-defaults.yaml` in the chart, which holds the built-in defaults.
Each role's ConfigMap still lists every setting explicitly, so an image upgrade cannot change capacity or timeouts behind a running release.
To change any of them, set it under `config.general_settings` in your own values file; your value wins.
