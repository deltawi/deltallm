-- Reconcile the historical raw SQL migration chain with the current Prisma schema contract.
-- This keeps fresh installs, legacy db push baselines, and upgrade-path validation on one
-- converged database shape without rewriting old migrations in place.

-- DropForeignKey
ALTER TABLE "deltallm_auditpayload" DROP CONSTRAINT "deltallm_auditpayload_event_fk";

-- DropForeignKey
ALTER TABLE "deltallm_promptbinding" DROP CONSTRAINT "deltallm_promptbinding_template_fk";

-- DropForeignKey
ALTER TABLE "deltallm_promptlabel" DROP CONSTRAINT "deltallm_promptlabel_template_fk";

-- DropForeignKey
ALTER TABLE "deltallm_promptlabel" DROP CONSTRAINT "deltallm_promptlabel_version_fk";

-- DropForeignKey
ALTER TABLE "deltallm_promptversion" DROP CONSTRAINT "deltallm_promptversion_template_fk";

-- DropForeignKey
ALTER TABLE "deltallm_routegroupmember" DROP CONSTRAINT "deltallm_routegroupmember_deployment_fk";

-- DropForeignKey
ALTER TABLE "deltallm_routegroupmember" DROP CONSTRAINT "deltallm_routegroupmember_route_group_fk";

-- DropForeignKey
ALTER TABLE "deltallm_routepolicy" DROP CONSTRAINT "deltallm_routepolicy_route_group_fk";

-- AlterTable
ALTER TABLE "deltallm_auditevent"
  DROP CONSTRAINT "deltallm_auditevent_pkey",
  ALTER COLUMN "event_id" DROP DEFAULT,
  ALTER COLUMN "event_id" SET DATA TYPE TEXT USING "event_id"::text,
  ALTER COLUMN "occurred_at" SET DATA TYPE TIMESTAMP(3) USING ("occurred_at" AT TIME ZONE 'UTC'),
  ADD CONSTRAINT "deltallm_auditevent_pkey" PRIMARY KEY ("event_id");

-- AlterTable
ALTER TABLE "deltallm_auditpayload"
  DROP CONSTRAINT "deltallm_auditpayload_pkey",
  ALTER COLUMN "payload_id" DROP DEFAULT,
  ALTER COLUMN "payload_id" SET DATA TYPE TEXT USING "payload_id"::text,
  ALTER COLUMN "event_id" SET DATA TYPE TEXT USING "event_id"::text,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ADD CONSTRAINT "deltallm_auditpayload_pkey" PRIMARY KEY ("payload_id");

-- AlterTable
ALTER TABLE "deltallm_batch_completion_outbox"
  ALTER COLUMN "completion_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_batch_file"
  ALTER COLUMN "file_id" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_batch_item"
  ALTER COLUMN "item_id" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_batch_job"
  ALTER COLUMN "batch_id" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_callabletargetbinding"
  ALTER COLUMN "callable_target_binding_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_callabletargetscopepolicy"
  ALTER COLUMN "callable_target_scope_policy_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_emailoutbox"
  ALTER COLUMN "to_addresses" DROP DEFAULT,
  ALTER COLUMN "cc_addresses" DROP DEFAULT,
  ALTER COLUMN "bcc_addresses" DROP DEFAULT,
  ALTER COLUMN "next_attempt_at" SET DATA TYPE TIMESTAMP(3) USING ("next_attempt_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3) USING ("updated_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "sent_at" SET DATA TYPE TIMESTAMP(3) USING ("sent_at" AT TIME ZONE 'UTC');

-- AlterTable
ALTER TABLE "deltallm_emailsuppression"
  ALTER COLUMN "first_seen_at" SET DATA TYPE TIMESTAMP(3) USING ("first_seen_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "last_seen_at" SET DATA TYPE TIMESTAMP(3) USING ("last_seen_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3) USING ("updated_at" AT TIME ZONE 'UTC');

-- AlterTable
ALTER TABLE "deltallm_emailtoken"
  ALTER COLUMN "expires_at" SET DATA TYPE TIMESTAMP(3) USING ("expires_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "consumed_at" SET DATA TYPE TIMESTAMP(3) USING ("consumed_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3) USING ("updated_at" AT TIME ZONE 'UTC');

-- AlterTable
ALTER TABLE "deltallm_emailwebhookevent"
  ALTER COLUMN "occurred_at" SET DATA TYPE TIMESTAMP(3) USING ("occurred_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3) USING ("updated_at" AT TIME ZONE 'UTC');

-- AlterTable
ALTER TABLE "deltallm_mcpapprovalrequest"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_mcpbinding"
  ALTER COLUMN "tool_allowlist" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_mcpscopepolicy"
  ALTER COLUMN "mcp_scope_policy_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_mcpserver"
  ALTER COLUMN "forwarded_headers_allowlist" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_mcptoolpolicy"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_modeldeployment"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_namedcredential"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_organizationmembership"
  ALTER COLUMN "membership_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_organizationtable"
  ALTER COLUMN "id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_platformaccount"
  ALTER COLUMN "account_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_platformidentity"
  ALTER COLUMN "identity_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_platforminvitation"
  ALTER COLUMN "expires_at" SET DATA TYPE TIMESTAMP(3) USING ("expires_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "accepted_at" SET DATA TYPE TIMESTAMP(3) USING ("accepted_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "cancelled_at" SET DATA TYPE TIMESTAMP(3) USING ("cancelled_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3) USING ("created_at" AT TIME ZONE 'UTC'),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3) USING ("updated_at" AT TIME ZONE 'UTC');

-- AlterTable
ALTER TABLE "deltallm_platformsession"
  ALTER COLUMN "session_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_promptbinding"
  ALTER COLUMN "prompt_binding_id" DROP DEFAULT,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_promptlabel"
  ALTER COLUMN "prompt_label_id" DROP DEFAULT,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_promptrenderlog"
  ALTER COLUMN "prompt_render_log_id" DROP DEFAULT,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_prompttemplate"
  ALTER COLUMN "prompt_template_id" DROP DEFAULT,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_promptversion"
  ALTER COLUMN "prompt_version_id" DROP DEFAULT,
  ALTER COLUMN "published_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "archived_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_routegroup"
  ALTER COLUMN "route_group_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_routegroupbinding"
  ALTER COLUMN "route_group_binding_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_routegroupmember"
  ALTER COLUMN "membership_id" DROP DEFAULT,
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_routepolicy"
  ALTER COLUMN "route_policy_id" DROP DEFAULT,
  ALTER COLUMN "published_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "deltallm_serviceaccount"
  ALTER COLUMN "service_account_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_spendlog_events"
  ALTER COLUMN "request_tags" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_teammembership"
  ALTER COLUMN "membership_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_teammodelspend"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_teamtable"
  ALTER COLUMN "team_id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT,
  ALTER COLUMN "self_service_keys_enabled" SET NOT NULL,
  ALTER COLUMN "self_service_require_expiry" SET NOT NULL;

-- AlterTable
ALTER TABLE "deltallm_usertable"
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AlterTable
ALTER TABLE "deltallm_verificationtoken"
  ALTER COLUMN "id" DROP DEFAULT,
  ALTER COLUMN "updated_at" DROP DEFAULT;

-- AddForeignKey
ALTER TABLE "deltallm_auditpayload"
  ADD CONSTRAINT "deltallm_auditpayload_event_fk"
  FOREIGN KEY ("event_id") REFERENCES "deltallm_auditevent"("event_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_promptversion"
  ADD CONSTRAINT "deltallm_promptversion_template_fk"
  FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_promptlabel"
  ADD CONSTRAINT "deltallm_promptlabel_template_fk"
  FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_promptlabel"
  ADD CONSTRAINT "deltallm_promptlabel_version_fk"
  FOREIGN KEY ("prompt_version_id") REFERENCES "deltallm_promptversion"("prompt_version_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_promptbinding"
  ADD CONSTRAINT "deltallm_promptbinding_template_fk"
  FOREIGN KEY ("prompt_template_id") REFERENCES "deltallm_prompttemplate"("prompt_template_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_routegroupmember"
  ADD CONSTRAINT "deltallm_routegroupmember_route_group_fk"
  FOREIGN KEY ("route_group_id") REFERENCES "deltallm_routegroup"("route_group_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_routegroupmember"
  ADD CONSTRAINT "deltallm_routegroupmember_deployment_fk"
  FOREIGN KEY ("deployment_id") REFERENCES "deltallm_modeldeployment"("deployment_id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deltallm_routepolicy"
  ADD CONSTRAINT "deltallm_routepolicy_route_group_fk"
  FOREIGN KEY ("route_group_id") REFERENCES "deltallm_routegroup"("route_group_id")
  ON DELETE CASCADE ON UPDATE CASCADE;
