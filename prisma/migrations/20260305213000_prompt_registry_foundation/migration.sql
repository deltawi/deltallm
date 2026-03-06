CREATE TABLE IF NOT EXISTS "deltallm_prompttemplate" (
  "prompt_template_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "template_key" TEXT NOT NULL UNIQUE,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "owner_scope" TEXT,
  "metadata" JSONB,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "deltallm_prompttemplate_created_at_idx"
  ON "deltallm_prompttemplate" ("created_at");

CREATE TABLE IF NOT EXISTS "deltallm_promptversion" (
  "prompt_version_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "prompt_template_id" TEXT NOT NULL,
  "version" INTEGER NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'draft',
  "template_body" JSONB NOT NULL,
  "variables_schema" JSONB,
  "model_hints" JSONB,
  "published_at" TIMESTAMP,
  "published_by" TEXT,
  "archived_at" TIMESTAMP,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_promptversion_template_fk"
    FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_promptversion_template_version_key"
  ON "deltallm_promptversion" ("prompt_template_id", "version");

CREATE INDEX IF NOT EXISTS "deltallm_promptversion_template_status_idx"
  ON "deltallm_promptversion" ("prompt_template_id", "status");

CREATE TABLE IF NOT EXISTS "deltallm_promptlabel" (
  "prompt_label_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "prompt_template_id" TEXT NOT NULL,
  "label" TEXT NOT NULL,
  "prompt_version_id" TEXT NOT NULL,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_promptlabel_template_fk"
    FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
    ON DELETE CASCADE,
  CONSTRAINT "deltallm_promptlabel_version_fk"
    FOREIGN KEY ("prompt_version_id") REFERENCES "deltallm_promptversion"("prompt_version_id")
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_promptlabel_template_label_key"
  ON "deltallm_promptlabel" ("prompt_template_id", "label");

CREATE INDEX IF NOT EXISTS "deltallm_promptlabel_version_idx"
  ON "deltallm_promptlabel" ("prompt_version_id");

CREATE TABLE IF NOT EXISTS "deltallm_promptbinding" (
  "prompt_binding_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "prompt_template_id" TEXT NOT NULL,
  "label" TEXT NOT NULL,
  "priority" INTEGER NOT NULL DEFAULT 100,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "metadata" JSONB,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_promptbinding_template_fk"
    FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_promptbinding_scope_target_key"
  ON "deltallm_promptbinding" ("scope_type", "scope_id", "prompt_template_id", "label");

CREATE INDEX IF NOT EXISTS "deltallm_promptbinding_scope_idx"
  ON "deltallm_promptbinding" ("scope_type", "scope_id", "enabled", "priority");

CREATE INDEX IF NOT EXISTS "deltallm_promptbinding_template_idx"
  ON "deltallm_promptbinding" ("prompt_template_id");

CREATE TABLE IF NOT EXISTS "deltallm_promptrenderlog" (
  "prompt_render_log_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "request_id" TEXT,
  "api_key" TEXT,
  "user_id" TEXT,
  "team_id" TEXT,
  "organization_id" TEXT,
  "route_group_key" TEXT,
  "model" TEXT,
  "prompt_template_id" TEXT,
  "prompt_version_id" TEXT,
  "prompt_key" TEXT,
  "label" TEXT,
  "status" TEXT NOT NULL,
  "latency_ms" INTEGER,
  "error_code" TEXT,
  "error_message" TEXT,
  "variables" JSONB,
  "metadata" JSONB,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "deltallm_promptrenderlog_created_at_idx"
  ON "deltallm_promptrenderlog" ("created_at");

CREATE INDEX IF NOT EXISTS "deltallm_promptrenderlog_prompt_lookup_idx"
  ON "deltallm_promptrenderlog" ("prompt_key", "label", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_promptrenderlog_request_idx"
  ON "deltallm_promptrenderlog" ("request_id");
