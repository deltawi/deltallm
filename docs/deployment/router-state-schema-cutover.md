# Router Redis v1 Schema Cutover

This release namespaces ephemeral router state as
`deltallm:<app_env>:v1:<router-capability>:<identifiers>`. Earlier releases used unscoped keys such
as `active_requests:*`, `cooldown:*`, `failures:*`, `health:*`, `latency:*`, and `usage_*:*`. Old and
new binaries must not run concurrently: they would make independent admission, cooldown, health,
latency, and usage decisions against the same providers.

The Helm chart marks v1 releases on the API ConfigMap and blocks upgrades from an unmarked legacy
release while `routerStateSchemaCutover.enabled=true` unless the operator acknowledges the cutover
and selects the `Recreate` strategy. Later v1-to-v1 upgrades detect the marker and retain their
configured strategy. The acknowledgement is a safety gate, not a drain mechanism; set it only
after every old API and batch-worker pod has stopped.

## Forward cutover

1. Schedule a maintenance window and stop new gateway and batch admission at the ingress or job
   producer. Record the desired API and worker replica counts and autoscaling settings.
2. Disable or suspend the API and batch-worker HPAs so they cannot restore replicas during the
   drain.
3. Wait for active requests, streams, provider attempts, and claimed batch work to finish within
   their configured drain deadlines. Investigate rather than force-completing durable batch work.
4. Scale both the API and batch-worker Deployments to zero. Confirm that no pod using the old image
   remains in any namespace connected to the same Redis environment.
5. Inspect legacy state with the bounded control-plane command. Every retained active-request value
   must be zero before proceeding. Do not use `KEYS` against a production Redis service.

   ```bash
   uv run python scripts/router_redis_schema_cutover.py inspect \
     --redis-url-file /run/secrets/deltallm-redis-url
   ```

   The file must contain only the Redis URL. Use `--redis-url` only for credential-free local
   services because command arguments can be visible to other users on the host.
6. Run the Helm upgrade with the normal pinned values and secrets plus:

   ```bash
   helm upgrade deltallm deltallm/deltallm \
     --namespace deltallm \
     --version <chart-version> \
     -f <values-file> \
     --set routerStateSchemaCutover.acknowledged=true \
     --set strategy.type=Recreate
   ```

7. Confirm migrations completed before application readiness, then verify API and worker readiness,
   provider health, namespaced Redis state, and a controlled gateway request before restoring
   admission and autoscaling.

The new key schema does not add Redis round trips. Admission and health transitions keep their
existing Lua calls, while batch reads remain pipelines or `MGET` operations.

## Rollback

1. Stop new admission, drain the v1 workloads, suspend their HPAs, and scale every v1 API and worker
   Deployment to zero. Never start an older binary while a v1 pod is still connected to Redis.
2. Remove only the earlier unscoped ephemeral router keys with the bounded control-plane command.
   It refuses to delete while any legacy active-request count is non-zero and requires an explicit
   destructive-operation confirmation:

   ```bash
   uv run python scripts/router_redis_schema_cutover.py clear \
     --redis-url-file /run/secrets/deltallm-redis-url \
     --confirm-clear-legacy-router-state
   ```

   This prevents an older binary from reviving stale cooldown, health, latency, or admission
   evidence. Namespaced v1 keys may remain until their TTLs expire.
3. If rate or usage windows are being reset, wait for the current configured window and record the
   temporary accounting impact. Durable billing and audit data are not part of this Redis cleanup.
4. Deploy the older chart using `Recreate`, run provider health probes, verify request admission and
   cooldown recovery, then restore traffic and autoscaling.

If a maintenance cutover is unacceptable, do not bypass the Helm gate. Implement and certify a
separate dual-schema compatibility release before attempting a zero-downtime rollout.
