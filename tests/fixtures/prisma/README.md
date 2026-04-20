# Prisma Upgrade Fixtures

This directory holds the deterministic SQL and metadata used by PR 3 upgrade-path CI.

Fixtures:

- `legacy_v0_1_19_snapshot.sql`
  - schema-only dump generated from `v0.1.19` after `prisma db push`
  - represents the real legacy no-history release shape
- `legacy_v0_1_19_seed.sql`
  - deterministic application rows for the legacy fixture
  - intentionally does not create `_prisma_migrations`
- `legacy_v0_1_19_metadata.json`
  - source tag/commit and seeded identifiers for Scenario B
- `previous_release_snapshot.sql`
  - schema-only dump generated from `v0.1.20-rc2` after `prisma migrate deploy`
- `previous_release_seed.sql`
  - `_prisma_migrations` plus deterministic batch/create-session rows from `v0.1.20-rc2`
- `previous_release_metadata.json`
  - source tag/commit, fixture migration watermark, migration count, and seeded identifiers for Scenario C

Scenario mapping:

- `head_db_push_no_history_baseline`
  - synthetic current-HEAD `db push` environment with no Prisma history
  - proves the PR 2 helper can baseline a no-history database that already matches the checked-in schema contract
- `legacy_v0_1_19_refusal`
  - restores the real `v0.1.19` legacy schema with no Prisma history
  - proves the helper refuses this older released shape cleanly instead of attempting an unsafe baseline
- `previous_release_v0_1_20_rc2_upgrade`
  - restores the `v0.1.20-rc2` migrated release state
  - proves current HEAD upgrades a real tagged release with pre-existing batch data

Invariants enforced by CI:

- the legacy fixture must not contain `_prisma_migrations`
- the released `v0.1.19` legacy fixture must be rejected by the helper with the expected safety error
- the previous-release fixture must be behind current HEAD
- the previous-release scenario must end with a higher migration count than the fixture metadata records
- seeded historical rows must still exist after the upgrade

Refresh process:

1. Create a detached worktree for `v0.1.19` and `v0.1.20-rc2`.
2. Restore disposable PostgreSQL databases for each tag.
3. Generate the legacy schema with `prisma db push` from `v0.1.19`.
4. Generate the previous-release schema with `prisma migrate deploy` from `v0.1.20-rc2`.
5. Dump schema-only SQL into the snapshot files.
6. Refresh the seed/metadata files if the representative fixture rows change.
7. Run `./scripts/ci/prisma_upgrade_paths.sh head_db_push_no_history_baseline`.
8. Run `./scripts/ci/prisma_upgrade_paths.sh legacy_v0_1_19_refusal`.
9. Run `./scripts/ci/prisma_upgrade_paths.sh previous_release_v0_1_20_rc2_upgrade`.
