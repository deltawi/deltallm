# Database Migrations

Production migration has one invariant: **one release-scoped job completes successfully before any
new API or worker replica starts**. Do not rely on every replica racing through the image's default
migration bootstrap.

## Supported command

The generic migration command uses Prisma's checked-in migration history and retries only database
connectivity failures:

```bash
python -m src.prisma_bootstrap \
  --schema ./prisma/schema.prisma \
  --max-attempts 30 \
  --sleep-seconds 2
```

It runs `prisma migrate deploy`; it does not run `prisma db push`. A non-connectivity error is fatal
and requires investigation rather than an automatic destructive repair.

Some releases require a named coordinator, backfill, or compatibility cutover in addition to the
generic command. Read every rollout page associated with the target release. For example,
[Organization deletion](../features/organization-deletion.md) and the [router-state schema
cutover](router-state-schema-cutover.md) have stricter sequencing requirements.

## Kubernetes job example

Create a new job name for every release and use the same immutable image that will run the
application. This example assumes `deltallm-runtime-secrets` contains `DATABASE_URL`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: deltallm-migrate-<release-id>
  namespace: deltallm
  labels:
    app.kubernetes.io/name: deltallm
    app.kubernetes.io/component: migration
spec:
  backoffLimit: 4
  activeDeadlineSeconds: 900
  template:
    metadata:
      labels:
        app.kubernetes.io/name: deltallm
        app.kubernetes.io/component: migration
    spec:
      restartPolicy: OnFailure
      automountServiceAccountToken: false
      containers:
        - name: migrate
          image: deltallm/deltallm@sha256:<release-image-digest>
          command: ["python", "-m", "src.prisma_bootstrap"]
          args:
            - --schema
            - ./prisma/schema.prisma
            - --max-attempts
            - "30"
            - --sleep-seconds
            - "2"
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: deltallm-runtime-secrets
                  key: DATABASE_URL
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
```

Apply the reviewed manifest, wait for it, and retain the logs with the release evidence:

```bash
kubectl apply -f deltallm-migration-<release-id>.yaml
kubectl wait --for=condition=complete --timeout=15m \
  job/deltallm-migrate-<release-id> -n deltallm
kubectl logs job/deltallm-migrate-<release-id> -n deltallm
```

Do not reuse a completed Job name for a different image or command.

## Rollout after migration

Only after the migration and any release-specific verification succeed, deploy the application
with the image bootstrap wrapper disabled:

```yaml
migrationJob:
  enabled: false

command: ["uvicorn"]
args: ["src.main:app", "--host", "0.0.0.0", "--port", "4000"]
```

The global command applies to both API and chart-managed batch-worker pods. Confirm the rendered
manifests before rollout.

## Migration design and failure handling

- Prefer expand/backfill/contract changes: add compatible structures, deploy compatible code,
  backfill with bounded work, then remove old structures in a later release.
- Take and verify a recoverable backup before risky or contract migrations.
- Monitor locks, long transactions, connection saturation, replication lag, and disk during the job.
- If a migration fails, stop the application rollout. Preserve job logs and database state; do not
  edit `_prisma_migrations`, mark a migration applied, or use `db push` without a reviewed recovery plan.
- Rolling application code back does not roll schema back. A safe rollback requires the old binary
  to remain compatible with the migrated schema.

Continue with [Upgrades and rollbacks](upgrade-and-rollback.md).
