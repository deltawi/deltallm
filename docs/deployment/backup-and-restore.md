# Backup and Restore

A backup must be complete and protected. The retention period must meet business requirements.
Regular restore tests show that the backup remains usable.

1. Set the recovery-point objective from business requirements.
2. Set the recovery-time objective from business requirements.
3. Configure the database and object stores to meet these objectives.

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

Use the database vendor's supported backup tools in an isolated operator environment.

1. Configure a PostgreSQL service named `deltallm-production`.
2. Put its password in a protected password file.
3. Keep the password out of commands and shell history.

For a portable logical backup, use this command:

```bash
PGSERVICE=deltallm-production \
  pg_dump --format=custom --file=deltallm-<timestamp>.dump
```

1. Encrypt the backup file.
2. Record its checksum.
3. Record the source database and its version.
4. Move the file to storage with access controls and retention protection.

A successful backup command does not prove that you can restore the database.

## Restore rehearsal

Restore into a new, isolated database—never over the active production database:

```bash
createdb deltallm_restore_test
PGSERVICE=deltallm-restore-test \
  pg_restore --exit-on-error --no-owner --dbname=deltallm_restore_test \
  deltallm-<timestamp>.dump
```

After the restore, do these steps:

1. Block external connections from the restored environment to providers, email services, webhooks, MCP servers, and other external systems.
2. Use separate test secrets.
3. Disable background side effects.
4. Run the application version that is compatible with the backup's schema.
5. Examine the migration history.
6. Examine representative tenant, key, and model records.
7. Verify table counts.
8. Do authenticated read-only checks with test providers.
9. Do synthetic gateway checks with test providers.
10. Measure the recovery time.
11. Record the manual steps.
12. Delete the restored sensitive data or restore its protection, as specified by policy.

## Production recovery

1. Declare the incident.
2. Stop writers and workers.
3. Record the selected recovery point.
4. Restore to a new database instance or a recovery target that the provider manages.
5. Before you change application secrets, verify database consistency and migration compatibility.
6. Start an isolated application replica with external side effects disabled.
7. Do the recovery checks.
8. After approval, switch traffic to the restored application.
9. Enable the workers and integrations again.
10. Reconcile requests, batch jobs, callbacks, audit and spend outboxes, and provider side effects that occurred after the recovery point.

Database recovery cannot reverse external provider actions.
If the incident included unauthorized backup access, replace the credentials.
Record the actual recovery point and recovery time.
Update the [production checklist](production-checklist.md) with the identified gaps.
