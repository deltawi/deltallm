# Upgrade a production deployment

Use this checklist with the deployment owner, database owner, and monitoring owner.

1. Complete the [production checklist](../deployment/production-checklist.md).
2. Read the rollout notes for every version included in the upgrade.
3. Create a backup and prove that it can be restored.
4. Run one target-version [database migration job](../deployment/database-migrations.md) before
   starting new application replicas.
5. Deploy a small canary with automatic migration bootstrap disabled.
6. Test successful requests and expected access denials.
7. Increase traffic while watching readiness, errors, latency, queues, and provider health.
8. Confirm that the previous application version is still compatible with the current database and
   that the [rollback decision](../deployment/upgrade-and-rollback.md#rollback-decision) remains
   valid. Execute rollback only if the documented decision threshold is met.

Before the maintenance window, do a full rollback trial in staging.
The production upgrade is complete when all these conditions are true:

- The migration evidence has a timestamp before the rollout.
- Each replica uses the pinned image.
- The tests pass.
- The alerts remain stable.
- The operator recorded the rollback readiness check.
