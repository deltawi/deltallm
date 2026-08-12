-- Prisma applies this migration transactionally. Bound lock acquisition so an
-- ACCESS EXCLUSIVE request cannot queue gateway writes behind a busy table.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

ALTER TABLE "deltallm_spendlog_events"
ADD COLUMN IF NOT EXISTS "owner_account_id" TEXT;

ALTER TABLE "deltallm_batch_job"
ADD COLUMN IF NOT EXISTS "created_by_owner_account_id" TEXT,
ADD COLUMN IF NOT EXISTS "created_by_owner_snapshot_complete" BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE "deltallm_batch_create_session"
ADD COLUMN IF NOT EXISTS "created_by_owner_account_id" TEXT,
ADD COLUMN IF NOT EXISTS "created_by_owner_snapshot_complete" BOOLEAN NOT NULL DEFAULT FALSE;

-- Legacy rows deliberately remain NULL. Current key ownership is not reliable
-- evidence of who owned a key when a historical request was made.

-- Compatibility shims for a rolling writer deployment. Upgraded writers set
-- snapshot_complete even when the authenticated key is deliberately ownerless.
-- Old replicas omit it, so the trigger snapshots ownership at INSERT time.
CREATE OR REPLACE FUNCTION "deltallm_snapshot_batch_session_owner_account_id"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT NEW."created_by_owner_snapshot_complete" THEN
        IF NEW."created_by_api_key" IS NOT NULL THEN
            SELECT token."owner_account_id"
              INTO NEW."created_by_owner_account_id"
              FROM "deltallm_verificationtoken" AS token
             WHERE token."token" = NEW."created_by_api_key"
             LIMIT 1;
        END IF;
        NEW."created_by_owner_snapshot_complete" := TRUE;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS "deltallm_batch_create_session_owner_snapshot" ON "deltallm_batch_create_session";
CREATE TRIGGER "deltallm_batch_create_session_owner_snapshot"
BEFORE INSERT ON "deltallm_batch_create_session"
FOR EACH ROW
EXECUTE FUNCTION "deltallm_snapshot_batch_session_owner_account_id"();

CREATE OR REPLACE FUNCTION "deltallm_snapshot_batch_job_owner_account_id"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW."created_by_owner_snapshot_complete" THEN
        RETURN NEW;
    END IF;

    -- An existing create session is the closest available request-time record.
    -- Its NULL is authoritative: never replace it with a later key owner.
    SELECT session."created_by_owner_account_id"
      INTO NEW."created_by_owner_account_id"
      FROM "deltallm_batch_create_session" AS session
     WHERE session."target_batch_id" = NEW."batch_id"
     LIMIT 1;

    IF NOT FOUND AND NEW."created_by_api_key" IS NOT NULL THEN
        -- Direct legacy job writers have no create session. Snapshot the key at
        -- job insertion time as the rolling-deploy compatibility fallback.
        SELECT token."owner_account_id"
          INTO NEW."created_by_owner_account_id"
          FROM "deltallm_verificationtoken" AS token
         WHERE token."token" = NEW."created_by_api_key"
         LIMIT 1;
    END IF;

    NEW."created_by_owner_snapshot_complete" := TRUE;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS "deltallm_batch_job_owner_snapshot" ON "deltallm_batch_job";
CREATE TRIGGER "deltallm_batch_job_owner_snapshot"
BEFORE INSERT ON "deltallm_batch_job"
FOR EACH ROW
EXECUTE FUNCTION "deltallm_snapshot_batch_job_owner_account_id"();

DROP FUNCTION IF EXISTS "deltallm_snapshot_batch_owner_account_id"();

CREATE OR REPLACE FUNCTION "deltallm_snapshot_spend_owner_account_id"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    event_batch_id TEXT;
BEGIN
    IF NEW."owner_account_id" IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Upgraded writers add this marker even when the authenticated key has no
    -- account owner. Avoid a verification-token lookup on that steady-state
    -- path; only compatibility writes from old replicas need the fallback.
    IF COALESCE(NEW."metadata"->>'_deltallm_reporting_writer_version', '') = '2' THEN
        RETURN NEW;
    END IF;

    event_batch_id := NULLIF(NEW."metadata"->>'batch_id', '');
    IF event_batch_id IS NOT NULL THEN
        SELECT job."created_by_owner_account_id"
          INTO NEW."owner_account_id"
          FROM "deltallm_batch_job" AS job
         WHERE job."batch_id" = event_batch_id
         LIMIT 1;

        -- A matching job is authoritative even when its immutable snapshot is
        -- NULL. Falling through would assign usage to a later key owner.
        IF FOUND THEN
            RETURN NEW;
        END IF;
    END IF;

    IF NEW."owner_account_id" IS NULL AND NEW."api_key" IS NOT NULL THEN
        SELECT token."owner_account_id"
          INTO NEW."owner_account_id"
          FROM "deltallm_verificationtoken" AS token
         WHERE token."token" = NEW."api_key"
         LIMIT 1;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS "deltallm_spend_event_owner_snapshot" ON "deltallm_spendlog_events";
CREATE TRIGGER "deltallm_spend_event_owner_snapshot"
BEFORE INSERT ON "deltallm_spendlog_events"
FOR EACH ROW
EXECUTE FUNCTION "deltallm_snapshot_spend_owner_account_id"();
