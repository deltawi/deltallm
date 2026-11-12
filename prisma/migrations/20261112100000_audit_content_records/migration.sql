-- Accept audit_content records in the audit ingestion outbox. An audit_content record
-- carries the request and response bodies of a data-plane request whose audit event
-- another owner writes (the accounting v2 projection). The audit worker attaches the
-- bodies to that event.
--
-- The new check accepts a superset of the old values, so earlier application
-- versions keep working. NOT VALID adds it without a table scan under the
-- ACCESS EXCLUSIVE lock; VALIDATE then scans under a lock that allows writes.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
ALTER TABLE "deltallm_audit_ingestion_outbox"
    DROP CONSTRAINT "deltallm_audit_ingestion_outbox_type_check";
ALTER TABLE "deltallm_audit_ingestion_outbox"
    ADD CONSTRAINT "deltallm_audit_ingestion_outbox_type_check"
    CHECK ("record_type" IN ('audit_event', 'prompt_render', 'audit_content')) NOT VALID;
COMMIT;

ALTER TABLE "deltallm_audit_ingestion_outbox"
    VALIDATE CONSTRAINT "deltallm_audit_ingestion_outbox_type_check";
