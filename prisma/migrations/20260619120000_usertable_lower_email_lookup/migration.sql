-- Supports the case-insensitive legacy runtime-user email fallback in the admin principals API.
DROP INDEX IF EXISTS "deltallm_usertable_lower_user_email_idx";
CREATE INDEX IF NOT EXISTS "deltallm_usertable_lower_user_email_idx"
ON "deltallm_usertable" (lower("user_email"))
WHERE "user_email" IS NOT NULL;
