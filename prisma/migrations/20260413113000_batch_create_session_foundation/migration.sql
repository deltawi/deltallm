CREATE TABLE "deltallm_batch_create_session" (
    "session_id" TEXT NOT NULL,
    "target_batch_id" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "endpoint" TEXT NOT NULL,
    "input_file_id" TEXT NOT NULL,
    "staged_storage_backend" TEXT NOT NULL,
    "staged_storage_key" TEXT NOT NULL,
    "staged_checksum" TEXT,
    "staged_bytes" INTEGER NOT NULL,
    "expected_item_count" INTEGER NOT NULL,
    "inferred_model" TEXT,
    "metadata" JSONB,
    "requested_service_tier" TEXT,
    "effective_service_tier" TEXT,
    "service_tier_source" TEXT,
    "scheduling_scope_key" TEXT,
    "priority_quota_scope_key" TEXT,
    "idempotency_scope_key" TEXT,
    "idempotency_key" TEXT,
    "last_error_code" TEXT,
    "last_error_message" TEXT,
    "promotion_attempt_count" INTEGER NOT NULL DEFAULT 0,
    "created_by_api_key" TEXT,
    "created_by_user_id" TEXT,
    "created_by_team_id" TEXT,
    "created_by_organization_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completed_at" TIMESTAMP(3),
    "last_attempt_at" TIMESTAMP(3),
    "expires_at" TIMESTAMP(3),

    CONSTRAINT "deltallm_batch_create_session_idempotency_pair_chk"
        CHECK (
            ("idempotency_scope_key" IS NULL AND "idempotency_key" IS NULL)
            OR ("idempotency_scope_key" IS NOT NULL AND "idempotency_key" IS NOT NULL)
        ),
    CONSTRAINT "deltallm_batch_create_session_idempotency_scope_not_blank_chk"
        CHECK ("idempotency_scope_key" IS NULL OR BTRIM("idempotency_scope_key") <> ''),
    CONSTRAINT "deltallm_batch_create_session_idempotency_key_not_blank_chk"
        CHECK ("idempotency_key" IS NULL OR BTRIM("idempotency_key") <> ''),
    CONSTRAINT "deltallm_batch_create_session_pkey" PRIMARY KEY ("session_id")
);

CREATE UNIQUE INDEX "deltallm_batch_create_session_target_batch_id_key"
ON "deltallm_batch_create_session"("target_batch_id");

CREATE UNIQUE INDEX "deltallm_batch_create_session_idempotency_key"
ON "deltallm_batch_create_session"("idempotency_scope_key", "idempotency_key");

CREATE INDEX "deltallm_batch_create_session_input_file_idx"
ON "deltallm_batch_create_session"("input_file_id");

CREATE INDEX "deltallm_batch_create_session_status_created_idx"
ON "deltallm_batch_create_session"("status", "created_at");

CREATE INDEX "deltallm_batch_create_session_api_key_status_created_idx"
ON "deltallm_batch_create_session"("created_by_api_key", "status", "created_at");

CREATE INDEX "deltallm_batch_create_session_team_status_created_idx"
ON "deltallm_batch_create_session"("created_by_team_id", "status", "created_at");

CREATE INDEX "deltallm_batch_create_session_org_status_created_idx"
ON "deltallm_batch_create_session"("created_by_organization_id", "status", "created_at");

CREATE INDEX "deltallm_batch_create_session_expires_idx"
ON "deltallm_batch_create_session"("expires_at");

ALTER TABLE "deltallm_batch_create_session"
ADD CONSTRAINT "deltallm_batch_create_session_input_file_id_fkey"
FOREIGN KEY ("input_file_id") REFERENCES "deltallm_batch_file"("file_id")
ON DELETE RESTRICT ON UPDATE CASCADE;
