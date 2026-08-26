# General Settings

The `general_settings` section configures authentication, database connections, email delivery, SSO, governance notifications, caching, and platform-level options.

## Recommended Starter Shape

The docs use `config.example.yaml` as the starter config. The intended pattern is:

- keep the active settings minimal
- source secrets from environment variables
- leave advanced features commented until you need them

Minimal starter example:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
  redis_url: os.environ/REDIS_URL
  platform_bootstrap_admin_email: os.environ/PLATFORM_BOOTSTRAP_ADMIN_EMAIL
  platform_bootstrap_admin_password: os.environ/PLATFORM_BOOTSTRAP_ADMIN_PASSWORD
  auth_session_ttl_hours: 12
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
  governance_notifications_enabled: false
  budget_notifications_enabled: false
  key_lifecycle_notifications_enabled: false
```

## Full Reference

```yaml
general_settings:
  instance_name: DeltaLLM
  ui_branding:
    primary_color: "#5B50D6"
    secondary_color: "#8B7CFF"
    menu_hover_color: "#F7F5FF"
  master_key: os.environ/DELTALLM_MASTER_KEY
  deltallm_key_header_name: Authorization
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
  db_pool_size: 20
  db_pool_timeout: 30
  spend_reporting_max_concurrency: 2
  spend_reporting_global_max_concurrency: 2
  spend_reporting_queue_timeout_seconds: 10
  spend_reporting_execution_timeout_seconds: 60
  spend_reporting_redis_timeout_seconds: 0.5
  spend_reporting_v2_enabled: false
  upstream_http_connect_timeout_seconds: 10
  upstream_http_read_timeout_seconds: 300
  upstream_http_write_timeout_seconds: 30
  upstream_http_pool_timeout_seconds: 10
  upstream_http_max_connections: 500
  upstream_http_max_keepalive_connections: 100
  upstream_http_keepalive_expiry_seconds: 60
  redis_url: os.environ/REDIS_URL
  redis_host: localhost
  redis_port: 6379
  redis_password: os.environ/REDIS_PASSWORD
  cache_enabled: false
  cache_backend: memory
  cache_ttl: 3600
  cache_max_size: 10000
  stream_cache_max_bytes: 262144
  stream_cache_max_fragments: 2048
  failover_event_history_size: 1000
  platform_bootstrap_admin_email: os.environ/PLATFORM_BOOTSTRAP_ADMIN_EMAIL
  platform_bootstrap_admin_password: os.environ/PLATFORM_BOOTSTRAP_ADMIN_PASSWORD
  auth_session_ttl_hours: 12
  invitation_token_ttl_hours: 72
  password_reset_token_ttl_minutes: 60
  api_key_auth_cache_ttl_seconds: 300
  organization_lifecycle_auth_max_staleness_seconds: 3
  organization_lifecycle_auth_cache_max_entries: 10000
  organization_deletion_recovery_window_hours: 168
  organization_deletion_max_attempts: 20
  organization_deletion_requests_enabled: false
  organization_deletion_worker_enabled: true
  organization_deletion_worker_poll_interval_seconds: 5
  organization_deletion_worker_batch_size: 5
  organization_deletion_worker_max_concurrency: 2
  organization_deletion_worker_lease_seconds: 60
  organization_deletion_worker_record_timeout_seconds: 45
  organization_deletion_worker_page_size: 100
  organization_deletion_worker_max_pages_per_claim: 10
  organization_deletion_waiting_poll_seconds: 10
  organization_deletion_retry_initial_seconds: 5
  organization_deletion_retry_max_seconds: 300
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: false
  email_enabled: false
  email_provider: smtp
  email_from_address: no-reply@example.com
  email_reply_to: support@example.com
  email_base_url: http://localhost:4002
  email_worker_enabled: true
  email_worker_batch_size: 10
  email_worker_max_concurrency: 3
  email_worker_delivery_lease_seconds: 60
  email_worker_audit_lease_seconds: 30
  email_worker_startup_timeout_seconds: 5
  email_worker_shutdown_drain_timeout_seconds: 20
  email_max_attempts: 5
  email_retry_initial_seconds: 60
  email_retry_max_seconds: 3600
  smtp_host: localhost
  smtp_port: 1025
  smtp_username: os.environ/SMTP_USERNAME
  smtp_password: os.environ/SMTP_PASSWORD
  smtp_use_tls: false
  resend_api_key: os.environ/RESEND_API_KEY
  sendgrid_api_key: os.environ/SENDGRID_API_KEY
  governance_notifications_enabled: false
  budget_notifications_enabled: false
  key_lifecycle_notifications_enabled: false
  budget_alert_ttl_seconds: 3600
  enable_sso: false
  sso_provider: oidc
  sso_client_id: os.environ/SSO_CLIENT_ID
  sso_client_secret: os.environ/SSO_CLIENT_SECRET
  sso_authorize_url: https://idp.example.com/oauth2/authorize
  sso_token_url: https://idp.example.com/oauth2/token
  sso_userinfo_url: https://idp.example.com/oauth2/userinfo
  sso_redirect_uri: https://your-domain.com/auth/callback
  sso_scope: openid email profile
  sso_admin_email_list: []
  sso_default_team_id: null
  sso_state_ttl_seconds: 600
  self_registration:
    enabled: false
    mode: sso_allowed_domain
    allowed_domains: []
    require_email_verification: true
    require_admin_approval: false
    default_org:
      id: null
      name: null
      max_budget: null
      soft_budget: null
      rpm_limit: null
      tpm_limit: null
      rph_limit: null
      rpd_limit: null
      tpd_limit: null
    default_team:
      id: null
      alias: null
      role: team_developer
      max_budget: null
      soft_budget: null
      rpm_limit: null
      tpm_limit: null
      rph_limit: null
      rpd_limit: null
      tpd_limit: null
      self_service_keys_enabled: true
      self_service_max_keys_per_user: null
      self_service_budget_ceiling: null
      self_service_require_expiry: true
      self_service_max_expiry_days: null
    default_user:
      user_role: internal_user
      max_budget: null
      soft_budget: null
      rpm_limit: null
      tpm_limit: null
      rph_limit: null
      rpd_limit: null
      tpd_limit: null
  embeddings_batch_enabled: false
  embeddings_batch_worker_enabled: true
  embeddings_batch_completion_outbox_worker_enabled: true
  embeddings_batch_storage_backend: local
  embeddings_batch_storage_dir: .deltallm/batch-artifacts
  embeddings_batch_create_session_cleanup_enabled: true
  embeddings_batch_poll_interval_seconds: 1.0
  embeddings_batch_item_claim_limit: 20
  embeddings_batch_max_attempts: 3
  embeddings_batch_retry_initial_seconds: 5
  embeddings_batch_retry_max_seconds: 300
  embeddings_batch_retry_multiplier: 2.0
  embeddings_batch_retry_jitter: true
  embeddings_batch_model_group_backpressure_enabled: true
  embeddings_batch_model_group_backpressure_min_seconds: 5
  embeddings_batch_model_group_backpressure_max_seconds: 300
  batch_completed_artifact_retention_days: 7
  batch_failed_artifact_retention_days: 14
  batch_metadata_retention_days: 30
  embeddings_batch_gc_enabled: true
  embeddings_batch_gc_interval_seconds: 86400
  embeddings_batch_gc_scan_limit: 200
  audit_enabled: true
  audit_ingestion_mode: legacy
  audit_ingestion_worker_enabled: true
  audit_ingestion_batch_size: 100
  audit_ingestion_flush_interval_ms: 100
  audit_ingestion_max_pending_events: 100000
  audit_ingestion_required_reserve: 10000
  audit_retention_worker_enabled: true
  audit_retention_interval_seconds: 86400
  audit_retention_scan_limit: 500
  audit_metadata_retention_days: 365
  audit_payload_retention_days: 90
```

## UI Branding

Platform administrators can update the installation-wide appearance from the **Theme** tab in the Admin UI Settings page. The same values can be supplied in `general_settings.ui_branding` for file-based deployments.

| Setting | Default | Description |
|---------|---------|-------------|
| `instance_name` | `DeltaLLM` | Product name shown in the shell, authentication flows, browser title, examples, and notification copy. |
| `ui_branding.primary_color` | `#5B50D6` | Primary actions, links, and focus treatments. |
| `ui_branding.secondary_color` | `#8B7CFF` | Secondary actions and navigation accents. |
| `ui_branding.menu_hover_color` | `#F7F5FF` | Navigation hover background. |

Logo and favicon files are managed from **Settings > Theme**, not from file configuration. The service accepts PNG, JPEG, WebP, and SVG files, plus ICO for favicons, with a 2 MB limit per asset. SVG files containing scripts, executable attributes, document type/entity declarations, embedded documents, or external resource references are rejected. Asset bytes are stored in PostgreSQL `BYTEA` columns; the dynamic configuration contains only the versioned internal asset reference.

The supplied colours are base colours, not a request to use one fixed text colour. DeltaLLM derives normal, hover, foreground, and soft-surface tokens at runtime so button labels and branded text retain WCAG AA contrast. Very light primary or secondary colours are adjusted for visible control boundaries, and a menu hover colour that would disappear against the white navigation background is adjusted slightly. A full wordmark falls back to the configured mark and instance name when it cannot load. When no custom logo assets are configured, the built-in Delta mark and wordmark are used. Failed logo assets receive one delayed retry and become eligible again after branding is saved or refreshed; a failed custom favicon falls back to the built-in favicon.

Theme values saved in the Admin UI are persisted as dynamic database overrides and take precedence over file configuration until changed again. **Reset to DeltaLLM defaults** writes explicit factory database overrides (`DeltaLLM`, `#5B50D6`, `#8B7CFF`, and `#F7F5FF`) and clears every custom asset reference. It does not remove the database override to reveal branding from YAML. The reset and deletion of all uploaded logo and favicon BLOBs commit in one serialized transaction, so a rejected transaction cannot expose a partially reset theme. When audit logging is enabled, the endpoint requires a synchronous reset-attempt audit record before it performs the irreversible deletion. Outcome audit records are best effort and never change an already committed reset.

Asset BLOB changes and their versioned theme references commit in the same serialized database transaction. PostgreSQL is authoritative. After commit, Redis broadcasts a small wake-up message so each healthy replica can refresh its in-memory asset cache; the existing database poll, every 30 seconds by default, remains the recovery path when pub/sub delivery or a process-local apply fails. A reset response with `reconciliation_pending: true` therefore means the reset is durable but the responding process could not apply it immediately; the browser still uses the committed branding returned by the server. Persistent database or process failures can delay convergence beyond the poll interval. Public asset reads are served from replica memory with ETags and immutable caching, so normal page rendering does not query PostgreSQL. Theme-only updates refresh application identity without rebuilding model and routing runtime state. Clients load only the public branding projection, never the broader settings payload, and an already-open browser refreshes it when the page regains focus or visibility. During the initial request, the UI shows a neutral loading state and does not render default DeltaLLM branding before the configured branding is known; if that request fails or exceeds three seconds, the built-in defaults are used.

## Authentication Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `master_key` | — | Master API key with full access to all endpoints |
| `deltallm_key_header_name` | `Authorization` | HTTP header name for API key authentication |
| `salt_key` | `change-me` | Salt used for hashing virtual API keys |
| `platform_bootstrap_admin_email` | — | Email for the initial platform admin account |
| `platform_bootstrap_admin_password` | — | Password for the initial platform admin account |
| `auth_session_ttl_hours` | `12` | Session cookie lifetime in hours |
| `invitation_token_ttl_hours` | `72` | Invite acceptance link lifetime in hours |
| `password_reset_token_ttl_minutes` | `60` | Password reset link lifetime in minutes |
| `api_key_auth_cache_ttl_seconds` | `300` | Redis TTL for API key authentication cache entries |
| `model_deployment_source` | `hybrid` | Model source mode: `hybrid`, `db_only`, `config_only` |
| `model_deployment_bootstrap_from_config` | `true` | If `true`, seed DB model deployments from `model_list` when table is empty |

Recommended steady state:
- `model_deployment_source: db_only`
- `model_deployment_bootstrap_from_config: false`

## Organization Deletion Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `organization_lifecycle_auth_max_staleness_seconds` | `3` | Maximum process-local organization-state staleness on authenticated data-plane requests |
| `organization_lifecycle_auth_cache_max_entries` | `10000` | Maximum organization lifecycle records cached per process |
| `organization_deletion_recovery_window_hours` | `168` | Minimum delay before irreversible cleanup begins |
| `organization_deletion_max_attempts` | `20` | Phase claim attempts before the job requires an administrator retry |
| `organization_deletion_requests_enabled` | `false` | Startup-only rollout gate for creating new deletion jobs; enable only after every replica is lifecycle-protocol v2 aware |
| `organization_deletion_worker_enabled` | `true` | Enables durable organization cleanup claims in this process role |
| `organization_deletion_worker_poll_interval_seconds` | `5` | Idle polling interval |
| `organization_deletion_worker_batch_size` | `5` | Maximum jobs claimed per poll |
| `organization_deletion_worker_max_concurrency` | `2` | Maximum cleanup jobs processed concurrently per process |
| `organization_deletion_worker_lease_seconds` | `60` | Claim lease duration |
| `organization_deletion_worker_record_timeout_seconds` | `45` | Per-phase execution timeout |
| `organization_deletion_worker_page_size` | `100` | Maximum records changed by an individual cleanup query |
| `organization_deletion_worker_max_pages_per_claim` | `10` | Maximum cleanup pages run before yielding the job |
| `organization_deletion_waiting_poll_seconds` | `10` | Recheck delay during the recovery/batch-drain phase |
| `organization_deletion_retry_initial_seconds` | `5` | Initial automatic retry delay |
| `organization_deletion_retry_max_seconds` | `300` | Maximum automatic retry delay |

Keep the lifecycle staleness bound short because it is the maximum time an already-cached active organization may remain authorized after another replica schedules deletion. Each process refreshes a singleton lifecycle generation in the background; authenticated requests use matching cached snapshots without another database round trip and fail closed if that background snapshot becomes stale. The deletion request also performs best-effort immediate invalidation, while PostgreSQL and the durable invalidation outbox provide the authoritative transition.

Deploy lifecycle-aware code with `organization_deletion_requests_enabled: false` first. After every API and worker replica reports lifecycle protocol v2 and a fresh lifecycle snapshot, set it to `true` and roll the API deployment. Disable this setting before a rollback. Never roll back to lifecycle-unaware code while an organization is inactive or a deletion job is unfinished.

See [Organization Deletion](../features/organization-deletion.md) for lifecycle behavior, retained data, recovery limits, and operational guidance.

## Database Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `database_url` | — | PostgreSQL connection string |
| `db_pool_size` | `20` | Maximum database connection pool size |
| `db_pool_timeout` | `30` | Connection pool timeout in seconds |
| `telemetry_db_pool_size` | `5` | Size of the separate Prisma pool used by durable spend and audit ingestion |
| `telemetry_db_pool_timeout_seconds` | `2` | Connection-acquisition timeout for the telemetry pool |
| `telemetry_worker_startup_timeout_seconds` | `5` | Maximum time an expected spend or audit worker may take to reconcile capacity and signal readiness |
| `telemetry_shutdown_drain_timeout_seconds` | `20` | Total spend/audit drain and cancellation deadline during process shutdown |
| `spend_ingestion_mode` | `legacy` | `legacy` writes spend synchronously; `outbox` durably enqueues and bulk-applies spend in the telemetry pool |
| `spend_ingestion_batch_size` | `100` | Maximum spend events claimed and committed per worker transaction |
| `spend_ingestion_flush_interval_ms` | `100` | Idle poll interval for the spend outbox worker |
| `spend_ingestion_max_pending_events` | `100000` | Hard cluster-wide bound for active spend outbox rows |
| `spend_ingestion_overload_policy` | `sync_fallback` | At capacity, either use a concurrency-bounded synchronous write or return a controlled `503` with `fail_closed` |
| `spend_ingestion_fallback_max_concurrency` | `1` | Maximum synchronous fallback transactions executing in one process |
| `spend_ingestion_fallback_max_waiters` | `8` | Maximum requests allowed to wait for a synchronous fallback slot in one process; excess requests receive `503` |
| `spend_ingestion_fallback_queue_timeout_ms` | `100` | Maximum time a fallback request may wait for an execution slot |
| `spend_ingestion_fallback_execution_timeout_seconds` | `2` | Deadline covering the fallback transaction and exact spend update |
| `spend_ingestion_completed_retention_hours` | `1` | Retain completed spend outbox rows before bounded cleanup |
| `spend_ingestion_failed_retention_days` | `30` | Compatibility setting; required spend failures are retained as `blocked` until operator replay and are never deleted by retention cleanup |
| `spend_ingestion_cleanup_interval_seconds` | `60` | Interval for the independent spend terminal-row maintenance task |
| `spend_ingestion_cleanup_batch_size` | `1000` | Rows deleted by each spend cleanup query |
| `spend_ingestion_cleanup_max_batches_per_run` | `10` | Maximum cleanup pages drained per maintenance run |
| `spend_ingestion_cleanup_time_budget_seconds` | `2` | Wall-clock budget for one spend cleanup run |
| `spend_reporting_max_concurrency` | `2` | Maximum cache-miss spend reports executing concurrently per API worker |
| `spend_reporting_global_max_concurrency` | `2` | Maximum reporting transactions executing across all workers connected to the same PostgreSQL database |
| `spend_reporting_queue_timeout_seconds` | `10` | Maximum time a spend report waits for a reporting query slot |
| `spend_reporting_execution_timeout_seconds` | `60` | Maximum execution time after a reporting slot is acquired, including database connection acquisition and query execution |
| `spend_reporting_redis_timeout_seconds` | `0.5` | Per-operation Redis deadline for reporting cache coordination before falling back to a guarded database load |
| `spend_reporting_v2_enabled` | `false` | Enables gated team and personal usage views after the reporting-v2 database and fleet rollout is complete |

Pool settings are applied by appending Prisma's `connection_limit` and `pool_timeout` query parameters to the effective database URL at startup. Durable telemetry uses a second Prisma manager and therefore cannot consume request-pool connections. Database URLs, pool settings, and ingestion modes are startup-only: the admin API returns `409 restart_required` if a dynamic update changes them. Batch, capacity, retry, and cleanup settings may be reloaded where supported.

Keep both reporting concurrency limits comfortably below the database pool size so gateway authentication, spend writes, and control-plane operations retain database capacity. PostgreSQL advisory-lock slots enforce the global limit without blocking, while the per-worker limit bounds local queues. Cache hits do not consume reporting query slots. Reporting concurrency and timeout settings are applied to subsequent report loads when dynamic configuration changes; active loads retain the immutable limits with which they started.

Leave `spend_reporting_v2_enabled` disabled during the initial rolling deployment. Follow the [scoped usage reporting rollout](../deployment/usage-reporting-v2.md) before enabling it.

Environment overrides:

- `DELTALLM_DATABASE_URL`
- `DELTALLM_DB_POOL_SIZE`
- `DELTALLM_DB_POOL_TIMEOUT`
- `DELTALLM_TELEMETRY_DB_POOL_SIZE`
- `DELTALLM_TELEMETRY_DB_POOL_TIMEOUT_SECONDS`
- `DELTALLM_SPEND_INGESTION_MODE`
- `DELTALLM_AUDIT_INGESTION_MODE`

If those overrides are unset, DeltaLLM falls back to `general_settings.database_url`, `general_settings.db_pool_size`, and `general_settings.db_pool_timeout`. If no application-level database URL is configured, it will still honor the raw `DATABASE_URL` environment variable used by Prisma.

## Upstream HTTP Settings

These settings control the shared outbound HTTP client used for upstream provider traffic. They are read into a startup snapshot; restart the process or roll the Kubernetes deployment after changing them. Runtime config reloads do not rebuild the HTTP client or change per-request upstream timeout behavior.

| Setting | Default | Description |
|---------|---------|-------------|
| `upstream_http_connect_timeout_seconds` | `10` | Time allowed to establish a new upstream TCP/TLS connection |
| `upstream_http_read_timeout_seconds` | `300` | Time allowed while waiting for upstream response bytes; higher values are useful for streaming |
| `upstream_http_write_timeout_seconds` | `30` | Time allowed while sending request bytes to the upstream |
| `upstream_http_pool_timeout_seconds` | `10` | Time a request can wait for an available upstream connection before failing locally |
| `upstream_http_max_connections` | `500` | Maximum concurrent outbound connections per DeltaLLM process |
| `upstream_http_max_keepalive_connections` | `100` | Maximum idle keep-alive connections retained per process |
| `upstream_http_keepalive_expiry_seconds` | `60` | How long an idle keep-alive connection is retained |

Per-deployment `deltallm_params.timeout` overrides the provider read timeout for that deployment. Without an explicit deployment timeout, DeltaLLM uses `upstream_http_read_timeout_seconds` so production operators can tune streaming and long-running provider calls globally. Connect, write, and pool timeouts remain explicit so slow connection establishment and local connection pool pressure fail predictably instead of looking like provider slowness. Background health checks cap their pool wait below the health-check wrapper timeout so local pool pressure is reported as gateway capacity instead of marking a provider deployment unhealthy.

For production sizing, see [Upstream HTTP Tuning](../deployment/upstream-http.md).

## Redis Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `redis_host` | `localhost` | Redis server hostname |
| `redis_port` | `6379` | Redis server port |
| `redis_password` | — | Redis password (if required) |
| `redis_url` | — | Full Redis URL (overrides host/port/password) |

Redis is also used for:

- API key auth caching
- alert dedupe
- SSO callback state storage

If you plan to enable SSO, treat Redis as required rather than optional.

## Email Settings

Email delivery is optional but required for:

- invitation emails
- password reset
- admin test email
- governance notifications

| Setting | Default | Description |
|---------|---------|-------------|
| `email_enabled` | `false` | Enable outbound email features |
| `email_provider` | `smtp` | Provider: `smtp`, `resend`, or `sendgrid` |
| `email_from_address` | — | Sender address for transactional and governance email |
| `email_reply_to` | — | Optional reply-to address |
| `email_base_url` | — | Base URL used in invite and password-reset links |
| `email_worker_enabled` | `true` | Run the internal outbox worker |
| `email_worker_batch_size` | `10` | Maximum delivery and delivery-audit records claimed in one worker pass |
| `email_worker_max_concurrency` | `3` | Maximum delivery records processed concurrently in one process |
| `email_worker_delivery_lease_seconds` | `60` | Renewable fenced lease for one external email-delivery attempt |
| `email_worker_audit_lease_seconds` | `30` | Renewable fenced lease for one required delivery-audit attempt |
| `email_worker_startup_timeout_seconds` | `5` | Maximum time for an enabled email worker to start and signal readiness |
| `email_worker_shutdown_drain_timeout_seconds` | `20` | Total deadline for the worker to drain and cancel owned tasks during shutdown |
| `email_max_attempts` | `5` | Max outbox delivery attempts |
| `email_retry_initial_seconds` | `60` | Initial retry backoff |
| `email_retry_max_seconds` | `3600` | Max retry backoff |
| `smtp_host` | — | SMTP server hostname |
| `smtp_port` | — | SMTP server port |
| `smtp_username` | — | SMTP username |
| `smtp_password` | — | SMTP password |
| `smtp_use_tls` | `false` | Use TLS for SMTP |
| `resend_api_key` | — | Resend API key |
| `sendgrid_api_key` | — | SendGrid API key |

Recommended rollout:

1. enable email with SMTP or a provider
2. set `email_base_url` to the canonical public app origin
3. verify `/ui/api/email/test`
4. enable invite and recovery flows
5. enable governance notifications only after delivery is confirmed

If `email_enabled: true`, `email_base_url` must be an absolute `http://` or `https://` URL. DeltaLLM fails email bootstrap when it is missing or relative.

Delivery claims are fenced with a worker ID, a unique claim token, and a renewable
lease. A transport failure after bytes may have reached the provider moves the row
to `delivery_unknown`; it is never retried automatically. A platform administrator
must confirm the provider result, then resolve it as `sent` or `failed` through
`POST /ui/api/email/outbox/{email_id}/resolve-delivery`. Required delivery-audit
records move to `blocked` after retry exhaustion, fail readiness, and can be replayed
after investigation through
`POST /ui/api/email/outbox/{email_id}/delivery-audit/replay`. Both operator actions
persist their required audit in the same database transaction as the state change.
Email enablement and worker lifecycle, capacity, lease, startup, and shutdown
settings are startup-only; the admin settings API returns `409 restart_required`
when a dynamic update attempts to change them.

## Cache Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `cache_enabled` | `false` | Enable response caching |
| `cache_backend` | `memory` | Cache backend: `memory`, `redis`, or `s3` |
| `cache_ttl` | `3600` | Cache entry time-to-live in seconds |
| `cache_max_size` | `10000` | Maximum entries for memory cache |
| `stream_cache_max_bytes` | `262144` | Max buffered streaming response bytes before streaming cache is disabled for that stream |
| `prompt_singleflight_max_keys` | `256` | Maximum distinct prompt-resolution cache misses admitted per process; callers for an existing key share its task |
| `prompt_singleflight_timeout_seconds` | `2` | Deadline for an owned prompt-resolution cache-miss task before it fails with a controlled service-unavailable response |
| `stream_cache_max_fragments` | `2048` | Max buffered streaming content fragments before streaming cache is disabled for that stream |
| `failover_event_history_size` | `1000` | Max in-memory failover events retained per instance for `/health/fallback-events` |

## Health Check Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `background_health_checks` | `false` | Run periodic health checks on deployments |
| `health_check_interval` | `300` | Seconds between health checks |
| `health_check_model` | `gpt-3.5-turbo` | Model to use for health check probes |

## SSO Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_sso` | `false` | Enable Single Sign-On |
| `sso_provider` | `oidc` | SSO provider: `microsoft`, `google`, `okta`, or `oidc` |
| `sso_client_id` | — | OAuth client ID |
| `sso_client_secret` | — | OAuth client secret |
| `sso_authorize_url` | — | OAuth authorization URL |
| `sso_token_url` | — | OAuth token URL |
| `sso_userinfo_url` | — | OAuth user info URL |
| `sso_redirect_uri` | — | OAuth redirect URI |
| `sso_scope` | `openid email profile` | OAuth scopes |
| `sso_admin_email_list` | `[]` | Emails that get platform admin role on first SSO login |
| `sso_default_team_id` | — | Optional team automatically assigned to SSO users |
| `sso_state_ttl_seconds` | `600` | TTL for Redis-backed SSO callback state |

SSO callback state is stored in Redis. If SSO is enabled but Redis is unavailable, DeltaLLM keeps SSO disabled instead of exposing a broken login flow.

## Self-Registration Settings

`self_registration` enables constrained self-service onboarding for first-time SSO users. It is intended for developer sandbox access, not unrestricted public signup.

```yaml
general_settings:
  enable_sso: true
  self_registration:
    enabled: true
    mode: sso_allowed_domain
    allowed_domains:
      - example.com
    require_email_verification: true
    require_admin_approval: false
    default_org:
      id: org-sandbox
      name: Developer Sandbox
      max_budget: 100
      soft_budget: 80
      rpm_limit: 300
      tpm_limit: 500000
      rph_limit: 2000
      rpd_limit: 10000
      tpd_limit: 5000000
    default_team:
      id: team-self-serve
      alias: Self-Service Developers
      role: team_developer
      max_budget: 50
      soft_budget: 40
      rpm_limit: 150
      tpm_limit: 250000
      rph_limit: 1000
      rpd_limit: 5000
      tpd_limit: 2500000
      self_service_keys_enabled: true
      self_service_max_keys_per_user: 2
      self_service_budget_ceiling: 5
      self_service_require_expiry: true
      self_service_max_expiry_days: 14
    default_user:
      max_budget: 10
      soft_budget: 8
      rpm_limit: 30
      tpm_limit: 50000
      rph_limit: 200
      rpd_limit: 1000
      tpd_limit: 500000
```

| Setting | Default | Description |
|---------|---------|-------------|
| `self_registration.enabled` | `false` | Enable first-time SSO provisioning into the configured sandbox org/team |
| `self_registration.mode` | `sso_allowed_domain` | Current supported production path for automatic provisioning |
| `self_registration.allowed_domains` | `[]` | Bare email domains eligible for first-time SSO provisioning |
| `self_registration.require_email_verification` | `true` | Require the identity provider to report a verified email before provisioning |
| `self_registration.require_admin_approval` | `false` | Reserved approval gate. When true, automatic sandbox provisioning is blocked |
| `self_registration.default_org.*` | — | Organization ID, display name, budgets, and rate limits to seed |
| `self_registration.default_team.*` | — | Team ID, alias, role, budgets, rate limits, and self-service key policy to seed |
| `self_registration.default_user.*` | — | Runtime user profile type, budget, and rate limits to seed |

When enabled, `default_org.id`, `default_team.id`, and at least one `allowed_domains` entry are required for `sso_allowed_domain`.

The config values seed records only during provisioning. DeltaLLM does not continuously reconcile those records, so platform admins can later edit the organization, team, memberships, asset access, budgets, and limits through the Admin UI or API without config reloads reverting those changes.

## Governance Notification Settings

Governance notifications are opt-in and disabled by default.

| Setting | Default | Description |
|---------|---------|-------------|
| `governance_notifications_enabled` | `false` | Master switch for governance emails |
| `budget_notifications_enabled` | `false` | Enable soft-budget threshold emails |
| `key_lifecycle_notifications_enabled` | `false` | Enable key create/regenerate/revoke/delete emails |
| `budget_alert_ttl_seconds` | `3600` | Deduplication window for budget alerts (shared across all channels) |
| `slack_alerting_enabled` | `false` | Send governance alerts to a Slack incoming webhook in addition to email |
| `slack_webhook_url` | `null` | Slack incoming webhook URL (secret); required when `slack_alerting_enabled` is true |
| `slack_alert_kinds` | `[]` | Alert types routed to Slack, e.g. `["budget_threshold"]`; empty routes nothing |

## Metrics Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `prometheus_endpoint` | `/metrics` | Path for Prometheus metrics endpoint |
| `metrics_retention_days` | `30` | Days to retain spend log data |

## Batch Settings

These settings retain the historical `embeddings_batch_*` names for compatibility. They now control the internal Batch API for supported endpoints, including `/v1/embeddings` and non-streaming `/v1/chat/completions`.

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_enabled` | `false` | Enable `/v1/files` and `/v1/batches` endpoints |
| `embeddings_batch_worker_enabled` | `true` | Run internal batch executor worker loop |
| `embeddings_batch_completion_outbox_worker_enabled` | `true` | Run the batch completion outbox worker loop that finalizes item accounting and spend records |
| `batch_webhook_enabled` | `false` | Accept an optional terminal webhook configuration on new batches |
| `batch_webhook_worker_enabled` | `true` | Run durable webhook delivery workers; disable on API-only pods in split deployments |
| `batch_webhook_observability_enabled` | `true` | Refresh cluster-wide webhook queue gauges independently of delivery and cleanup; assign this role to worker pods in split deployments |
| `batch_webhook_encryption_key` | unset | URL-safe base64 32-byte key used to encrypt webhook URLs and signing secrets at rest; required when webhooks are enabled |
| `batch_webhook_poll_interval_seconds` | `1.0` | Idle webhook outbox poll interval |
| `batch_webhook_observability_refresh_interval_seconds` | `15.0` | Interval for refreshing cluster-wide webhook queue gauges from Postgres |
| `batch_webhook_max_concurrency` | `4` | Maximum concurrent deliveries per webhook worker process |
| `batch_webhook_lease_seconds` | `30` | Delivery ownership lease; must exceed the request timeout |
| `batch_webhook_timeout_seconds` | `10.0` | DNS resolution and outbound request timeout |
| `batch_webhook_max_attempts` | `8` | Maximum attempts before a delivery becomes failed |
| `batch_webhook_retry_initial_seconds` | `5` | Initial jittered exponential retry delay |
| `batch_webhook_retry_max_seconds` | `3600` | Maximum retry delay and `Retry-After` cap |
| `batch_webhook_allowed_ports` | `[443]` | Destination ports permitted by the webhook SSRF policy |
| `batch_webhook_allowed_private_cidrs` | `[]` | Explicit private CIDR exceptions; cloud metadata addresses remain denied |
| `batch_webhook_allow_http` | `false` | Permit unencrypted HTTP destinations; intended only for controlled development networks |
| `batch_webhook_delivery_retention_days` | `30` | Retain delivered and failed outbox rows for inspection/replay history; active rows are never removed, and retained ownership snapshots preserve scoped operations after normal batch metadata cleanup |
| `batch_webhook_cleanup_max_rows_per_run` | `10000` | Maximum delivered/failed webhook rows deleted per garbage-collection run; cleanup uses bounded pages and stops at this budget |
| `embeddings_batch_storage_backend` | `local` | Artifact storage backend. Use `s3` for multi-replica production deployments |
| `embeddings_batch_storage_dir` | `.deltallm/batch-artifacts` | Local artifact storage base directory |
| `embeddings_batch_create_session_cleanup_enabled` | `true` | Enable cleanup for internal staged batch-create artifacts |
| `embeddings_batch_poll_interval_seconds` | `1.0` | Worker poll interval when queue is idle |
| `embeddings_batch_item_claim_limit` | `20` | Max items claimed per worker iteration |
| `embeddings_batch_max_attempts` | `3` | Max retry attempts per failed item |
| `embeddings_batch_retry_initial_seconds` | `5` | Initial retry delay for retryable batch item failures |
| `embeddings_batch_retry_max_seconds` | `300` | Maximum retry delay for retryable batch item failures, including capped `Retry-After` hints |
| `embeddings_batch_retry_multiplier` | `2.0` | Exponential backoff multiplier applied between retry attempts |
| `embeddings_batch_retry_jitter` | `true` | Add jitter to spread batch retries and avoid synchronized retry spikes |
| `embeddings_batch_model_group_backpressure_enabled` | `true` | Temporarily defer model groups that have no healthy deployments |
| `embeddings_batch_model_group_backpressure_min_seconds` | `5` | Minimum model-group deferral duration |
| `embeddings_batch_model_group_backpressure_max_seconds` | `300` | Maximum model-group deferral duration |
| `batch_completed_artifact_retention_days` | `7` | Retention for completed job artifacts |
| `batch_failed_artifact_retention_days` | `14` | Retention for failed/cancelled job artifacts |
| `batch_metadata_retention_days` | `30` | Retention horizon for batch metadata rows |
| `embeddings_batch_gc_enabled` | `true` | Enable background retention cleanup for expired batch metadata/artifacts |
| `embeddings_batch_gc_interval_seconds` | `86400` | Cleanup loop interval in seconds |
| `embeddings_batch_gc_scan_limit` | `200` | Max expired jobs/files processed per cleanup pass |

For Helm deployments with more than one replica, configure `embeddings_batch_storage_backend: s3` and the matching S3 bucket settings before enabling batch. Local batch storage is intended for development and single-replica deployments only.

The prompt singleflight key bound and timeout are startup-owned. Changing either
through dynamic configuration returns `409 restart_required`; roll the deployment
to apply a new bound consistently.

Batch execution honors the same model access, budget, callback, guardrail, rate-limit, and max-parallel policies as synchronous gateway requests. For multi-replica deployments, run Redis and configure `redis_url` so rate-limit counters, max-parallel slots, and model-group backpressure are shared across workers. Without Redis, persistent Postgres state still prevents duplicate item ownership, but in-memory counters and backpressure are local to each replica.

## Audit Settings

Audit events are written to Postgres and can be queried via the Admin Audit API.

| Setting | Default | Description |
|---------|---------|-------------|
| `audit_enabled` | `true` | Enable audit logging (audit events + payload metadata) |
| `audit_ingestion_mode` | `legacy` | `legacy` synchronously persists required audit and prompt-render records while queuing only best-effort audit events in process; production should use `outbox` to durably accept required records through the dedicated telemetry pool |
| `audit_ingestion_worker_enabled` | `true` | Claim and persist durable audit outbox records in this process |
| `audit_ingestion_batch_size` | `100` | Maximum durable audit records committed in one worker transaction |
| `audit_ingestion_flush_interval_ms` | `100` | Idle poll interval for the durable audit worker |
| `audit_ingestion_lease_seconds` | `30` | Claim lease duration before another worker may recover a record |
| `audit_ingestion_max_attempts` | `10` | Attempts before required records become operator-visible `blocked` work and best-effort records become terminally failed |
| `audit_ingestion_max_pending_events` | `100000` | Hard cluster-wide bound for queued, retrying, processing, and blocked required audit records |
| `audit_ingestion_required_reserve` | `10000` | Capacity reserved for required compliance records; best-effort records cannot consume it |
| `audit_ingestion_completed_retention_hours` | `1` | Retain completed audit outbox records before bounded cleanup |
| `audit_ingestion_failed_retention_days` | `30` | Retain terminal failed best-effort audit records; blocked required records are never deleted automatically |
| `audit_ingestion_cleanup_interval_seconds` | `60` | Interval for the independent audit terminal-row maintenance task |
| `audit_ingestion_cleanup_batch_size` | `1000` | Rows deleted by each audit cleanup query |
| `audit_ingestion_cleanup_max_batches_per_run` | `10` | Maximum cleanup pages drained per maintenance run |
| `audit_ingestion_cleanup_time_budget_seconds` | `2` | Wall-clock budget for one audit cleanup run |
| `audit_retention_worker_enabled` | `true` | Enable background audit retention cleanup loop |
| `audit_retention_interval_seconds` | `86400` | Cleanup loop interval in seconds |
| `audit_retention_scan_limit` | `500` | Max expired rows processed per cleanup pass |
| `audit_metadata_retention_days` | `365` | Default retention for audit events (metadata) |
| `audit_payload_retention_days` | `90` | Default retention for audit payloads (request/response bodies when stored) |

With durable ingestion enabled, required audit and prompt-render events never use the in-memory queue. Prompt renders and their best-effort resolution audit share one policy-aware enqueue transaction: one lock-only SQL statement followed by one batched policy/capacity decision and insert statement using a fresh PostgreSQL snapshot. At capacity, required writes fail closed with a controlled `503`; best-effort events are explicitly dropped and counted after their non-reserved capacity is exhausted. A best-effort audit dependency failure is also counted and dropped without changing request or external-side-effect correctness, while required persistence remains fail-closed. In `legacy` mode, required records are persisted synchronously while only best-effort records use the bounded compatibility queue. Exhausted required outbox records remain `blocked`, consume capacity, and require platform-admin replay after investigation. Organization content-policy changes take a database advisory lock, redact active and blocked envelopes when storage is disabled, and publish on an application/environment/schema-scoped Redis channel so every replica evicts its local policy cache. The database policy check remains authoritative if Redis is unavailable. Readiness checks the dedicated telemetry pool and expected workers; repeated worker failures remain readiness-fatal even while the supervised loop attempts recovery.

Apply the telemetry migrations and follow the [durable telemetry rollout](../deployment/telemetry-ingestion-rollout.md) before changing either ingestion mode.
