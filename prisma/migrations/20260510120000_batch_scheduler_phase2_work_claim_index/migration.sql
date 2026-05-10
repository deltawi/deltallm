CREATE INDEX "deltallm_batch_job_work_slice_claim_idx"
ON "deltallm_batch_job"(
    ("last_scheduled_at" IS NOT NULL),
    "last_scheduled_at",
    (COALESCE("queue_entered_at", "created_at")),
    "created_at"
)
WHERE "status" IN ('queued', 'in_progress');
