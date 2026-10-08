# External customer sign-in

The Console owns Clerk sign-in. DeltaLLM accepts a short assertion from the trusted Console backend and returns an opaque gateway session. The Console stores that session in an encrypted server-side vault. The browser receives a Console session reference cookie.

External sign-in is off by default. Apply the migrations and deploy the gateway to all API replicas before enabling an integration. This feature does not change payment entitlement, budgets, prices, team blocking, or application keys.

## Configure the gateway

```yaml
general_settings:
  audit_ingestion_mode: outbox
  audit_ingestion_worker_enabled: true
  cache_invalidation_worker_enabled: true
  api_key_auth_cache_ttl_seconds: 60
  external_auth:
    enabled: true
    deployment_protocol: external_customer_v1
    allowed_origins: [https://console.example.com]
    trusted_proxy_cidrs: []
    integrations:
      - integration_id: console-production
        issuer: https://console.example.com
        audience: deltallm-production
        identity_issuer: https://your-clerk-instance.example.com
        keys:
          - kid: console-2026-10
            public_key: os.environ/CONSOLE_SIGNING_PUBLIC_KEY
  ui_mount:
    mount_path: /gateway
    external_console: true
```

Use the exact Clerk instance issuer, including its path and trailing slash if present. The signer issuer and Clerk identity issuer are separate values. Use RSA keys of at least 2,048 bits and RS256. Keep private signing keys in the Console. The gateway loads public keys at startup; it does not fetch JWKS per request.

Only trust proxy address ranges that your ingress controls. An empty list ignores forwarded client addresses. Do not use `0.0.0.0/0` or `::/0`. The Console must validate browser Origin and CSRF, then forward the approved Origin on unsafe customer requests. The exchange and signed revocation endpoints accept no Cookie or Origin headers.

The startup-only JSON environment alternatives are `DELTALLM_EXTERNAL_AUTH` and `DELTALLM_UI_MOUNT`. Explicit YAML takes precedence. Public keys and trust details are excluded from the generic settings API. A restart is required for trust or UI mount changes.

Enabled external auth requires PostgreSQL, Redis, a healthy durable audit worker, and the cache invalidation worker. Startup fails if these requirements are not met. Redis, database, audit, queue, or crypto failure returns unavailable. It never enables master access.

## Register a customer

Use a management credential in the existing provisioning service. Keep it separate from customer traffic.

1. Provision the customer's organization and team through the existing provisioning workflow.
2. Register the pair with `PUT /ui/api/external-auth/integrations/{integration_id}/bindings/{external_customer_id}`. The body is `{"organization_id":"...","team_id":"..."}`.
3. Store the returned `binding_id` in the Console's trusted customer record. A retry with the same pair is idempotent. A different pair returns conflict.
4. Enable the configured integration with `PUT /ui/api/external-auth/integrations/{integration_id}` and `{"enabled":true,"expected_version":0,"reason":"Console rollout approved"}`. Use the current version for later changes.

Database integration rows start disabled. Configuring a public key does not enable them. Binding tenant IDs are immutable. Suspend and resume operations require an operator, a reason, and the current version.

First sign-in creates an ordinary dedicated account, one organization membership, one team membership, and a runtime user. It does not adopt an account by email. An email collision requires explicit operator approval with a fresh `gateway_account_link` assertion at `/ui/api/external-auth/subjects/link`. Existing runtime users require the same type of approval at `/ui/api/external-auth/subjects/runtime-user-binding` with `gateway_runtime_identity_bind`. Both calls include a bounded `approval_reference`, `reason`, `expected_version`, and `binding_id`.

A removed membership stays removed. Further sign-ins do not reset roles, update account email, or resume suspended access. A linked account retains local MFA and forced-password-change requirements. MFA verification carries forward only within the same live parent and while the stored factor is unchanged.

## Customer access

The session permits work in its bound organization and team. It can create private credentials and models, share eligible assets within that workspace, view its own usage, and manage its own keys under the current team self-service policy. It cannot create public grants, change budgets or owners, rotate keys, or administer other customers.

Team lists and detail reads return only the registered team, including when other teams use the same organization. Organization-wide member lists and member searches require an operator session. Stored administrator membership roles do not override the customer permission ceiling.

Batch administration and batch create-session routes require an operator session. External customer sessions cannot read batch records, prompts, responses, costs, or scheduler details, or run batch maintenance actions. A scope request with a required permission fails with HTTP 403 when the customer has no live grant for that permission.

An external session cannot create a local gateway password. A linked account with an existing local password can change it only after gateway MFA verification, when required, and proof of the current password. A password change must not create an independent login method for a new Console customer.

The browser UI uses `auth_mode=session`. `/auth/me` adds `session_source=external_customer`, `workspace`, and `expires_at`. A customer session plus any master credential is denied. The Console UI has no master-login fallback.

The Playground requires an active API key owned by the customer and assigned to the exact bound runtime user and team. The Console validates the choice with `POST /auth/external/inference-key`, stores the raw key encrypted, and sends normal API-key inference requests. A gateway browser session does not authorize inference. See the [Console connection contract](../guides/console-gateway-connection.md).

Logout closes the external parent and all child sessions. It does not revoke application keys. Subject or binding suspension also leaves application keys unchanged. Use explicit key revocation or existing inference controls when those must stop.

Integration disable, binding suspension, and subject suspension commit their state, epoch, and required audit in one short transaction. They do not scan session history. Session validation checks current epochs. Renewal also checks the epochs of the parent's last issued child, including an expired child. This durable record remains for seven days, longer than the maximum twelve-hour parent lifetime. A missing or stale record denies renewal. Resume permits a new parent; it cannot reopen an old parent. The existing cleanup worker removes historical records under the retention policy.

Key removal commits deletion, required audit, and an exact-hash invalidation record together. Its response contains `enforcement`, `invalidation_id`, and `maximum_enforcement_delay_seconds`. `pending` means the durable worker must finish cache enforcement; the configured upper bound is 61 seconds with a 60-second cache lifetime. The UI displays that pending state and polls `GET /ui/api/key-revocations/{invalidation_id}`. The endpoint permits only the requesting account or an authorized operator and returns no key hash.

Organization deletion or deletion of a mapped account, identity, runtime user, or team suspends external access and revokes parents and children in the same database transaction. Immutable historical IDs remain in the external tables. Cancellation of organization deletion does not resume external access automatically. An operator must recheck and resume it. A deleted tenant or mapped identity cannot be resumed.

## Diagnostics, retention, and signer rotation

Use `GET /ui/api/external-auth/status` with an operator management credential. It reports protocol `external_customer_v1`, disabled/degraded/ready state, worker health, and bounded active/queued database work. It returns no trust material or customer records. Readiness checks Redis and the completed ledger entries for all seven external-auth migrations; a missing or rolled-back migration prevents enabled startup. Public liveness contains no customer diagnostics.

Monitor fixed-label external-auth request, denial, saturation, duration, cleanup backlog/age, and key-revocation-delay metrics. A backlog sample is capped at the configured batch size plus one; an oldest-age increase requires investigation. Admitted invalid-signature denials use required sanitized audit, globally limited to 60 records per minute. Oversized bodies and ingress overload use counters. Assertions, sessions, raw API keys, email, and external session IDs are never audit content.

Replay claims retain at least 15 minutes, expired child-session metadata seven days, and parent tombstones at least 30 days beyond expiry. Mappings remain durable. One database lease coordinates indexed cleanup across API replicas. The default batch of 1,000 per five seconds permits 200 rows/second per record kind. Configuration requires at least 25% nominal headroom over the aggregate configured integration rate. Qualify sustained insertion, backlog, indexes, vacuum, and audit throughput against the actual production database before enabling all customers.

Deploy new public keys to every replica before the Console switches its `kid`. Keep the previous key through the assertion lifetime plus clock allowance and in-flight request deadline. Normal key rotation preserves identity mappings. Issuer, audience, identity issuer, or workspace changes require explicit trust migration with access disabled and old sessions revoked. On signer compromise, disable the integration and revoke all parents/children before rotation and re-enablement.

The Console must check Clerk on every proxied request and durably retry verified revocation delivery. During a gateway revocation-delivery outage, an already issued child can remain valid for at most 300 seconds; the Console must stop local proxy access immediately. This bound applies to browser sessions. Application keys require explicit key revocation or existing inference blocking.

The production image runs as UID/GID 10001. Mount configuration and required secret references with permissions readable by that identity; use protected backend secret storage for signing and vault keys.

API-key cache read failure or invalid lookup data uses the bounded primary-database check. A cache write outage after a successful primary check does not reject that request or create a local positive cache. A conflicting revocation marker, invalid atomic-fill result, or expired fill deadline still denies authorization. Monitor `deltallm_key_auth_cache_failures_total` with its fixed `reason` labels for cache degradation.

An additional canonical production-image CI check is supplied in `plans/issue-344-ci-image-build.patch`. A maintainer with workflow-write permission must apply it to activate the image build, non-root runtime, built UI, and external-auth POST route checks. The patch also makes the required `test` check depend on the image job. Until it is applied, the current CI does not certify the production image. Connected staging and deployment capacity tests remain required.

## Deployment and rollback

External auth adds a four-connection primary database pool to each API process. The slots reserve two mutations, one validation, and one maintenance operation. At 15 API pods, including rolling surge, the extra reservation is 60 connections. Include the main pool, telemetry pool, workers, migrations, monitoring, and other database clients in the total database budget.

The Helm chart checks `externalAuthCapacity.maximumApiPods`, `otherReservedConnections`, and `postgresConnectionBudget` when the feature is enabled. The budget defaults to zero and must be certified for your deployment. Keep the feature off on batch workers. When using a separate batch worker role, set `api.config.general_settings.cache_invalidation_worker_enabled: true` explicitly; the split-worker default would otherwise disable it on the API. The capacity check includes worker peak replicas, rolling surge, and configured main/telemetry pool overrides. Reserve additional headroom for other clients and never size the database from the incremental 60-connection example alone.

Stage enablement in this order:

1. Apply all additive migrations with integrations disabled.
2. Deploy the new binary to every API replica. Verify readiness and the deployment protocol, Redis behavior, audit delivery, and database budget.
3. Deploy the matching Console signer, vault, proxy, renewal, key selection, and logout flows.
4. Run the customer flow and failure tests listed in the Console contract.
5. Enable one test binding, then the approved production integration.

Before reverting to an older binary, stop Console exchange and renewal, disable integrations, and revoke **all** external parents and child rows in `deltallm_platformsession`. Older binaries know the child table but do not check external epochs. Retain the additive tables, subject associations, parent tombstones, and audit rows. Reconcile every exact key hash retained in revocation outbox records against both `key:v5:` and the previous `key:v4:` namespace before old replicas receive traffic. Do not use a broad Redis key scan as a substitute.

The release requirements for [issue 344](https://github.com/deltawi/deltallm/issues/344) requires deployed Console and gateway end-to-end evidence before production enablement. Local gateway tests cannot establish that evidence.

Use `scripts/external_auth_rollback.py` with the deployment's `DATABASE_URL` and `REDIS_URL` to preview rollback counts. After stopping Console exchange, renewal, customer traffic, and new key mutations, run it with `--apply --approval-reference <approved-change-or-incident>`. Each database mutation page commits required audit with the revocation. Keep the audit worker running until those records are delivered. The command is idempotent and retains the additive schema. It returns bounded samples and progress counts only. Samples are capped at 1,001; zero means no matching live row. Revocation and exact-hash reconciliation use indexed pages. A database or Redis failure aborts the command; keep old traffic stopped and retry. Verify zero live parents, zero live children, and zero enabled integrations before restoring old traffic. Wait at least 61 seconds after the last old/new key-auth reader was stopped if its cached snapshot can still be in flight.
