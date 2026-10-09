ALTER TABLE "deltallm_tiermodelpolicy" ADD COLUMN "output_tpm_limit" INTEGER;
ALTER TABLE "deltallm_teamtable" ADD COLUMN "model_output_tpm_limit" JSONB;
ALTER TABLE "deltallm_verificationtoken" ADD COLUMN "model_output_tpm_limit" JSONB;

ALTER TABLE "deltallm_tiermodelpolicy" ADD CONSTRAINT "tier_model_output_tpm_positive"
CHECK (output_tpm_limit IS NULL OR output_tpm_limit > 0);

CREATE FUNCTION deltallm_valid_model_output_limits(value JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE entry RECORD;
BEGIN
    IF jsonb_typeof(value) <> 'object' OR octet_length(value::text) > 32768 THEN
        RETURN FALSE;
    END IF;
    IF (SELECT count(*) FROM jsonb_each(value)) > 64 THEN RETURN FALSE; END IF;
    FOR entry IN SELECT key, val FROM jsonb_each(value) AS entries(key, val) LOOP
        IF length(btrim(entry.key)) = 0 OR entry.key <> btrim(entry.key)
           OR octet_length(entry.key) > 256 OR entry.key ~ '[*[:cntrl:]]'
           OR jsonb_typeof(entry.val) <> 'number' THEN RETURN FALSE; END IF;
        IF entry.val::text !~ '^[0-9]+$' THEN RETURN FALSE; END IF;
        IF entry.val::text::numeric < 1 OR entry.val::text::numeric > 2147483647 THEN
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN TRUE;
END;
$$;

ALTER TABLE "deltallm_teamtable" ADD CONSTRAINT "team_model_output_tpm_valid"
CHECK (model_output_tpm_limit IS NULL OR deltallm_valid_model_output_limits(model_output_tpm_limit));
ALTER TABLE "deltallm_verificationtoken" ADD CONSTRAINT "key_model_output_tpm_valid"
CHECK (model_output_tpm_limit IS NULL OR deltallm_valid_model_output_limits(model_output_tpm_limit));
