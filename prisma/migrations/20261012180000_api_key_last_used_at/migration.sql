-- Record when each API key last authenticated a request. The gateway writes
-- these rows in batches from a background task, so a value can be up to one
-- flush interval old. A key without a row has no recorded use.
--
-- The value is in its own table: an update of the key row fires the
-- organization lifecycle fence and contends with key edits. The foreign key
-- follows token regeneration and key deletion.
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE "deltallm_verificationtoken_last_used" (
  "token" TEXT NOT NULL,
  "last_used_at" TIMESTAMPTZ(6) NOT NULL,
  CONSTRAINT "deltallm_verificationtoken_last_used_pkey" PRIMARY KEY ("token"),
  CONSTRAINT "deltallm_verificationtoken_last_used_token_fkey"
    FOREIGN KEY ("token") REFERENCES "deltallm_verificationtoken"("token")
    ON DELETE CASCADE ON UPDATE CASCADE
);

COMMIT;
