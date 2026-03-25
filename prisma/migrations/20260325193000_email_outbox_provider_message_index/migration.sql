CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_provider_message_idx"
  ON "deltallm_emailoutbox" ("provider", "last_provider_message_id");
