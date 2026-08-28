# Upgrades and Rollbacks

Treat an upgrade as a coordinated change to application code, database schema, configuration,
workers, and external integrations. A Helm rollback alone cannot reverse a schema or data change.

## Before the maintenance window

1. Read release notes and every linked rollout/cutover page between the current and target versions.
2. Record current application/chart versions, image digests, values, ConfigMaps, secret references,
   feature flags, and database migration state.
3. Confirm the old version can run against the target release's expanded schema. If it cannot, the
   rollback point is before migration and requires database restore or a release-specific plan.
4. Take a verified backup and confirm the restore procedure, available capacity, and on-call owners.
5. Render and review the target manifests. Confirm operational routes remain private and replicas do
   not execute migration bootstrap.
6. Run representative smoke/load tests in a staging environment using the same migration sequence.

## Production sequence

1. Pause incompatible administrative writes or feature entry points named by the release runbook.
2. Run the one-shot [database migration](database-migrations.md) with the target image.
3. Complete required readiness scripts, bounded backfills, or namespace cutovers.
4. Deploy a canary or the smallest safe replica set with migration bootstrap disabled.
5. Verify liveness, readiness, authenticated gateway traffic, streaming, admin access, audit/spend
   ingestion, queues, provider errors, and authorization denials.
6. Increase traffic/replicas while watching latency, failure rate, saturation, and background lag.
7. Enable new feature flags only after every old API and worker replica is drained and the release
   runbook's compatibility checks pass.
8. Record evidence and close the maintenance window only when alerts and backlogs are stable.

## Rollback decision

Prefer disabling a new feature flag or routing traffic away from a bad canary when that contains
the issue. Roll application binaries back only if the previous version is compatible with the
current schema and durable data.

Stop and use a reviewed recovery plan when:

- a contract migration removed or changed data the old binary needs;
- a backfill partially changed meaning or ownership;
- an old and new protocol cannot overlap; or
- the incident requires restoring PostgreSQL to a previous point in time.

## Application rollback

1. Stop rollout and preserve logs, migration output, current manifests, and incident timestamps.
2. Disable new feature gates and stop incompatible writers/workers.
3. Restore the previous immutable image and configuration without re-running migration bootstrap.
4. Verify readiness and the same success/denial smoke tests used during rollout.
5. Monitor queues and durable outboxes for duplicate, stuck, or incompatible work.
6. Document any data repair required before retrying the upgrade.

If database restore is required, follow [Backup and restore](backup-and-restore.md); do not point
application replicas at a partially restored database.
