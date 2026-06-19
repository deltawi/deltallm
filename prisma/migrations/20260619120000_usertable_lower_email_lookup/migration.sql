-- Supports the case-insensitive legacy runtime-user email fallback in the admin principals API.
-- Rebuild concurrently so K8s rollouts do not block user-table writes and retries recover invalid indexes.
DROP INDEX CONCURRENTLY IF EXISTS "deltallm_usertable_lower_user_email_idx";
CREATE INDEX CONCURRENTLY "deltallm_usertable_lower_user_email_idx"
ON "deltallm_usertable" (lower("user_email"))
WHERE "user_email" IS NOT NULL;
