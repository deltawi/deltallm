# Prisma Migration Runbook

Use this runbook when upgrading a legacy DeltaLLM database that was initialized with `prisma db push` and does not have trustworthy Prisma migration history.

This is a one-time manual remediation flow for issue 94 style environments. It is not part of normal startup, Helm rollout, or Docker runtime behavior.

## When To Use This

Use this runbook when all of the following are true:

- the target database already contains DeltaLLM tables
- `_prisma_migrations` is missing or empty
- `prisma migrate deploy` fails because Prisma refuses to apply migrations onto a database without a baseline

Do not use this runbook for:

- fresh empty databases
- databases that already have valid Prisma migration history
- environments with manual schema drift you have not investigated
- environments with failed or partially applied Prisma migrations

## Why This Happens

Older DeltaLLM environments could be initialized with `prisma db push`. That created tables directly but did not record the checked-in migration chain in `_prisma_migrations`.

The supported runtime contract now expects `prisma migrate deploy`. Prisma correctly refuses to deploy that migration history onto an existing non-empty database unless the operator first records the historical migrations as already applied.

## Preconditions

Before running the baseline flow:

- take a database backup or snapshot
- schedule a release window
- confirm you have the correct `DATABASE_URL`
- prepare a disposable shadow database and set `SHADOW_DATABASE_URL`
- use the same repository revision you intend to deploy
- confirm the checked-in Prisma migration chain is internally consistent for that revision

The shadow database is required by `prisma migrate diff --from-migrations ...`.

## Helper Commands

The repo ships a guarded helper:

```bash
uv run python scripts/prisma/baseline_legacy_environment.py inspect
uv run python scripts/prisma/baseline_legacy_environment.py plan
uv run python scripts/prisma/baseline_legacy_environment.py apply --yes
```

Required environment:

```bash
export DATABASE_URL='postgresql://...'
```

Required for `plan` and `apply`:

```bash
export SHADOW_DATABASE_URL='postgresql://...'
```

Optional flags:

```bash
--schema ./prisma/schema.prisma
--migrations-dir ./prisma/migrations
--output-diff /tmp/deltallm-issue94-diff.sql
```

## Step 1: Inspect

Run:

```bash
uv run python scripts/prisma/baseline_legacy_environment.py inspect
```

The helper reports:

- database name
- public table count
- whether `_prisma_migrations` is missing, empty, or populated
- whether the environment looks fresh, legacy and unbaselined, partially recorded, already baselined, or unsafe
- the recommended next step

Expected outcomes:

- `fresh_empty`: use normal `prisma migrate deploy`
- `legacy_unbaselined`: continue to `plan`
- `partial_history_prefix`: continue to `plan` so the helper can decide whether this is a resumable baseline or a normal `migrate deploy` environment
- `already_baselined`: use normal `prisma migrate deploy`
- `unexpected_history`: stop and investigate manually

## Step 2: Plan

Run:

```bash
uv run python scripts/prisma/baseline_legacy_environment.py plan
```

The helper will:

- rerun inspection
- run `prisma migrate status` for operator context
- compare `./prisma/migrations` against the live database with `prisma migrate diff`
- compare the checked-in migration chain against `schema.prisma` when a live diff is detected
- if partial Prisma history already exists, compare the live database against that recorded prefix before deciding whether baselining can resume
- refuse to continue if drift is detected
- print the exact ordered list of `resolve --applied` operations it would perform
- print the final `prisma migrate deploy` step

If the live schema does not match the checked-in migration chain, the helper writes the generated SQL diff to a file and exits non-zero.

If the checked-in migration chain does not match `schema.prisma`, the helper also exits non-zero and refuses to baseline until the repository state is reconciled.

Do not continue to `apply` until you understand any diff output.

## Step 3: Apply

Run only after `plan` succeeds:

```bash
uv run python scripts/prisma/baseline_legacy_environment.py apply --yes
```

The helper will:

- rerun the same safety checks as `plan`
- refuse to continue if the environment changed
- mark each checked-in migration as applied in lexical order
- run `prisma migrate deploy`
- run `prisma migrate status`
- print a compact post-baseline summary
- require the final helper classification to end in `already_baselined`

If `--yes` is omitted, the helper refuses to mutate migration history.

## If `apply` Stops Mid-Run

If `apply --yes` fails after recording some migration rows:

- do not improvise extra `migrate resolve --applied` commands
- rerun `plan`
- let the helper decide whether the database is in a safe resumable baseline state or whether it should instead return to normal `migrate deploy`

This is why the helper treats repo-prefix partial history as an analyzed state rather than assuming every partial prefix is already safe.

If the final post-check does not end in `already_baselined`, treat the run as failed even if `resolve`, `deploy`, and `status` completed. Do not assume the baseline is healthy until the helper says so.

## Post-Change Verification

After `apply --yes` succeeds:

1. rerun `inspect` and confirm the database is now classified as `already_baselined`
2. run the normal deployment path for your environment
3. confirm the application reaches its readiness endpoint
4. run a targeted smoke check against the live API or Admin UI

Useful checks:

```bash
uv run python scripts/prisma/baseline_legacy_environment.py inspect
uv run prisma migrate status --schema=./prisma/schema.prisma
curl http://localhost:4000/health/readiness
```

## Rollback Expectations

Baselining changes Prisma migration history. It does not roll back schema objects or restore prior table definitions.

If the helper reports drift, or if post-change validation fails in a way you do not understand:

- stop
- do not improvise additional `resolve` operations
- restore from backup if necessary
- reconcile the schema and migration history deliberately before retrying

## Unsafe Cases

Do not baseline automatically or proceed through this helper when:

- `_prisma_migrations` already contains rows
- the helper reports failed, unfinished, or rolled-back migration history
- `prisma migrate diff` reports live drift
- the helper reports that the checked-in migration chain does not match `schema.prisma`
- the target database is empty and should instead use normal `migrate deploy`
- the target database is not the environment you intended to modify

## Normal Path After Baselining

After a successful baseline, the environment returns to the standard DeltaLLM contract:

- Helm/shared environments: `prisma migrate deploy` before serving traffic
- Docker/local setup: explicit migration step, then startup in verification mode where applicable

You should not need this runbook again for the same environment unless its Prisma migration history is manually altered or lost.
