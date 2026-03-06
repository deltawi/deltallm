CREATE TABLE IF NOT EXISTS "deltallm_routegroup" (
  "route_group_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "group_key" TEXT NOT NULL UNIQUE,
  "name" TEXT,
  "mode" TEXT NOT NULL DEFAULT 'chat',
  "routing_strategy" TEXT,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "metadata" JSONB,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "deltallm_routegroup_enabled_idx"
  ON "deltallm_routegroup" ("enabled");

CREATE TABLE IF NOT EXISTS "deltallm_routegroupmember" (
  "membership_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "route_group_id" TEXT NOT NULL,
  "deployment_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "weight" INTEGER,
  "priority" INTEGER,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_routegroupmember_route_group_fk"
    FOREIGN KEY ("route_group_id") REFERENCES "deltallm_routegroup"("route_group_id")
    ON DELETE CASCADE,
  CONSTRAINT "deltallm_routegroupmember_deployment_fk"
    FOREIGN KEY ("deployment_id") REFERENCES "deltallm_modeldeployment"("deployment_id")
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_routegroupmember_route_group_id_deployment_id_key"
  ON "deltallm_routegroupmember" ("route_group_id", "deployment_id");

CREATE INDEX IF NOT EXISTS "deltallm_routegroupmember_deployment_id_idx"
  ON "deltallm_routegroupmember" ("deployment_id");

CREATE TABLE IF NOT EXISTS "deltallm_routepolicy" (
  "route_policy_id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "route_group_id" TEXT NOT NULL,
  "version" INTEGER NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'draft',
  "policy_json" JSONB NOT NULL,
  "published_at" TIMESTAMP,
  "published_by" TEXT,
  "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_routepolicy_route_group_fk"
    FOREIGN KEY ("route_group_id") REFERENCES "deltallm_routegroup"("route_group_id")
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_routepolicy_route_group_id_version_key"
  ON "deltallm_routepolicy" ("route_group_id", "version");

CREATE INDEX IF NOT EXISTS "deltallm_routepolicy_group_status_idx"
  ON "deltallm_routepolicy" ("route_group_id", "status");
