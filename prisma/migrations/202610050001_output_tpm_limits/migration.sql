ALTER TABLE "deltallm_verificationtoken" ADD COLUMN "output_tpm_limit" INTEGER,
    ADD CONSTRAINT "verificationtoken_output_tpm_positive" CHECK ("output_tpm_limit" > 0);
ALTER TABLE "deltallm_usertable" ADD COLUMN "output_tpm_limit" INTEGER,
    ADD CONSTRAINT "usertable_output_tpm_positive" CHECK ("output_tpm_limit" > 0);
ALTER TABLE "deltallm_teamtable" ADD COLUMN "output_tpm_limit" INTEGER,
    ADD CONSTRAINT "teamtable_output_tpm_positive" CHECK ("output_tpm_limit" > 0);
ALTER TABLE "deltallm_organizationtable" ADD COLUMN "output_tpm_limit" INTEGER,
    ADD CONSTRAINT "organizationtable_output_tpm_positive" CHECK ("output_tpm_limit" > 0);
