-- AlterTable
ALTER TABLE "deltallm_platformsession" ADD COLUMN     "external_binding_epoch" INTEGER,
ADD COLUMN     "external_generation" INTEGER,
ADD COLUMN     "external_integration_epoch" INTEGER,
ADD COLUMN     "external_parent_id" TEXT,
ADD COLUMN     "external_subject_epoch" INTEGER;

-- CreateTable
CREATE TABLE "deltallm_externalauthintegration" (
    "integration_id" TEXT NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "epoch" INTEGER NOT NULL DEFAULT 0,
    "version" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthintegration_pkey" PRIMARY KEY ("integration_id")
);

-- CreateTable
CREATE TABLE "deltallm_externalauthbinding" (
    "binding_id" TEXT NOT NULL,
    "integration_id" TEXT NOT NULL,
    "external_customer_id" TEXT NOT NULL,
    "organization_id" TEXT NOT NULL,
    "team_id" TEXT NOT NULL,
    "profile" TEXT NOT NULL DEFAULT 'customer_v1',
    "state" TEXT NOT NULL DEFAULT 'active',
    "epoch" INTEGER NOT NULL DEFAULT 0,
    "version" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthbinding_pkey" PRIMARY KEY ("binding_id")
);

-- CreateTable
CREATE TABLE "deltallm_externalauthsubject" (
    "subject_id" TEXT NOT NULL,
    "integration_id" TEXT NOT NULL,
    "binding_id" TEXT NOT NULL,
    "identity_issuer" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "state" TEXT NOT NULL DEFAULT 'pending',
    "epoch" INTEGER NOT NULL DEFAULT 0,
    "version" INTEGER NOT NULL DEFAULT 0,
    "account_id" TEXT,
    "identity_id" TEXT,
    "runtime_user_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthsubject_pkey" PRIMARY KEY ("subject_id")
);

-- CreateTable
CREATE TABLE "deltallm_externalauthparentsession" (
    "parent_id" TEXT NOT NULL,
    "integration_id" TEXT NOT NULL,
    "external_session_id_hash" TEXT NOT NULL,
    "subject_id" TEXT NOT NULL,
    "auth_time" TIMESTAMP(3) NOT NULL,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "revoked_at" TIMESTAMP(3),
    "generation" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthparentsession_pkey" PRIMARY KEY ("parent_id")
);

-- CreateTable
CREATE TABLE "deltallm_externalauthassertionuse" (
    "integration_id" TEXT NOT NULL,
    "jti_hash" TEXT NOT NULL,
    "purpose" TEXT NOT NULL,
    "received_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "retain_until" TIMESTAMP(3) NOT NULL,
    "outcome" TEXT NOT NULL DEFAULT 'claimed',

    CONSTRAINT "deltallm_externalauthassertionuse_pkey" PRIMARY KEY ("integration_id","jti_hash")
);

-- CreateIndex
CREATE INDEX "deltallm_externalauthbinding_organization_id_idx" ON "deltallm_externalauthbinding"("organization_id");

-- CreateIndex
CREATE INDEX "deltallm_externalauthbinding_team_id_idx" ON "deltallm_externalauthbinding"("team_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthbinding_integration_id_external_custom_key" ON "deltallm_externalauthbinding"("integration_id", "external_customer_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthbinding_integration_id_binding_id_key" ON "deltallm_externalauthbinding"("integration_id", "binding_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthsubject_account_id_key" ON "deltallm_externalauthsubject"("account_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthsubject_identity_id_key" ON "deltallm_externalauthsubject"("identity_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthsubject_runtime_user_id_key" ON "deltallm_externalauthsubject"("runtime_user_id");

-- CreateIndex
CREATE INDEX "deltallm_externalauthsubject_binding_id_idx" ON "deltallm_externalauthsubject"("binding_id");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthsubject_identity_issuer_subject_key" ON "deltallm_externalauthsubject"("identity_issuer", "subject");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthsubject_integration_id_subject_id_key" ON "deltallm_externalauthsubject"("integration_id", "subject_id");

-- CreateIndex
CREATE INDEX "deltallm_externalauthparentsession_subject_id_idx" ON "deltallm_externalauthparentsession"("subject_id");

-- CreateIndex
CREATE INDEX "deltallm_externalauthparentsession_expires_at_idx" ON "deltallm_externalauthparentsession"("expires_at");

-- CreateIndex
CREATE UNIQUE INDEX "deltallm_externalauthparentsession_integration_id_external__key" ON "deltallm_externalauthparentsession"("integration_id", "external_session_id_hash");

-- CreateIndex
CREATE INDEX "deltallm_externalauthassertionuse_retain_until_idx" ON "deltallm_externalauthassertionuse"("retain_until");

-- CreateIndex
CREATE INDEX "deltallm_platformsession_external_parent_id_external_genera_idx" ON "deltallm_platformsession"("external_parent_id", "external_generation");

-- AddForeignKey
ALTER TABLE "deltallm_platformsession" ADD CONSTRAINT "deltallm_platformsession_external_parent_id_fkey" FOREIGN KEY ("external_parent_id") REFERENCES "deltallm_externalauthparentsession"("parent_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthbinding" ADD CONSTRAINT "deltallm_externalauthbinding_integration_id_fkey" FOREIGN KEY ("integration_id") REFERENCES "deltallm_externalauthintegration"("integration_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthbinding" ADD CONSTRAINT "deltallm_externalauthbinding_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "deltallm_organizationtable"("organization_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthbinding" ADD CONSTRAINT "deltallm_externalauthbinding_team_id_fkey" FOREIGN KEY ("team_id") REFERENCES "deltallm_teamtable"("team_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthsubject" ADD CONSTRAINT "deltallm_externalauthsubject_integration_id_binding_id_fkey" FOREIGN KEY ("integration_id", "binding_id") REFERENCES "deltallm_externalauthbinding"("integration_id", "binding_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthsubject" ADD CONSTRAINT "deltallm_externalauthsubject_account_id_fkey" FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthsubject" ADD CONSTRAINT "deltallm_externalauthsubject_identity_id_fkey" FOREIGN KEY ("identity_id") REFERENCES "deltallm_platformidentity"("identity_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthsubject" ADD CONSTRAINT "deltallm_externalauthsubject_runtime_user_id_fkey" FOREIGN KEY ("runtime_user_id") REFERENCES "deltallm_usertable"("user_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthparentsession" ADD CONSTRAINT "deltallm_externalauthparentsession_integration_id_subject__fkey" FOREIGN KEY ("integration_id", "subject_id") REFERENCES "deltallm_externalauthsubject"("integration_id", "subject_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_externalauthassertionuse" ADD CONSTRAINT "deltallm_externalauthassertionuse_integration_id_fkey" FOREIGN KEY ("integration_id") REFERENCES "deltallm_externalauthintegration"("integration_id") ON DELETE RESTRICT ON UPDATE CASCADE;

