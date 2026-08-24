ALTER TABLE "deltallm_routepolicy"
  ADD COLUMN "semantics_version" INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM "deltallm_modeldeployment"
    GROUP BY "model_name"
    HAVING COUNT(*) > 1
  ) THEN
    RAISE EXCEPTION
      'Cannot enforce unique model names: deltallm_modeldeployment contains duplicates';
  END IF;
END $$;

CREATE UNIQUE INDEX "deltallm_modeldeployment_model_name_key"
  ON "deltallm_modeldeployment"("model_name");

DROP INDEX IF EXISTS "deltallm_modeldeployment_model_name_idx";

CREATE TABLE "deltallm_routeruntimestate" (
  "state_key" TEXT NOT NULL,
  "revision" BIGINT NOT NULL DEFAULT 0,
  "route_groups_initialized" BOOLEAN NOT NULL DEFAULT FALSE,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_routeruntimestate_pkey" PRIMARY KEY ("state_key")
);

INSERT INTO "deltallm_routeruntimestate" (
  "state_key",
  "revision",
  "route_groups_initialized",
  "updated_at"
)
SELECT
  'routing_runtime',
  0,
  EXISTS (SELECT 1 FROM "deltallm_routegroup"),
  CURRENT_TIMESTAMP;

WITH ranked_published AS (
  SELECT
    route_policy_id,
    ROW_NUMBER() OVER (
      PARTITION BY route_group_id
      ORDER BY version DESC, updated_at DESC, route_policy_id DESC
    ) AS publication_rank
  FROM deltallm_routepolicy
  WHERE status = 'published'
)
UPDATE deltallm_routepolicy AS policy
SET status = 'archived', updated_at = NOW()
FROM ranked_published AS ranked
WHERE policy.route_policy_id = ranked.route_policy_id
  AND ranked.publication_rank > 1;

CREATE UNIQUE INDEX "deltallm_routepolicy_one_published_per_group"
  ON "deltallm_routepolicy"("route_group_id")
  WHERE "status" = 'published';
