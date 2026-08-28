# Backup and Restore

Backups are useful only when they are complete, protected, retained, and repeatedly restored. Set
recovery-point and recovery-time objectives from business requirements, then configure the
database and object stores to meet them.

## What to protect

| Asset | Backup/retention requirement |
| --- | --- |
| PostgreSQL | Managed snapshots or physical backups plus point-in-time recovery where required; logical export for portability |
| Batch artifacts and callback/log object storage | Versioning or provider backups with lifecycle and deletion protection appropriate to retention policy |
| Deployment configuration | Versioned non-secret config, chart/application versions, image digests, and secret references |
| Secret-manager data | Provider-supported protected backup/recovery; never copy plaintext secrets into the docs or Git backup |
| Redis | Configure persistence/replication only if enabled features require recovery; design for cache loss separately |

Prometheus data, external telemetry, provider-side logs, email delivery records, and identity-provider
configuration can also be part of the recovery scope. Name an owner for each system.

## PostgreSQL backup example

Run the database vendor's supported backup tooling from an isolated operator environment. For a
portable logical backup:

```bash
pg_dump --format=custom --file=deltallm-<timestamp>.dump "$DATABASE_URL"
```

Encrypt the artifact, record its checksum and source database/version, and move it to access-
controlled storage with retention protection. A successful command is not restore evidence.

## Restore rehearsal

Restore into a new, isolated database—never over the active production database:

```bash
createdb deltallm_restore_test
pg_restore --exit-on-error --no-owner \
  --dbname=postgresql://<isolated-restore-target>/deltallm_restore_test \
  deltallm-<timestamp>.dump
```

Then:

1. restrict the restored environment from provider, email, webhook, MCP, and other external egress;
2. use separate test secrets and disable background side effects;
3. run the application version compatible with the backup's schema;
4. verify migration history, representative tenant/key/model records, and table counts;
5. run authenticated read-only and synthetic gateway checks with test providers;
6. measure recovery time and record any manual steps; and
7. destroy or re-protect restored sensitive data according to policy.

## Production recovery

1. Declare the incident, stop writers/workers, and record the recovery point selected.
2. Restore to a new database instance or provider-managed recovery target.
3. Validate database consistency and migration compatibility before changing application secrets.
4. Bring up a quarantined application replica with external side effects disabled and run checks.
5. Switch traffic only after approval, then re-enable workers and integrations deliberately.
6. Reconcile requests, batch jobs, callbacks, audit/spend outboxes, and provider side effects that
   occurred after the recovery point. Database recovery cannot undo external provider actions.

Rotate credentials if the incident involved unauthorized backup access. Record actual recovery
point/time results and feed gaps back into the [production checklist](production-checklist.md).
