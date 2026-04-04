CREATE TABLE "deltallm_namedcredential" (
  "credential_id" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "connection_config" JSONB NOT NULL,
  "metadata" JSONB,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_namedcredential_pkey" PRIMARY KEY ("credential_id")
);

CREATE UNIQUE INDEX "deltallm_namedcredential_name_key"
  ON "deltallm_namedcredential"("name");

CREATE INDEX "deltallm_namedcredential_provider_idx"
  ON "deltallm_namedcredential"("provider");

CREATE INDEX "deltallm_namedcredential_created_at_idx"
  ON "deltallm_namedcredential"("created_at");

ALTER TABLE "deltallm_modeldeployment"
  ADD COLUMN "named_credential_id" TEXT;

CREATE INDEX "deltallm_modeldeployment_named_credential_idx"
  ON "deltallm_modeldeployment"("named_credential_id");

ALTER TABLE "deltallm_modeldeployment"
  ADD CONSTRAINT "deltallm_modeldeployment_named_credential_id_fkey"
  FOREIGN KEY ("named_credential_id")
  REFERENCES "deltallm_namedcredential"("credential_id")
  ON DELETE SET NULL
  ON UPDATE CASCADE;
