CREATE TABLE IF NOT EXISTS "deltallm_callabletargetaccessgroupbinding" (
  "callable_target_access_group_binding_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "group_key" TEXT NOT NULL,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "metadata" JSONB,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_callabletargetaccessgroupbinding_scope_group_key"
  ON "deltallm_callabletargetaccessgroupbinding" ("group_key", "scope_type", "scope_id");

CREATE INDEX IF NOT EXISTS "deltallm_callabletargetaccessgroupbinding_scope_idx"
  ON "deltallm_callabletargetaccessgroupbinding" ("scope_type", "scope_id", "enabled");

CREATE INDEX IF NOT EXISTS "deltallm_callabletargetaccessgroupbinding_group_idx"
  ON "deltallm_callabletargetaccessgroupbinding" ("group_key");
