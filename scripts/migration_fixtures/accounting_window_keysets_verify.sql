-- Check each funding lookup after fresh install and both supported upgrades.
DO $verify$
DECLARE
    target RECORD;
    body TEXT;
    keyset TEXT := 'w.window_id = ANY(ARRAY(';
    reference_probe TEXT := $probe$lookup.window_id=ref->>'window_id' OFFSET 0$probe$;
BEGIN
    FOR target IN SELECT * FROM (VALUES
        ('deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)',4),
        ('deltallm_accounting_allocate_permit_grants_batch(bigint,text,integer,jsonb)',1),
        ('deltallm_accounting_allocate_local_permit_grants_batch(bigint,text,integer,jsonb)',1)
    ) contract(signature,keyset_count)
    LOOP
        SELECT pg_get_functiondef(target.signature::regprocedure) INTO STRICT body;
        IF position('w.window_id IN (' IN body)>0
           OR length(body)-length(replace(body,keyset,''))
              <>target.keyset_count*length(keyset) THEN
            RAISE EXCEPTION 'accounting window keyset contract is missing';
        END IF;
    END LOOP;
    FOR target IN SELECT * FROM (VALUES
        ('deltallm_accounting_permit_window_ids(bigint,jsonb)'),
        ('deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)')
    ) contract(signature)
    LOOP
        SELECT pg_get_functiondef(target.signature::regprocedure) INTO STRICT body;
        IF length(body)-length(replace(body,reference_probe,''))<>length(reference_probe) THEN
            RAISE EXCEPTION 'accounting window reference probe is missing';
        END IF;
    END LOOP;
    SELECT pg_get_functiondef(
        'deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)'::regprocedure
    ) INTO STRICT body;
    IF position('AS is_replay OFFSET 0' IN body)=0
       OR position('AND NOT replay.is_replay' IN body)=0
       OR position('AND NOT EXISTS (' IN body)>0 THEN
        RAISE EXCEPTION 'accounting operation primary-key probe is missing';
    END IF;
    SELECT pg_get_functiondef(
        'deltallm_accounting_allocate_permit_grant(bigint,text,uuid,integer,integer,jsonb)'
        ::regprocedure
    ) INTO STRICT body;
    IF position('SELECT candidate.grant_id FROM candidate' IN body)=0
       OR position('WHERE g.grant_id=candidate.grant_id' IN body)>0 THEN
        RAISE EXCEPTION 'accounting permit promotion keyset is missing';
    END IF;
END;
$verify$;
