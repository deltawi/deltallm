# OpenAPI Schema

DeltaLLM exports the FastAPI schema from the same application routes used at runtime. The
artifact is generated deterministically and CI fails when route/schema changes are not
committed.

[Download `openapi.json`](openapi.json){ .md-button .md-button--primary }

Use the file with OpenAPI tooling such as Swagger UI, Redoc, client generators, contract tests,
or API gateways. The running application also serves its schema and interactive viewers at:

```text
GET /openapi.json
GET /docs
GET /redoc
```

Restrict those runtime routes at the ingress if publishing your full control-plane schema is
not appropriate for the deployment.

## Regenerate locally

The application imports typed repositories, so the Prisma client must exist.
Schema generation does not start the FastAPI lifespan.
It does not connect to PostgreSQL, Redis, or providers.

```bash
uv sync --frozen --extra docs
uv run prisma generate --schema=./prisma/schema.prisma
uv run python scripts/docs/export_openapi.py
```

To verify without modifying the artifact:

```bash
uv run python scripts/docs/export_openapi.py --check
```

The exporter rejects missing and duplicate operation IDs.
OpenAPI documents the schemas that routes declare.
Some control-plane operations still accept untyped dictionaries.
These operations need backend types before a generator can give more specific field contracts.
