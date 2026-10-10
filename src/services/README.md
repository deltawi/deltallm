# Service modules

This package owns application use cases and policy. Database queries belong in
`src/db`. Provider protocol code belongs in `src/providers`. Bootstrap owns
clients, pools, and worker startup.

```text
src/services/                   # Application services, policy, and background work.
├── __init__.py                 # Keep the existing public service exports.
├── selector_evaluation_cli.py  # Keep the documented selector evaluation command.
├── identity/                   # Manage accounts, sessions, registration, and invitations.
│   ├── keys/                   # Authenticate, cache, notify, remove, and revoke keys.
│   └── external/               # Exchange external identities and manage their sessions.
├── access/                     # Resolve asset ownership, grants, visibility, and access.
├── admission/                  # Enforce rate, concurrency, output-token, and fair-share limits.
├── tiers/                      # Manage tier assignments, policy snapshots, and previews.
├── models/                     # Load deployments and resolve model identity and credentials.
├── routing/                    # Load route groups, publish policy, and evaluate selectors.
├── prompts/                    # Look up and render prompts with bounded cached reads.
├── organizations/              # Authorize organization lifecycle and mutation operations.
│   └── deletion/               # Plan, request, audit, and complete organization deletion.
├── email/                      # Deliver email and process tokens, feedback, and recipients.
├── audit/                      # Accept, retain, and replay audit records.
├── invalidation/               # Reconcile shared cache and configuration changes.
├── reporting/                  # Read bounded spend, cost, and health reports.
└── ui/                         # Map UI capabilities and validate branding assets.
```

Keep feature-specific caches with their feature. The `invalidation` folder owns
shared invalidation work, not every cache. The `models` folder contains services;
edge request and response models remain in `src/models`.

Import internal services from their group. Keep the public `src.services` exports
and the `python -m src.services.selector_evaluation_cli` command compatible.
This folder change does not change policy, SQL, limits, or worker lifecycle.
