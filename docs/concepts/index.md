# Concepts

Concept pages explain how DeltaLLM behaves and where each responsibility lives. Read them
before designing a production topology, access model, or integration that depends on failure
semantics.

| Concept | What it answers |
| --- | --- |
| [Architecture](architecture.md) | Which components own durable state, coordination, request handling, and background work? |
| [Life of a request](request-lifecycle.md) | In what order does DeltaLLM authenticate, transform, authorize, admit, route, and account for a request? |
| [Tenancy and access](tenancy-and-access.md) | How do platform accounts, organizations, teams, users, and keys affect authorization? |

Concepts describe behavior rather than setup steps. Use the [Guides](../features/index.md) to
configure a feature and the [API reference](../api/index.md) for HTTP contracts.
