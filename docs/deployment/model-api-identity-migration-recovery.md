# Recover the v0.1.48 model identity migration

DeltaLLM v0.1.48 added separate user-friendly model names and API model IDs. The migration
`20260927150000_model_api_identity` used the PostgreSQL `pgcrypto.digest` function. An installation
can fail when its migration history says `pgcrypto` was installed but the extension is missing from
the database.

The first failure reports PostgreSQL error `42883`. Later attempts report Prisma error `P3009`
because Prisma will not continue after a failed migration.

## Safety conditions

Stop the application rollout and take a verified database backup before recovery. Run recovery from
one release-scoped migration job. Do not run it concurrently from API or worker replicas.

The recovery command refuses to continue unless all of these conditions are true:

- Prisma has one active failed record for `20260927150000_model_api_identity`.
- Its log contains the known missing `digest(text, unknown)` error.
- Prisma recorded zero applied steps.
- PostgreSQL did not retain the migration's columns or index.

These checks prevent this command from hiding a different migration failure.

## Recovery command

Use the hotfix image and the same `DATABASE_URL` used by the normal migration job:

```bash
python -m src.prisma_bootstrap \
  --schema ./prisma/schema.prisma \
  --recover-model-api-identity \
  --max-attempts 30 \
  --sleep-seconds 2
```

The command performs these operations in order:

1. Verify the exact failed migration state.
2. Install `pgcrypto` if it is missing and verify that `digest(text,text)` is visible.
3. Use `prisma migrate resolve --rolled-back` for only the failed model identity migration.
4. Run the normal `prisma migrate deploy` command.

After it succeeds, start API and worker replicas with the normal command. Do not keep the recovery
flag in their startup configuration.

If the safety check, extension installation, or Prisma resolution fails, the command stops without
running later migrations. Keep the output and inspect the database state before taking another
action. Do not mark this migration as applied and do not edit `_prisma_migrations` directly.

This recovery mode is a bounded compatibility path for the v0.1.48 failure. It can be removed after
v0.1.48 is no longer a supported upgrade source and affected installations have been recovered.
