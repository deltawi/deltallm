# Prisma Upgrade Fixtures

This directory holds the deterministic SQL and metadata used by the lighter PR 3 upgrade-path CI.

What stays in permanent CI:

- `fresh_install`
  - proves a normal empty database can reach HEAD with `prisma migrate deploy`
- `previous_release_upgrade`
  - restores one real tagged migrated release and upgrades it to HEAD
  - verifies both migration history progression and survival of representative seeded batch data

Fixture files:

- `previous_release_snapshot.sql`
  - schema-only dump generated from `v0.1.20-rc2` after `prisma migrate deploy`
- `previous_release_seed.sql`
  - `_prisma_migrations` plus a small deterministic batch/create-session dataset from `v0.1.20-rc2`
- `previous_release_metadata.json`
  - source tag/commit, fixture migration watermark, migration count, and seeded identifiers

What does not stay in permanent CI:

- legacy `db push` release states
  - those stay covered by the PR 2 helper and operator runbook
  - they are higher-maintenance and less portable than the common fresh-install and previous-release cases

Refresh process:

1. Create a detached worktree for the chosen previous release tag.
2. Restore a disposable PostgreSQL database for that tag.
3. Generate the previous-release schema with `prisma migrate deploy`.
4. Dump schema-only SQL into `previous_release_snapshot.sql`.
5. Refresh the seed and metadata files if the representative fixture rows change.
6. Run `./scripts/ci/prisma_upgrade_paths.sh fresh_install`.
7. Run `./scripts/ci/prisma_upgrade_paths.sh previous_release_upgrade`.
