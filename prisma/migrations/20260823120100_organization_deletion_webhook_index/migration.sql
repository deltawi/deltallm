-- This migration intentionally cannot run inside a transaction. Production
-- executes it through the single organization-deletion migration coordinator,
-- which repairs an interrupted invalid index before retrying Prisma.
CREATE INDEX CONCURRENTLY IF NOT EXISTS
  deltallm_batch_webhook_outbox_org_status_created_idx
ON deltallm_batch_webhook_outbox (
  created_by_organization_id,
  status,
  created_at,
  event_id
);
