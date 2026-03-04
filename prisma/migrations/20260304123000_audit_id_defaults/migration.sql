CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
DECLARE
  event_id_type text;
  payload_id_type text;
BEGIN
  SELECT data_type INTO event_id_type
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'deltallm_auditevent'
    AND column_name = 'event_id';

  IF event_id_type = 'uuid' THEN
    EXECUTE 'ALTER TABLE "deltallm_auditevent" ALTER COLUMN "event_id" SET DEFAULT gen_random_uuid()';
  ELSE
    EXECUTE 'ALTER TABLE "deltallm_auditevent" ALTER COLUMN "event_id" SET DEFAULT gen_random_uuid()::text';
  END IF;

  SELECT data_type INTO payload_id_type
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'deltallm_auditpayload'
    AND column_name = 'payload_id';

  IF payload_id_type = 'uuid' THEN
    EXECUTE 'ALTER TABLE "deltallm_auditpayload" ALTER COLUMN "payload_id" SET DEFAULT gen_random_uuid()';
  ELSE
    EXECUTE 'ALTER TABLE "deltallm_auditpayload" ALTER COLUMN "payload_id" SET DEFAULT gen_random_uuid()::text';
  END IF;
END
$$;

ALTER TABLE "deltallm_auditevent"
  ALTER COLUMN "occurred_at" SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE "deltallm_auditpayload"
  ALTER COLUMN "created_at" SET DEFAULT CURRENT_TIMESTAMP;
