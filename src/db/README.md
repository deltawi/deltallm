# Database modules

This package owns SQL, database transactions, repositories, and record mapping.
Services own application policy. Prisma migrations remain in `prisma/migrations`.

```text
src/db/                     # Durable records, queries, constraints, and transactions.
├── __init__.py             # Keep the public database manager and repository exports.
├── errors.py               # Define shared database failure types.
├── json_fields.py          # Parse optional JSON object fields in one place.
├── runtime/                # Own database pools, allocated clients, and migration checks.
├── identity/               # Store accounts, keys, sessions, invitations, and key policies.
│   └── external/           # Store external identity bindings, sessions, and rollback work.
├── organizations/          # Read organizations and guard organization mutations.
│   └── deletion/           # Plan, claim, and complete durable organization deletion.
├── catalog/                # Store models, deployments, credentials, prompts, and branding.
├── routing/                # Store callable grants, route groups, policies, and routing costs.
├── tiers/                  # Store tier catalogs, assignments, versions, and policy records.
├── billing/                # Store reservations, spend, notifications, and recovery state.
├── accounting/             # Store native accounting generations, windows, and batches.
│   ├── permits/            # Allocate and return durable local accounting permits.
│   ├── journal/            # Claim journal work with durable leases and owner checks.
│   ├── reporting/          # Query and update accounting projections and read models.
│   └── health/             # Read accounting backlog and worker presence records.
├── audit/                  # Store audit events, payloads, ingestion work, and retention state.
├── email/                  # Store email delivery, feedback, and suppression records.
└── mcp/                    # Store MCP servers, bindings, grants, and scope policies.
```

Put a query with the repository that owns its records. Keep SQL parameterized.
Preserve tenant filters, transaction boundaries, and retry behavior when moving code.
