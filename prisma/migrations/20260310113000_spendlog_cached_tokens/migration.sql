ALTER TABLE "deltallm_spendlogs"
ADD COLUMN IF NOT EXISTS "prompt_tokens_cached" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS "completion_tokens_cached" INTEGER NOT NULL DEFAULT 0;
