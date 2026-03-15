ALTER TABLE "deltallm_mcpserver"
ADD COLUMN "owner_scope_type" TEXT NOT NULL DEFAULT 'global',
ADD COLUMN "owner_scope_id" TEXT,
ADD CONSTRAINT "deltallm_mcpserver_owner_scope_chk"
CHECK (
  (
    "owner_scope_type" = 'global'
    AND "owner_scope_id" IS NULL
  )
  OR (
    "owner_scope_type" = 'organization'
    AND "owner_scope_id" IS NOT NULL
  )
);

CREATE INDEX "deltallm_mcpserver_owner_scope_idx"
ON "deltallm_mcpserver"("owner_scope_type", "owner_scope_id");
