# Deploy and operate

Use this section when you are ready to run DeltaLLM outside a developer's computer or need to keep
an existing deployment healthy.

## Choose where to run it

| Option | Best for |
| --- | --- |
| [Railway](railway.md) | A quick hosted evaluation |
| [Docker](docker.md) | Local or single-host evaluation |
| [Kubernetes](../guides/kubernetes-deployment.md) | Evaluation or production on a Kubernetes cluster |

The Docker Compose `ha` profile is useful for testing more than one DeltaLLM process, but it is not
a production high-availability setup. Its services still share one machine.

## Before serving real traffic

1. Work through the [production checklist](production-checklist.md).
2. Keep secrets in environment variables or a secret manager.
3. Use protected PostgreSQL and Redis services with backups and monitoring.
4. Run database migrations once before starting new application replicas.
5. Keep health and metrics endpoints on a trusted network.
6. Test an upgrade, rollback, backup, and restore before you need them.

## Keep the system healthy

- [Monitor requests and dependencies](../guides/monitoring.md)
- [Upgrade or roll back](../guides/deployment-workflow.md)
- [Back up and restore data](backup-and-restore.md)
- [Respond to incidents](incident-runbooks.md)
- [Tune provider connections](../guides/provider-connections.md)
- [Harden security](../security/hardening.md)

Release-specific migration and feature rollout instructions are kept in
[Operations details](../reference/index.md#operations-details). Use one only when the release notes
for your target version link to it.
