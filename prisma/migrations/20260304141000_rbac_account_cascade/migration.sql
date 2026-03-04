ALTER TABLE "deltallm_platformsession"
  DROP CONSTRAINT IF EXISTS "deltallm_platformsession_account_id_fkey",
  ADD CONSTRAINT "deltallm_platformsession_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "deltallm_platformidentity"
  DROP CONSTRAINT IF EXISTS "deltallm_platformidentity_account_id_fkey",
  ADD CONSTRAINT "deltallm_platformidentity_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "deltallm_organizationmembership"
  DROP CONSTRAINT IF EXISTS "deltallm_organizationmembership_account_id_fkey",
  ADD CONSTRAINT "deltallm_organizationmembership_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "deltallm_teammembership"
  DROP CONSTRAINT IF EXISTS "deltallm_teammembership_account_id_fkey",
  ADD CONSTRAINT "deltallm_teammembership_account_id_fkey"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE;
