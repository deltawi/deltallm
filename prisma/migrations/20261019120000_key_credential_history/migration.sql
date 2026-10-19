BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS "deltallm_keycredentialhistory" (
    "token_hash" TEXT NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Block key writes until the active-key copy and trigger are both ready.
LOCK TABLE "deltallm_verificationtoken" IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT "token" FROM "deltallm_verificationtoken"
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION deltallm_reserve_key_credential()
RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.token IS NOT DISTINCT FROM OLD.token THEN
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO deltallm_keycredentialhistory (token_hash) VALUES (NEW.token)
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PKEY1', MESSAGE = 'API key value is unavailable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER deltallm_key_credential_reservation
BEFORE INSERT OR UPDATE OF token ON "deltallm_verificationtoken"
FOR EACH ROW EXECUTE FUNCTION deltallm_reserve_key_credential();
COMMIT;

-- Recover retained legacy identities without holding the key-table write lock.
SET statement_timeout = '5min';
INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT DISTINCT resource_id FROM deltallm_auditevent
WHERE resource_type = 'api_key' AND resource_id ~ '^[0-9a-f]{64}$'
ON CONFLICT DO NOTHING;

INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT DISTINCT api_key FROM deltallm_spendlog_events WHERE api_key ~ '^[0-9a-f]{64}$'
ON CONFLICT DO NOTHING;

INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT DISTINCT api_key FROM deltallm_accounting_usage_facts_v2 WHERE api_key ~ '^[0-9a-f]{64}$'
ON CONFLICT DO NOTHING;

INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT DISTINCT scope_id FROM deltallm_cacheinvalidationoutbox
WHERE scope_type = 'key_hash' AND scope_id ~ '^[0-9a-f]{64}$'
ON CONFLICT DO NOTHING;

INSERT INTO "deltallm_keycredentialhistory" ("token_hash")
SELECT DISTINCT payload_json->'event'->>'resource_id' FROM deltallm_audit_ingestion_outbox
WHERE payload_json->'event'->>'resource_type' = 'api_key'
  AND payload_json->'event'->>'resource_id' ~ '^[0-9a-f]{64}$'
ON CONFLICT DO NOTHING;
RESET statement_timeout;
