-- Baseline tables that historically existed before the tracked Prisma migration chain.
-- Keep these definitions compatible with the earliest tracked feature migrations:
-- later migrations still add service-account ownership to keys, named credentials on
-- model deployments, and the BatchJob status enum conversion.

CREATE TABLE IF NOT EXISTS "deltallm_organizationtable" (
  "id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "organization_id" TEXT NOT NULL,
  "organization_name" TEXT,
  "max_budget" DOUBLE PRECISION,
  "soft_budget" DOUBLE PRECISION,
  "rpm_limit" INTEGER,
  "tpm_limit" INTEGER,
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "budget_duration" TEXT,
  "budget_reset_at" TIMESTAMP(3),
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_organizationtable_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_organizationtable_organization_id_key"
  ON "deltallm_organizationtable" ("organization_id");

CREATE TABLE IF NOT EXISTS "deltallm_teamtable" (
  "team_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "team_alias" TEXT,
  "organization_id" TEXT,
  "max_budget" DOUBLE PRECISION,
  "soft_budget" DOUBLE PRECISION,
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "budget_duration" TEXT,
  "budget_reset_at" TIMESTAMP(3),
  "model_max_budget" JSONB,
  "tpm_limit" INTEGER,
  "rpm_limit" INTEGER,
  "models" TEXT[],
  "blocked" BOOLEAN NOT NULL DEFAULT false,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_teamtable_pkey" PRIMARY KEY ("team_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_teamtable_organization_id_idx"
  ON "deltallm_teamtable" ("organization_id");

CREATE TABLE IF NOT EXISTS "deltallm_usertable" (
  "user_id" TEXT NOT NULL,
  "user_email" TEXT,
  "user_role" TEXT NOT NULL DEFAULT 'internal_user',
  "max_budget" DOUBLE PRECISION,
  "soft_budget" DOUBLE PRECISION,
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "budget_duration" TEXT,
  "budget_reset_at" TIMESTAMP(3),
  "models" TEXT[],
  "tpm_limit" INTEGER,
  "rpm_limit" INTEGER,
  "team_id" TEXT,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_usertable_pkey" PRIMARY KEY ("user_id"),
  CONSTRAINT "deltallm_usertable_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "deltallm_teamtable"("team_id")
    ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_usertable_user_email_key"
  ON "deltallm_usertable" ("user_email");

CREATE INDEX IF NOT EXISTS "deltallm_usertable_team_id_idx"
  ON "deltallm_usertable" ("team_id");

CREATE TABLE IF NOT EXISTS "deltallm_verificationtoken" (
  "id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "token" TEXT NOT NULL,
  "key_name" TEXT,
  "user_id" TEXT,
  "team_id" TEXT,
  "models" TEXT[],
  "max_budget" DOUBLE PRECISION,
  "soft_budget" DOUBLE PRECISION,
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "budget_duration" TEXT,
  "budget_reset_at" TIMESTAMP(3),
  "rpm_limit" INTEGER,
  "tpm_limit" INTEGER,
  "max_parallel_requests" INTEGER,
  "expires" TIMESTAMP(3),
  "permissions" JSONB,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_verificationtoken_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "deltallm_verificationtoken_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "deltallm_usertable"("user_id")
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT "deltallm_verificationtoken_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "deltallm_teamtable"("team_id")
    ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_verificationtoken_token_key"
  ON "deltallm_verificationtoken" ("token");

CREATE INDEX IF NOT EXISTS "deltallm_verificationtoken_user_id_idx"
  ON "deltallm_verificationtoken" ("user_id");

CREATE INDEX IF NOT EXISTS "deltallm_verificationtoken_team_id_idx"
  ON "deltallm_verificationtoken" ("team_id");

CREATE INDEX IF NOT EXISTS "deltallm_verificationtoken_expires_idx"
  ON "deltallm_verificationtoken" ("expires");

CREATE TABLE IF NOT EXISTS "deltallm_platformaccount" (
  "account_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "email" TEXT NOT NULL,
  "password_hash" TEXT,
  "role" TEXT NOT NULL DEFAULT 'org_user',
  "is_active" BOOLEAN NOT NULL DEFAULT true,
  "force_password_change" BOOLEAN NOT NULL DEFAULT false,
  "mfa_enabled" BOOLEAN NOT NULL DEFAULT false,
  "mfa_secret" TEXT,
  "mfa_pending_secret" TEXT,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_login_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_platformaccount_pkey" PRIMARY KEY ("account_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_platformaccount_email_key"
  ON "deltallm_platformaccount" ("email");

CREATE TABLE IF NOT EXISTS "deltallm_platformsession" (
  "session_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "account_id" TEXT NOT NULL,
  "session_token_hash" TEXT NOT NULL,
  "mfa_verified" BOOLEAN NOT NULL DEFAULT false,
  "expires_at" TIMESTAMP(3) NOT NULL,
  "revoked_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_platformsession_pkey" PRIMARY KEY ("session_id"),
  CONSTRAINT "deltallm_platformsession_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_platformsession_session_token_hash_key"
  ON "deltallm_platformsession" ("session_token_hash");

CREATE INDEX IF NOT EXISTS "deltallm_platformsession_account_id_idx"
  ON "deltallm_platformsession" ("account_id");

CREATE INDEX IF NOT EXISTS "deltallm_platformsession_expires_at_idx"
  ON "deltallm_platformsession" ("expires_at");

CREATE TABLE IF NOT EXISTS "deltallm_platformidentity" (
  "identity_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "account_id" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "subject" TEXT NOT NULL,
  "email" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_platformidentity_pkey" PRIMARY KEY ("identity_id"),
  CONSTRAINT "deltallm_platformidentity_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_platformidentity_provider_subject_key"
  ON "deltallm_platformidentity" ("provider", "subject");

CREATE INDEX IF NOT EXISTS "deltallm_platformidentity_account_id_idx"
  ON "deltallm_platformidentity" ("account_id");

CREATE TABLE IF NOT EXISTS "deltallm_organizationmembership" (
  "membership_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "account_id" TEXT NOT NULL,
  "organization_id" TEXT NOT NULL,
  "role" TEXT NOT NULL DEFAULT 'org_member',
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_organizationmembership_pkey" PRIMARY KEY ("membership_id"),
  CONSTRAINT "deltallm_organizationmembership_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_organizationmembership_account_id_organization_id_key"
  ON "deltallm_organizationmembership" ("account_id", "organization_id");

CREATE INDEX IF NOT EXISTS "deltallm_organizationmembership_organization_id_idx"
  ON "deltallm_organizationmembership" ("organization_id");

CREATE TABLE IF NOT EXISTS "deltallm_teammembership" (
  "membership_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "account_id" TEXT NOT NULL,
  "team_id" TEXT NOT NULL,
  "role" TEXT NOT NULL DEFAULT 'team_viewer',
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_teammembership_pkey" PRIMARY KEY ("membership_id"),
  CONSTRAINT "deltallm_teammembership_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_teammembership_account_id_team_id_key"
  ON "deltallm_teammembership" ("account_id", "team_id");

CREATE INDEX IF NOT EXISTS "deltallm_teammembership_team_id_idx"
  ON "deltallm_teammembership" ("team_id");

CREATE TABLE IF NOT EXISTS "deltallm_config" (
  "config_name" TEXT NOT NULL,
  "config_value" TEXT NOT NULL,
  "updated_by" TEXT,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_config_pkey" PRIMARY KEY ("config_name")
);

CREATE TABLE IF NOT EXISTS "deltallm_spendlogs" (
  "id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "request_id" TEXT,
  "call_type" TEXT,
  "api_key" TEXT,
  "user" TEXT,
  "team_id" TEXT,
  "end_user" TEXT,
  "model" TEXT,
  "start_time" TIMESTAMP(3),
  "end_time" TIMESTAMP(3),
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "total_tokens" INTEGER NOT NULL DEFAULT 0,
  "prompt_tokens" INTEGER NOT NULL DEFAULT 0,
  "completion_tokens" INTEGER NOT NULL DEFAULT 0,
  "request_tags" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  CONSTRAINT "deltallm_spendlogs_pkey" PRIMARY KEY ("id")
);

CREATE TABLE IF NOT EXISTS "deltallm_modeldeployment" (
  "deployment_id" TEXT NOT NULL,
  "model_name" TEXT NOT NULL,
  "deltallm_params" JSONB NOT NULL,
  "model_info" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_modeldeployment_pkey" PRIMARY KEY ("deployment_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_modeldeployment_model_name_idx"
  ON "deltallm_modeldeployment" ("model_name");

CREATE TABLE IF NOT EXISTS "deltallm_routegroup" (
  "route_group_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "group_key" TEXT NOT NULL,
  "name" TEXT,
  "mode" TEXT NOT NULL DEFAULT 'chat',
  "routing_strategy" TEXT,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_routegroup_pkey" PRIMARY KEY ("route_group_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_routegroup_group_key_key"
  ON "deltallm_routegroup" ("group_key");

CREATE INDEX IF NOT EXISTS "deltallm_routegroup_enabled_idx"
  ON "deltallm_routegroup" ("enabled");

CREATE TABLE IF NOT EXISTS "deltallm_routegroupbinding" (
  "route_group_binding_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "route_group_id" TEXT NOT NULL,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_routegroupbinding_pkey" PRIMARY KEY ("route_group_binding_id"),
  CONSTRAINT "deltallm_routegroupbinding_route_group_id_fkey"
    FOREIGN KEY ("route_group_id") REFERENCES "deltallm_routegroup"("route_group_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_routegroupbinding_scope_target_key"
  ON "deltallm_routegroupbinding" ("route_group_id", "scope_type", "scope_id");

CREATE INDEX IF NOT EXISTS "deltallm_routegroupbinding_scope_idx"
  ON "deltallm_routegroupbinding" ("scope_type", "scope_id", "enabled");

CREATE INDEX IF NOT EXISTS "deltallm_routegroupbinding_group_idx"
  ON "deltallm_routegroupbinding" ("route_group_id");

CREATE TABLE IF NOT EXISTS "deltallm_callabletargetbinding" (
  "callable_target_binding_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "callable_key" TEXT NOT NULL,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_callabletargetbinding_pkey" PRIMARY KEY ("callable_target_binding_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_callabletargetbinding_scope_target_key"
  ON "deltallm_callabletargetbinding" ("callable_key", "scope_type", "scope_id");

CREATE INDEX IF NOT EXISTS "deltallm_callabletargetbinding_scope_idx"
  ON "deltallm_callabletargetbinding" ("scope_type", "scope_id", "enabled");

CREATE INDEX IF NOT EXISTS "deltallm_callabletargetbinding_target_idx"
  ON "deltallm_callabletargetbinding" ("callable_key");

CREATE TABLE IF NOT EXISTS "deltallm_callabletargetscopepolicy" (
  "callable_target_scope_policy_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "mode" TEXT NOT NULL DEFAULT 'inherit',
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_callabletargetscopepolicy_pkey" PRIMARY KEY ("callable_target_scope_policy_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_callabletargetscopepolicy_scope_key"
  ON "deltallm_callabletargetscopepolicy" ("scope_type", "scope_id");

CREATE INDEX IF NOT EXISTS "deltallm_callabletargetscopepolicy_scope_idx"
  ON "deltallm_callabletargetscopepolicy" ("scope_type", "scope_id");

CREATE TABLE IF NOT EXISTS "deltallm_mcpscopepolicy" (
  "mcp_scope_policy_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "mode" TEXT NOT NULL DEFAULT 'inherit',
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcpscopepolicy_pkey" PRIMARY KEY ("mcp_scope_policy_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_mcpscopepolicy_scope_key"
  ON "deltallm_mcpscopepolicy" ("scope_type", "scope_id");

CREATE INDEX IF NOT EXISTS "deltallm_mcpscopepolicy_scope_idx"
  ON "deltallm_mcpscopepolicy" ("scope_type", "scope_id");

CREATE TABLE IF NOT EXISTS "deltallm_batch_file" (
  "file_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "purpose" TEXT NOT NULL,
  "filename" TEXT NOT NULL,
  "bytes" INTEGER NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'processed',
  "storage_backend" TEXT NOT NULL DEFAULT 'local',
  "storage_key" TEXT NOT NULL,
  "checksum" TEXT,
  "created_by_api_key" TEXT,
  "created_by_user_id" TEXT,
  "created_by_team_id" TEXT,
  "created_by_organization_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expires_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_batch_file_pkey" PRIMARY KEY ("file_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_batch_file_purpose_idx"
  ON "deltallm_batch_file" ("purpose");

CREATE INDEX IF NOT EXISTS "deltallm_batch_file_created_at_idx"
  ON "deltallm_batch_file" ("created_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_file_expires_at_idx"
  ON "deltallm_batch_file" ("expires_at");

CREATE TABLE IF NOT EXISTS "deltallm_batch_job" (
  "batch_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "endpoint" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'validating',
  "execution_mode" TEXT NOT NULL DEFAULT 'managed_internal',
  "input_file_id" TEXT NOT NULL,
  "output_file_id" TEXT,
  "error_file_id" TEXT,
  "model" TEXT,
  "metadata" JSONB,
  "provider_batch_id" TEXT,
  "provider_status" TEXT,
  "provider_error" TEXT,
  "provider_last_sync_at" TIMESTAMP(3),
  "total_items" INTEGER NOT NULL DEFAULT 0,
  "in_progress_items" INTEGER NOT NULL DEFAULT 0,
  "completed_items" INTEGER NOT NULL DEFAULT 0,
  "failed_items" INTEGER NOT NULL DEFAULT 0,
  "cancelled_items" INTEGER NOT NULL DEFAULT 0,
  "locked_by" TEXT,
  "lease_expires_at" TIMESTAMP(3),
  "cancel_requested_at" TIMESTAMP(3),
  "status_last_updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "created_by_api_key" TEXT,
  "created_by_user_id" TEXT,
  "created_by_team_id" TEXT,
  "created_by_organization_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "started_at" TIMESTAMP(3),
  "completed_at" TIMESTAMP(3),
  "expires_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_batch_job_pkey" PRIMARY KEY ("batch_id"),
  CONSTRAINT "deltallm_batch_job_input_file_id_fkey"
    FOREIGN KEY ("input_file_id") REFERENCES "deltallm_batch_file"("file_id")
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT "deltallm_batch_job_output_file_id_fkey"
    FOREIGN KEY ("output_file_id") REFERENCES "deltallm_batch_file"("file_id")
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT "deltallm_batch_job_error_file_id_fkey"
    FOREIGN KEY ("error_file_id") REFERENCES "deltallm_batch_file"("file_id")
    ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_status_created_at_idx"
  ON "deltallm_batch_job" ("status", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_lease_expires_at_idx"
  ON "deltallm_batch_job" ("lease_expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_created_by_api_key_created_at_idx"
  ON "deltallm_batch_job" ("created_by_api_key", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_created_by_team_id_created_at_idx"
  ON "deltallm_batch_job" ("created_by_team_id", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_created_by_organization_id_created_at_idx"
  ON "deltallm_batch_job" ("created_by_organization_id", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_expires_at_idx"
  ON "deltallm_batch_job" ("expires_at");

CREATE TABLE IF NOT EXISTS "deltallm_batch_item" (
  "item_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "batch_id" TEXT NOT NULL,
  "line_number" INTEGER NOT NULL,
  "custom_id" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "request_body" JSONB NOT NULL,
  "response_body" JSONB,
  "error_body" JSONB,
  "usage" JSONB,
  "provider_cost" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "billed_cost" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "attempts" INTEGER NOT NULL DEFAULT 0,
  "last_error" TEXT,
  "locked_by" TEXT,
  "lease_expires_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "started_at" TIMESTAMP(3),
  "completed_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_batch_item_pkey" PRIMARY KEY ("item_id"),
  CONSTRAINT "deltallm_batch_item_batch_id_fkey"
    FOREIGN KEY ("batch_id") REFERENCES "deltallm_batch_job"("batch_id")
    ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_batch_item_batch_id_line_number_key"
  ON "deltallm_batch_item" ("batch_id", "line_number");

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_batch_item_batch_id_custom_id_key"
  ON "deltallm_batch_item" ("batch_id", "custom_id");

CREATE INDEX IF NOT EXISTS "deltallm_batch_item_batch_id_status_idx"
  ON "deltallm_batch_item" ("batch_id", "status");

CREATE INDEX IF NOT EXISTS "deltallm_batch_item_lease_expires_at_idx"
  ON "deltallm_batch_item" ("lease_expires_at");
