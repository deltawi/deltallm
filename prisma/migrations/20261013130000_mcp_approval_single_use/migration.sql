-- An approved MCP tool call request now allows one execution.
-- The gateway sets consumed_at when it uses the approval.
ALTER TABLE "deltallm_mcpapprovalrequest"
ADD COLUMN "consumed_at" TIMESTAMP(3);

-- Before this change an approval could run many times in its window, so we
-- cannot know if an existing approval was used. Treat each one as used: an
-- open approval needs a new decision after the upgrade.
UPDATE "deltallm_mcpapprovalrequest"
SET "consumed_at" = COALESCE("decided_at", "updated_at", NOW())
WHERE "status" = 'approved';
