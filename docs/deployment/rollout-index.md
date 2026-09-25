# Release-specific rollouts

These runbooks cover changes that need extra care during particular upgrades. They are not a general installation sequence.

!!! warning "Check the release notes first"
    Use a runbook only when the release notes for your target version link to it. Confirm the source and target versions, rehearse the change in staging, take a verified backup, and name the person who can stop or reverse the rollout.

| Runbook | Use it when the release notes mention | Main risk |
| --- | --- | --- |
| [Governance rollout](governance.md) | callable-target or MCP governance | inconsistent access decisions between replicas |
| [Organization tiers](organization-tiers-rollout.md) | organization tiers or weighted capacity pools | enabling policy before data and assignments are ready |
| [Batch scheduler](batch-scheduler-rollout.md) | the embeddings batch scheduler | stuck work or more database pressure than planned |
| [Batch webhooks](batch-webhook-rollout.md) | durable terminal batch webhooks | undelivered events or mismatched encryption keys |
| [Router Redis schema cutover](router-state-schema-cutover.md) | the router Redis v1 namespace | old and new replicas making different routing decisions |
| [Durable telemetry ingestion](telemetry-ingestion-rollout.md) | audit or spend outbox ingestion | missing records, backlog growth, or database exhaustion |
| [Scoped usage reporting](usage-reporting-v2.md) | scoped usage reporting or owner snapshots | incomplete ownership data or a blocked migration |

Fresh installations normally do not need these transition steps unless their release notes say otherwise.
