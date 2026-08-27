# Contributing to the DeltaLLM documentation

DeltaLLM publishes the Markdown sources in `docs/` with MkDocs Material. The public
site is available at <https://docs.deltallm.io/>.

Read `RULES.md` completely before changing documentation. Documentation must agree
with the implementation, configuration, deployment manifests, and supported public
contracts. A plan or TODO records design intent; it is not evidence that behavior is
available.

## Public and unpublished sources

Pages outside `docs/internal/` are public by default, even when they are absent from
the navigation. Do not place secrets, credentials, customer data, private URLs, or
unreviewed plans anywhere in the public tree.

`docs/internal/` contains repository-local planning material and is excluded from
the public MkDocs artifact. The publication check must remain enabled whenever the
documentation build changes. Navigation omission and `robots.txt` are not privacy
controls.

## Set up the frozen environment

The documentation dependencies are declared by the `docs` extra in `pyproject.toml`
and resolved in `uv.lock`:

```bash
uv sync --frozen --extra docs --extra dev
```

Do not hand-maintain a separate requirements file. When a documentation dependency
changes, update `pyproject.toml`, regenerate `uv.lock`, and run `uv lock --check`.

## Preview locally

```bash
uv run mkdocs serve
```

The preview includes only publishable pages. If a page is not ready for publication,
keep it outside the public source tree.

## Run the documentation gate

Use a temporary output directory so generated site files never enter the repository:

```bash
DOCS_SITE_DIR="$(mktemp -d)/site"
uv run mkdocs build --strict --site-dir "$DOCS_SITE_DIR"
uv run python scripts/docs/verify_public_site.py "$DOCS_SITE_DIR"
uv run prisma generate --schema=./prisma/schema.prisma
uv run python scripts/docs/export_openapi.py --check
uv run python scripts/docs/generate_config_reference.py --check
uv run python scripts/docs/generate_provider_reference.py --check
uv run python scripts/docs/report_health.py --check
uv run pytest -q --confcutdir=tests/docs tests/docs/test_verify_public_site.py
git diff --check
```

The build must produce zero MkDocs warnings. The artifact verifier additionally
checks that no internal path appears in generated files, the search index, or the
sitemap.

Generated references require a current Prisma client before importing the FastAPI application:

```bash
uv run prisma generate --schema=./prisma/schema.prisma
uv run python scripts/docs/export_openapi.py
uv run python scripts/docs/generate_config_reference.py
uv run python scripts/docs/generate_provider_reference.py
```

Commit the generated source artifacts with the implementation change that produced them. Do
not edit generated Markdown or JSON by hand.

## Writing and review rules

- Start with the user outcome and prerequisites.
- Use tested commands and realistic placeholders; never use real credentials.
- State whether guidance is for evaluation, development, or production.
- Document authentication, tenant scope, failure behavior, and restart requirements
  when they affect a task.
- Link to one source of truth instead of duplicating long configuration or API tables.
- Add every public page to `mkdocs.yml` unless it is deliberately declared non-nav.
- Preserve public URLs when moving pages, or add an explicit redirect.
- Use descriptive link text and meaningful image alternative text.
- Update related API, configuration, UI, deployment, and release surfaces together.

Choose one primary page type: tutorial, task guide, reference, concept/explanation, or runbook. Split
the material when one page tries to serve several reader intents or grows beyond roughly 500 lines.
The health report flags large files for editorial review without failing only because of length.

New policy, concept, and substantial operations pages should declare concise MkDocs front matter:

```yaml
---
title: Human-readable title
description: One-sentence discovery and review summary.
status: stable | experimental | deprecated | policy
audience: developers | operators | administrators | contributors
---
```

Use `applies_to` for version-specific guidance. Add `last_reviewed` only when an owner and recurring
review process will maintain it; an abandoned date is worse than no date. Existing-page metadata is
being adopted incrementally.

Reviewers should compare claims with source code and tests, inspect the generated
navigation, and run the full documentation gate before approval.

The public [documentation governance policy](docs/project/documentation-governance.md) defines
ownership, product-change prompts, health metrics, and the quarterly review cadence. The [versioning
and compatibility policy](docs/project/versioning-and-compatibility.md) defines release channels,
deprecation, and upgrade documentation requirements.
