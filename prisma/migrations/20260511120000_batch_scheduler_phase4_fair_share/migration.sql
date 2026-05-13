CREATE TABLE "deltallm_batch_scheduler_flow" (
    "flow_id" TEXT NOT NULL,
    "service_tier" TEXT NOT NULL,
    "model_group" TEXT NOT NULL,
    "tenant_scope_type" TEXT NOT NULL,
    "tenant_scope_id" TEXT NOT NULL,
    "weight" INTEGER NOT NULL DEFAULT 1,
    "quantum_work_units" INTEGER NOT NULL DEFAULT 16,
    "deficit_work_units" INTEGER NOT NULL DEFAULT 0,
    "active" BOOLEAN NOT NULL DEFAULT false,
    "queued_jobs" INTEGER NOT NULL DEFAULT 0,
    "queued_work_units" INTEGER NOT NULL DEFAULT 0,
    "in_flight_work_units" INTEGER NOT NULL DEFAULT 0,
    "skip_reason_summary" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "last_selected_at" TIMESTAMP(3),
    "last_refilled_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "deltallm_batch_scheduler_flow_pkey" PRIMARY KEY ("flow_id")
);

CREATE UNIQUE INDEX "deltallm_batch_scheduler_flow_key"
    ON "deltallm_batch_scheduler_flow"("service_tier", "model_group", "tenant_scope_type", "tenant_scope_id");

CREATE INDEX "deltallm_batch_scheduler_flow_tier_model_active_idx"
    ON "deltallm_batch_scheduler_flow"("service_tier", "model_group", "active", "last_selected_at");

CREATE INDEX "deltallm_batch_scheduler_flow_model_deficit_idx"
    ON "deltallm_batch_scheduler_flow"("model_group", "active", "deficit_work_units");

CREATE INDEX "deltallm_batch_scheduler_flow_tenant_active_idx"
    ON "deltallm_batch_scheduler_flow"("tenant_scope_type", "tenant_scope_id", "active");
