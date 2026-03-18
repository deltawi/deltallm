# General Settings

The `general_settings` section configures authentication, database connections, caching, and platform-level options.

## Full Reference

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  deltallm_key_header_name: Authorization
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
  db_pool_size: 20
  db_pool_timeout: 30
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
  api_key_auth_cache_ttl_seconds: 300
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: false
  embeddings_batch_enabled: false
  embeddings_batch_worker_enabled: true
  embeddings_batch_storage_dir: .deltallm/batch-artifacts
  embeddings_batch_poll_interval_seconds: 1.0
  embeddings_batch_item_claim_limit: 20
  embeddings_batch_max_attempts: 3
  batch_completed_artifact_retention_days: 7
  batch_failed_artifact_retention_days: 14
  batch_metadata_retention_days: 30
  embeddings_batch_gc_enabled: true
  embeddings_batch_gc_interval_seconds: 86400
  embeddings_batch_gc_scan_limit: 200
  audit_enabled: true
  audit_retention_worker_enabled: true
  audit_retention_interval_seconds: 86400
  audit_retention_scan_limit: 500
  audit_metadata_retention_days: 365
  audit_payload_retention_days: 90
```

## Authentication Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `master_key` | — | Master API key with full access to all endpoints |
| `deltallm_key_header_name` | `Authorization` | HTTP header name for API key authentication |
| `salt_key` | `change-me` | Salt used for hashing virtual API keys |
| `platform_bootstrap_admin_email` | — | Email for the initial platform admin account |
| `platform_bootstrap_admin_password` | — | Password for the initial platform admin account |
| `auth_session_ttl_hours` | `12` | Session cookie lifetime in hours |
| `api_key_auth_cache_ttl_seconds` | `300` | Redis TTL for API key authentication cache entries |
| `model_deployment_source` | `hybrid` | Model source mode: `hybrid`, `db_only`, `config_only` |
| `model_deployment_bootstrap_from_config` | `true` | If `true`, seed DB model deployments from `model_list` when table is empty |

Recommended steady state:
- `model_deployment_source: db_only`
- `model_deployment_bootstrap_from_config: false`

## Database Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `database_url` | — | PostgreSQL connection string |
| `db_pool_size` | `20` | Maximum database connection pool size |
| `db_pool_timeout` | `30` | Connection pool timeout in seconds |

Pool settings are applied by appending Prisma's `connection_limit` and `pool_timeout` query parameters to the effective database URL at startup.

Environment overrides:
- `DELTALLM_DATABASE_URL`
- `DELTALLM_DB_POOL_SIZE`
- `DELTALLM_DB_POOL_TIMEOUT`

If those overrides are unset, DeltaLLM falls back to `general_settings.database_url`, `general_settings.db_pool_size`, and `general_settings.db_pool_timeout`. If no application-level database URL is configured, it will still honor the raw `DATABASE_URL` environment variable used by Prisma.

## Redis Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `redis_host` | `localhost` | Redis server hostname |
| `redis_port` | `6379` | Redis server port |
| `redis_password` | — | Redis password (if required) |
| `redis_url` | — | Full Redis URL (overrides host/port/password) |

## Cache Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `cache_enabled` | `false` | Enable response caching |
| `cache_backend` | `memory` | Cache backend: `memory`, `redis`, or `s3` |
| `cache_ttl` | `3600` | Cache entry time-to-live in seconds |
| `cache_max_size` | `10000` | Maximum entries for memory cache |
| `stream_cache_max_bytes` | `262144` | Max buffered streaming response bytes before streaming cache is disabled for that stream |
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

## Metrics Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `prometheus_endpoint` | `/metrics` | Path for Prometheus metrics endpoint |
| `metrics_retention_days` | `30` | Days to retain spend log data |

## Embeddings Batch Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_enabled` | `false` | Enable `/v1/files` and `/v1/batches` endpoints |
| `embeddings_batch_worker_enabled` | `true` | Run internal batch executor worker loop |
| `embeddings_batch_storage_dir` | `.deltallm/batch-artifacts` | Local artifact storage base directory |
| `embeddings_batch_poll_interval_seconds` | `1.0` | Worker poll interval when queue is idle |
| `embeddings_batch_item_claim_limit` | `20` | Max items claimed per worker iteration |
| `embeddings_batch_max_attempts` | `3` | Max retry attempts per failed item |
| `batch_completed_artifact_retention_days` | `7` | Retention for completed job artifacts |
| `batch_failed_artifact_retention_days` | `14` | Retention for failed/cancelled job artifacts |
| `batch_metadata_retention_days` | `30` | Retention horizon for batch metadata rows |
| `embeddings_batch_gc_enabled` | `true` | Enable background retention cleanup for expired batch metadata/artifacts |
| `embeddings_batch_gc_interval_seconds` | `86400` | Cleanup loop interval in seconds |
| `embeddings_batch_gc_scan_limit` | `200` | Max expired jobs/files processed per cleanup pass |

## Audit Settings

Audit events are written to Postgres and can be queried via the Admin Audit API.

| Setting | Default | Description |
|---------|---------|-------------|
| `audit_enabled` | `true` | Enable audit logging (audit events + payload metadata) |
| `audit_retention_worker_enabled` | `true` | Enable background audit retention cleanup loop |
| `audit_retention_interval_seconds` | `86400` | Cleanup loop interval in seconds |
| `audit_retention_scan_limit` | `500` | Max expired rows processed per cleanup pass |
| `audit_metadata_retention_days` | `365` | Default retention for audit events (metadata) |
| `audit_payload_retention_days` | `90` | Default retention for audit payloads (request/response bodies when stored) |
