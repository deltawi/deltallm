-- Index for the work-slice claim ordering. Plain-column shape so it round-trips
-- through prisma's schema declaration (no expression / partial WHERE — those
-- can't be declared in schema.prisma and the predicate form would also block
-- the project's enum/text reconciliation migrations).
CREATE INDEX "deltallm_batch_job_work_slice_claim_idx"
ON "deltallm_batch_job"("status", "last_scheduled_at", "created_at");
