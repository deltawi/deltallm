ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_version_number_check"
  CHECK ("version_number" > 0);

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_draft_publish_metadata_check"
  CHECK (
    "status" <> 'draft'
    OR ("published_at" IS NULL AND "published_by_account_id" IS NULL)
  );
