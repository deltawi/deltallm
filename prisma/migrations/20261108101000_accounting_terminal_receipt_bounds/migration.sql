-- Reject receipts that cannot be materialized or projected before journal ACK.
-- Apply only to new receipts so an exact replay keeps its original ACK.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_validate_terminal_receipt(
    p_document JSONB, p_outcome TEXT, p_allowance NUMERIC
) RETURNS VOID LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    charge NUMERIC;
    spend JSONB:=p_document->'spend_payload';
    usage JSONB:=spend->'usage';
    snapshot JSONB:=spend#>'{metadata,billing,usage_snapshot}';
    count_key TEXT;
    count_value INTEGER;
    token_total BIGINT:=0;
BEGIN
    IF p_document->>'outcome' IS DISTINCT FROM p_outcome THEN
        RAISE EXCEPTION 'accounting_terminal_receipt_outcome' USING ERRCODE='P0001';
    END IF;
    IF p_outcome<>'completed' THEN RETURN; END IF;
    charge:=(p_document->>'exact_charge')::numeric;
    IF charge IS NULL OR charge<0 OR charge>p_allowance
       OR charge IS DISTINCT FROM charge::numeric(38,18) THEN
        RAISE EXCEPTION 'accounting_terminal_receipt_charge' USING ERRCODE='P0001';
    END IF;
    FOREACH count_key IN ARRAY ARRAY[
        'prompt_tokens','completion_tokens','input_audio_tokens','output_audio_tokens',
        'total_tokens','prompt_tokens_cached','completion_tokens_cached',
        'input_characters','output_characters','rerank_units'
    ] LOOP
        count_value:=deltallm_accounting_usage_count(usage,snapshot,count_key,0);
        IF count_key IN ('prompt_tokens','completion_tokens','input_audio_tokens','output_audio_tokens') THEN
            token_total:=token_total+count_value;
        END IF;
    END LOOP;
    IF token_total>2147483647 OR COALESCE(
        (usage->>'images')::integer,(snapshot->>'image_count')::integer,(snapshot->>'images')::integer,0
    )<0 THEN
        RAISE EXCEPTION 'accounting_terminal_receipt_usage' USING ERRCODE='P0001';
    END IF;
END;
$$;

DO $migration$
DECLARE
    definition TEXT:=pg_get_functiondef(
        'deltallm_accounting_append_terminal_journal(bigint,jsonb,text[],text[])'::regprocedure
    );
    anchor TEXT:='        SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g WHERE g.grant_id=item->>''grant_id'';';
BEGIN
    IF strpos(definition,anchor)=0 THEN
        RAISE EXCEPTION 'accounting_terminal_receipt_migration_anchor_missing';
    END IF;
    EXECUTE replace(definition,anchor,
$check$        PERFORM deltallm_accounting_validate_terminal_receipt(
            p_finalizations[position]::jsonb,item->>'outcome',(item->>'allowance_exact')::numeric
        );
$check$ || anchor);
END;
$migration$;

COMMIT;
