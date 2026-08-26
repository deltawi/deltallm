ALTER TABLE "deltallm_organizationtable"
  ADD COLUMN IF NOT EXISTS "lifecycle_state" TEXT NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS "lifecycle_version" BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "deletion_requested_at" TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS "deletion_not_before_at" TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS "deletion_job_id" TEXT;

ALTER TABLE "deltallm_organizationtable"
  DROP CONSTRAINT IF EXISTS "deltallm_organizationtable_lifecycle_state_check";

ALTER TABLE "deltallm_organizationtable"
  ADD CONSTRAINT "deltallm_organizationtable_lifecycle_state_check"
  CHECK ("lifecycle_state" IN ('active', 'deletion_pending', 'purging', 'deletion_failed'))
  NOT VALID;

CREATE TABLE IF NOT EXISTS "deltallm_organizationdeletionjob" (
  "deletion_job_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "organization_id" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'pending',
  "phase" TEXT NOT NULL DEFAULT 'cancel_pending',
  "requested_by_account_id" TEXT,
  "idempotency_key" TEXT NOT NULL,
  "request_hash" TEXT NOT NULL,
  "plan_token" TEXT NOT NULL,
  "plan_snapshot" JSONB NOT NULL,
  "options" JSONB NOT NULL DEFAULT '{}'::jsonb,
  "progress" JSONB NOT NULL DEFAULT '{}'::jsonb,
  "not_before_at" TIMESTAMPTZ NOT NULL,
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "max_attempts" INTEGER NOT NULL DEFAULT 20,
  "next_attempt_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "locked_by" TEXT,
  "lease_expires_at" TIMESTAMPTZ,
  "claim_epoch" BIGINT NOT NULL DEFAULT 0,
  "last_error_code" TEXT,
  "last_error_detail" VARCHAR(512),
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "completed_at" TIMESTAMPTZ,
  "restored_at" TIMESTAMPTZ,
  CONSTRAINT "deltallm_organizationdeletionjob_pkey" PRIMARY KEY ("deletion_job_id"),
  CONSTRAINT "deltallm_organizationdeletionjob_status_check"
    CHECK ("status" IN ('pending', 'processing', 'waiting', 'completed', 'failed', 'restored')),
  CONSTRAINT "deltallm_organizationdeletionjob_phase_check"
    CHECK ("phase" IN (
      'cancel_pending',
      'cancel_batches',
      'wait_for_batches',
      'resolve_owned_assets',
      'purge_sensitive_history',
      'remove_scoped_access',
      'revoke_credentials',
      'remove_tenant_state',
      'finalize',
      'completed',
      'restored'
    )),
  CONSTRAINT "deltallm_organizationdeletionjob_attempts_check"
    CHECK ("attempt_count" >= 0 AND "max_attempts" >= 1),
  CONSTRAINT "deltallm_organizationdeletionjob_claim_epoch_check"
    CHECK ("claim_epoch" >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_orgdeletionjob_org_idempotency_key"
  ON "deltallm_organizationdeletionjob" ("organization_id", "idempotency_key");

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_orgdeletionjob_one_active_per_org_idx"
  ON "deltallm_organizationdeletionjob" ("organization_id")
  WHERE "status" IN ('pending', 'processing', 'waiting', 'failed');

CREATE INDEX IF NOT EXISTS "deltallm_orgdeletionjob_due_idx"
  ON "deltallm_organizationdeletionjob" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "deltallm_orgdeletionjob_lease_idx"
  ON "deltallm_organizationdeletionjob" ("lease_expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_orgdeletionjob_org_created_idx"
  ON "deltallm_organizationdeletionjob" ("organization_id", "created_at");

CREATE TABLE IF NOT EXISTS "deltallm_organizationtombstone" (
  "organization_id" TEXT NOT NULL,
  "deletion_job_id" TEXT NOT NULL,
  "deleted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_organizationtombstone_pkey" PRIMARY KEY ("organization_id"),
  CONSTRAINT "deltallm_organizationtombstone_job_key" UNIQUE ("deletion_job_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_organizationtombstone_deleted_idx"
  ON "deltallm_organizationtombstone" ("deleted_at");

CREATE TABLE IF NOT EXISTS "deltallm_teamtombstone" (
  "team_id" TEXT NOT NULL,
  "organization_id" TEXT NOT NULL,
  "deletion_job_id" TEXT NOT NULL,
  "deleted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_teamtombstone_pkey" PRIMARY KEY ("team_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_teamtombstone_org_idx"
  ON "deltallm_teamtombstone" ("organization_id");

CREATE INDEX IF NOT EXISTS "deltallm_teamtombstone_job_idx"
  ON "deltallm_teamtombstone" ("deletion_job_id");

CREATE INDEX IF NOT EXISTS "deltallm_teamtombstone_deleted_idx"
  ON "deltallm_teamtombstone" ("deleted_at");

CREATE TABLE IF NOT EXISTS "deltallm_organizationlifecyclegeneration" (
  "singleton_id" SMALLINT NOT NULL DEFAULT 1,
  "generation" BIGINT NOT NULL DEFAULT 0,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_organizationlifecyclegeneration_pkey" PRIMARY KEY ("singleton_id"),
  CONSTRAINT "deltallm_organizationlifecyclegeneration_singleton_check"
    CHECK ("singleton_id" = 1),
  CONSTRAINT "deltallm_organizationlifecyclegeneration_value_check"
    CHECK ("generation" >= 0)
);

INSERT INTO "deltallm_organizationlifecyclegeneration" (
  "singleton_id",
  "generation",
  "updated_at"
)
VALUES (1, 0, NOW())
ON CONFLICT ("singleton_id") DO NOTHING;

CREATE OR REPLACE FUNCTION deltallm_reject_tombstoned_organization()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM deltallm_organizationtombstone
    WHERE organization_id = NEW.organization_id
  ) THEN
    RAISE EXCEPTION 'organization identifier is permanently tombstoned'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_organizationtable_tombstone_guard
  ON deltallm_organizationtable;
CREATE TRIGGER deltallm_organizationtable_tombstone_guard
BEFORE INSERT OR UPDATE OF organization_id ON deltallm_organizationtable
FOR EACH ROW EXECUTE FUNCTION deltallm_reject_tombstoned_organization();

CREATE OR REPLACE FUNCTION deltallm_reject_tombstoned_team()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM deltallm_teamtombstone
    WHERE team_id = NEW.team_id
  ) THEN
    RAISE EXCEPTION 'team identifier is permanently tombstoned'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_teamtable_tombstone_guard
  ON deltallm_teamtable;
CREATE TRIGGER deltallm_teamtable_tombstone_guard
BEFORE INSERT OR UPDATE OF team_id ON deltallm_teamtable
FOR EACH ROW EXECUTE FUNCTION deltallm_reject_tombstoned_team();

CREATE OR REPLACE FUNCTION deltallm_guard_inactive_organization_update()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.lifecycle_state <> 'active' AND (
    to_jsonb(NEW) - ARRAY[
      'lifecycle_state',
      'lifecycle_version',
      'deletion_requested_at',
      'deletion_not_before_at',
      'deletion_job_id',
      'updated_at'
    ]::text[]
  ) IS DISTINCT FROM (
    to_jsonb(OLD) - ARRAY[
      'lifecycle_state',
      'lifecycle_version',
      'deletion_requested_at',
      'deletion_not_before_at',
      'deletion_job_id',
      'updated_at'
    ]::text[]
  ) THEN
    RAISE EXCEPTION 'inactive organization cannot be modified'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_organizationtable_inactive_update_guard
  ON deltallm_organizationtable;
CREATE TRIGGER deltallm_organizationtable_inactive_update_guard
BEFORE UPDATE ON deltallm_organizationtable
FOR EACH ROW EXECUTE FUNCTION deltallm_guard_inactive_organization_update();

CREATE OR REPLACE FUNCTION deltallm_reject_organization_delete_with_teams()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM deltallm_teamtable t
    WHERE t.organization_id = OLD.organization_id
  ) THEN
    RAISE EXCEPTION 'organization still has referenced teams'
      USING ERRCODE = '23503';
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_organizationtable_team_reference_guard
  ON deltallm_organizationtable;
CREATE TRIGGER deltallm_organizationtable_team_reference_guard
BEFORE DELETE ON deltallm_organizationtable
FOR EACH ROW EXECUTE FUNCTION deltallm_reject_organization_delete_with_teams();

CREATE OR REPLACE FUNCTION deltallm_require_active_organization_id()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.organization_id IS NOT NULL
       AND OLD.organization_id IS DISTINCT FROM NEW.organization_id THEN
      PERFORM 1 FROM deltallm_organizationtable o
      WHERE o.organization_id = OLD.organization_id
        AND o.lifecycle_state = 'active'
      FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'organization is not active'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  IF NEW.organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = NEW.organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_teamtable_active_org_guard ON deltallm_teamtable;
CREATE TRIGGER deltallm_teamtable_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_teamtable
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_organization_id();

DROP TRIGGER IF EXISTS deltallm_orgmembership_active_org_guard
  ON deltallm_organizationmembership;
CREATE TRIGGER deltallm_orgmembership_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_organizationmembership
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_organization_id();

DROP TRIGGER IF EXISTS deltallm_orgtierassignment_active_org_guard
  ON deltallm_organizationtierassignment;
CREATE TRIGGER deltallm_orgtierassignment_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_organizationtierassignment
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_organization_id();

CREATE OR REPLACE FUNCTION deltallm_require_active_team_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.team_id IS DISTINCT FROM NEW.team_id THEN
    SELECT organization_id INTO previous_organization_id
    FROM deltallm_teamtable WHERE team_id = OLD.team_id
    FOR SHARE;
    IF previous_organization_id IS NOT NULL THEN
      PERFORM 1 FROM deltallm_organizationtable o
      WHERE o.organization_id = previous_organization_id
        AND o.lifecycle_state = 'active'
      FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'team organization is not active'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  SELECT organization_id INTO target_organization_id
  FROM deltallm_teamtable WHERE team_id = NEW.team_id
  FOR SHARE;
  IF target_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'team organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_serviceaccount_active_org_guard
  ON deltallm_serviceaccount;
CREATE TRIGGER deltallm_serviceaccount_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_serviceaccount
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_team_organization();

DROP TRIGGER IF EXISTS deltallm_teammembership_active_org_guard
  ON deltallm_teammembership;
CREATE TRIGGER deltallm_teammembership_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_teammembership
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_team_organization();

CREATE OR REPLACE FUNCTION deltallm_require_active_key_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_team_id TEXT;
  previous_organization_id TEXT;
  target_team_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    previous_team_id := OLD.team_id;
    IF previous_team_id IS NULL AND OLD.user_id IS NOT NULL THEN
      SELECT team_id INTO previous_team_id
      FROM deltallm_usertable WHERE user_id = OLD.user_id
      FOR SHARE;
    END IF;
    IF previous_team_id IS NULL AND OLD.owner_service_account_id IS NOT NULL THEN
      SELECT team_id INTO previous_team_id
      FROM deltallm_serviceaccount
      WHERE service_account_id = OLD.owner_service_account_id
      FOR SHARE;
    END IF;
    IF previous_team_id IS NOT NULL THEN
      SELECT organization_id INTO previous_organization_id
      FROM deltallm_teamtable WHERE team_id = previous_team_id
      FOR SHARE;
    END IF;
    IF previous_organization_id IS NOT NULL THEN
      PERFORM 1 FROM deltallm_organizationtable o
      WHERE o.organization_id = previous_organization_id
        AND o.lifecycle_state = 'active'
      FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'key organization is not active'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  target_team_id := NEW.team_id;
  IF target_team_id IS NULL AND NEW.user_id IS NOT NULL THEN
    SELECT team_id INTO target_team_id
    FROM deltallm_usertable WHERE user_id = NEW.user_id
    FOR SHARE;
  END IF;
  IF target_team_id IS NULL AND NEW.owner_service_account_id IS NOT NULL THEN
    SELECT team_id INTO target_team_id
    FROM deltallm_serviceaccount
    WHERE service_account_id = NEW.owner_service_account_id
    FOR SHARE;
  END IF;
  IF target_team_id IS NOT NULL THEN
    SELECT organization_id INTO target_organization_id
    FROM deltallm_teamtable WHERE team_id = target_team_id
    FOR SHARE;
  END IF;
  IF target_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'key organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_verificationtoken_active_org_guard
  ON deltallm_verificationtoken;
CREATE TRIGGER deltallm_verificationtoken_active_org_guard
BEFORE INSERT OR UPDATE
ON deltallm_verificationtoken
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_key_organization();

CREATE OR REPLACE FUNCTION deltallm_resolve_scope_organization(
  requested_scope_type TEXT,
  requested_scope_id TEXT
)
RETURNS TEXT AS $$
DECLARE
  resolved_organization_id TEXT;
BEGIN
  IF requested_scope_type = 'organization' THEN
    RETURN requested_scope_id;
  ELSIF requested_scope_type = 'team' THEN
    SELECT t.organization_id INTO resolved_organization_id
    FROM deltallm_teamtable t
    WHERE t.team_id = requested_scope_id;
  ELSIF requested_scope_type = 'api_key' THEN
    SELECT t.organization_id INTO resolved_organization_id
    FROM deltallm_verificationtoken v
    LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
    LEFT JOIN deltallm_serviceaccount s
      ON s.service_account_id = v.owner_service_account_id
    LEFT JOIN deltallm_teamtable t
      ON t.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
    WHERE v.token = requested_scope_id
    LIMIT 1;
  ELSIF requested_scope_type = 'user' THEN
    SELECT t.organization_id INTO resolved_organization_id
    FROM deltallm_usertable u
    LEFT JOIN deltallm_teamtable t ON t.team_id = u.team_id
    WHERE u.user_id = requested_scope_id
    LIMIT 1;
  END IF;
  RETURN resolved_organization_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_scope_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    previous_organization_id := deltallm_resolve_scope_organization(
      OLD.scope_type,
      OLD.scope_id
    );
    IF previous_organization_id IS NOT NULL THEN
      PERFORM 1 FROM deltallm_organizationtable o
      WHERE o.organization_id = previous_organization_id
        AND o.lifecycle_state = 'active'
      FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'scope organization is not active'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;

  target_organization_id := deltallm_resolve_scope_organization(
    NEW.scope_type,
    NEW.scope_id
  );
  IF target_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'scope organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'deltallm_routegroupbinding',
    'deltallm_callabletargetbinding',
    'deltallm_callabletargetaccessgroupbinding',
    'deltallm_callabletargetscopepolicy',
    'deltallm_mcpbinding',
    'deltallm_mcpscopepolicy',
    'deltallm_mcptoolpolicy',
    'deltallm_promptbinding'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS deltallm_active_scope_org_guard ON %I',
      target_table
    );
    EXECUTE format(
      'CREATE TRIGGER deltallm_active_scope_org_guard '
      'BEFORE INSERT OR UPDATE ON %I '
      'FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_scope_organization()',
      target_table
    );
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_require_active_approval_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
  previous_owner_organization_id TEXT;
  target_owner_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' AND (
    to_jsonb(NEW) - ARRAY[
      'status',
      'decision_comment',
      'decided_by_account_id',
      'decided_at',
      'expires_at',
      'updated_at'
    ]::text[]
  ) IS NOT DISTINCT FROM (
    to_jsonb(OLD) - ARRAY[
      'status',
      'decision_comment',
      'decided_by_account_id',
      'decided_at',
      'expires_at',
      'updated_at'
    ]::text[]
  ) THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    previous_organization_id := OLD.organization_id;
    IF previous_organization_id IS NULL THEN
      previous_organization_id := deltallm_resolve_scope_organization(
        OLD.scope_type,
        OLD.scope_id
      );
    END IF;
    IF previous_organization_id IS NULL AND OLD.requested_by_api_key IS NOT NULL THEN
      previous_organization_id := deltallm_resolve_scope_organization(
        'api_key',
        OLD.requested_by_api_key
      );
    END IF;
    IF previous_organization_id IS NULL AND OLD.requested_by_user IS NOT NULL THEN
      previous_organization_id := deltallm_resolve_scope_organization(
        'user',
        OLD.requested_by_user
      );
    END IF;
    SELECT s.owner_scope_id INTO previous_owner_organization_id
    FROM deltallm_mcpserver s
    WHERE s.mcp_server_id = OLD.mcp_server_id
      AND s.owner_scope_type = 'organization';
  END IF;

  target_organization_id := NEW.organization_id;
  IF target_organization_id IS NULL THEN
    target_organization_id := deltallm_resolve_scope_organization(
      NEW.scope_type,
      NEW.scope_id
    );
  END IF;
  IF target_organization_id IS NULL AND NEW.requested_by_api_key IS NOT NULL THEN
    target_organization_id := deltallm_resolve_scope_organization(
      'api_key',
      NEW.requested_by_api_key
    );
  END IF;
  IF target_organization_id IS NULL AND NEW.requested_by_user IS NOT NULL THEN
    target_organization_id := deltallm_resolve_scope_organization(
      'user',
      NEW.requested_by_user
    );
  END IF;
  SELECT s.owner_scope_id INTO target_owner_organization_id
  FROM deltallm_mcpserver s
  WHERE s.mcp_server_id = NEW.mcp_server_id
    AND s.owner_scope_type = 'organization';

  IF previous_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = previous_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approval organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  IF previous_owner_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = previous_owner_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approval organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  IF target_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approval organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  IF target_owner_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_owner_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approval organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_mcpapproval_active_org_guard
  ON deltallm_mcpapprovalrequest;
CREATE TRIGGER deltallm_mcpapproval_active_org_guard
BEFORE INSERT OR UPDATE ON deltallm_mcpapprovalrequest
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_approval_organization();

CREATE OR REPLACE FUNCTION deltallm_require_active_owned_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF TG_TABLE_NAME = 'deltallm_mcpserver' THEN
      IF OLD.owner_scope_type = 'organization' THEN
        previous_organization_id := OLD.owner_scope_id;
      END IF;
    ELSIF TG_TABLE_NAME = 'deltallm_prompttemplate' THEN
      IF OLD.owner_scope = 'organization' THEN
        previous_organization_id := OLD.metadata #>> '{_asset_governance,owner_scope_id}';
      END IF;
    ELSIF TG_TABLE_NAME = 'deltallm_routegroup' THEN
      IF OLD.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization' THEN
        previous_organization_id := OLD.metadata #>> '{_asset_governance,owner_scope_id}';
      END IF;
    END IF;
    IF previous_organization_id IS NOT NULL THEN
      PERFORM 1 FROM deltallm_organizationtable o
      WHERE o.organization_id = previous_organization_id
        AND o.lifecycle_state = 'active'
      FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'asset owner organization is not active'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'deltallm_mcpserver' THEN
    IF NEW.owner_scope_type = 'organization' THEN
      target_organization_id := NEW.owner_scope_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'deltallm_prompttemplate' THEN
    IF NEW.owner_scope = 'organization' THEN
      target_organization_id := NEW.metadata #>> '{_asset_governance,owner_scope_id}';
    END IF;
  ELSIF TG_TABLE_NAME = 'deltallm_routegroup' THEN
    IF NEW.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization' THEN
      target_organization_id := NEW.metadata #>> '{_asset_governance,owner_scope_id}';
    END IF;
  END IF;
  IF target_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = target_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'asset owner organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_mcpserver_active_owner_guard ON deltallm_mcpserver;
CREATE TRIGGER deltallm_mcpserver_active_owner_guard
BEFORE INSERT OR UPDATE ON deltallm_mcpserver
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_owned_organization();

DROP TRIGGER IF EXISTS deltallm_prompttemplate_active_owner_guard
  ON deltallm_prompttemplate;
CREATE TRIGGER deltallm_prompttemplate_active_owner_guard
BEFORE INSERT OR UPDATE ON deltallm_prompttemplate
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_owned_organization();

DROP TRIGGER IF EXISTS deltallm_routegroup_active_owner_guard
  ON deltallm_routegroup;
CREATE TRIGGER deltallm_routegroup_active_owner_guard
BEFORE INSERT OR UPDATE ON deltallm_routegroup
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_owned_organization();

CREATE OR REPLACE FUNCTION deltallm_require_active_referenced_asset_owner()
RETURNS TRIGGER AS $$
DECLARE
  owner_organization_id TEXT;
BEGIN
  IF TG_TABLE_NAME = 'deltallm_mcpapprovalrequest' THEN
    IF TG_OP = 'UPDATE' AND (
      to_jsonb(NEW) - ARRAY[
        'status',
        'decision_comment',
        'decided_by_account_id',
        'decided_at',
        'expires_at',
        'updated_at'
      ]::text[]
    ) IS NOT DISTINCT FROM (
      to_jsonb(OLD) - ARRAY[
        'status',
        'decision_comment',
        'decided_by_account_id',
        'decided_at',
        'expires_at',
        'updated_at'
      ]::text[]
    ) THEN
      RETURN NEW;
    END IF;
  END IF;

  IF TG_TABLE_NAME IN (
    'deltallm_mcpbinding',
    'deltallm_mcptoolpolicy',
    'deltallm_mcpapprovalrequest'
  ) THEN
    SELECT owner_scope_id INTO owner_organization_id
    FROM deltallm_mcpserver
    WHERE mcp_server_id = NEW.mcp_server_id
      AND owner_scope_type = 'organization'
    FOR SHARE;
  ELSIF TG_TABLE_NAME = 'deltallm_promptbinding' THEN
    SELECT metadata #>> '{_asset_governance,owner_scope_id}'
      INTO owner_organization_id
    FROM deltallm_prompttemplate
    WHERE prompt_template_id = NEW.prompt_template_id
      AND owner_scope = 'organization'
    FOR SHARE;
  ELSIF TG_TABLE_NAME = 'deltallm_routegroupbinding' THEN
    SELECT metadata #>> '{_asset_governance,owner_scope_id}'
      INTO owner_organization_id
    FROM deltallm_routegroup
    WHERE route_group_id = NEW.route_group_id
      AND metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
    FOR SHARE;
  ELSIF TG_TABLE_NAME = 'deltallm_callabletargetbinding' THEN
    SELECT metadata #>> '{_asset_governance,owner_scope_id}'
      INTO owner_organization_id
    FROM deltallm_routegroup
    WHERE group_key = NEW.callable_key
      AND metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
    FOR SHARE;
  ELSIF TG_TABLE_NAME = 'deltallm_promptversion' THEN
    SELECT metadata #>> '{_asset_governance,owner_scope_id}'
      INTO owner_organization_id
    FROM deltallm_routegroup
    WHERE group_key = NEW.route_preferences->>'route_group'
      AND metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
    FOR SHARE;
  ELSIF TG_TABLE_NAME = 'deltallm_routegroup' THEN
    SELECT metadata #>> '{_asset_governance,owner_scope_id}'
      INTO owner_organization_id
    FROM deltallm_prompttemplate
    WHERE template_key = NEW.metadata #>> '{default_prompt,template_key}'
      AND owner_scope = 'organization'
    FOR SHARE;
  END IF;

  IF owner_organization_id IS NOT NULL THEN
    PERFORM 1 FROM deltallm_organizationtable o
    WHERE o.organization_id = owner_organization_id
      AND o.lifecycle_state = 'active'
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'asset owner organization is not active'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'deltallm_mcpbinding',
    'deltallm_mcptoolpolicy',
    'deltallm_mcpapprovalrequest',
    'deltallm_promptbinding',
    'deltallm_routegroupbinding',
    'deltallm_callabletargetbinding',
    'deltallm_promptversion',
    'deltallm_routegroup'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS deltallm_active_asset_owner_guard ON %I',
      target_table
    );
    EXECUTE format(
      'CREATE TRIGGER deltallm_active_asset_owner_guard '
      'BEFORE INSERT OR UPDATE ON %I '
      'FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_referenced_asset_owner()',
      target_table
    );
  END LOOP;
END;
$$;
