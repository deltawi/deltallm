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

The Prisma client must exist because the application imports its typed repositories, but schema
generation does not start the FastAPI lifespan or connect to PostgreSQL, Redis, or providers.

```bash
uv sync --frozen --extra docs
uv run prisma generate --schema=./prisma/schema.prisma
uv run python scripts/docs/export_openapi.py
```

To verify without modifying the artifact:

```bash
uv run python scripts/docs/export_openapi.py --check
```

The exporter rejects missing and duplicate operation IDs. OpenAPI documents the schemas that
routes declare; control-plane operations that still accept untyped dictionaries require
separate backend typing work before a generator can infer stronger field contracts.
