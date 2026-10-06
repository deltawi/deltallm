-- Keep streamed document reads parameterized even with alternate join planning.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    old_operation TEXT := $old$FROM raw JOIN deltallm_accounting_terminal_payloads document
            ON document.journal_sequence=(v->>'sequence')::bigint$old$;
    new_operation TEXT := $new$FROM raw CROSS JOIN LATERAL (
            SELECT document.reservation_payload FROM deltallm_accounting_terminal_payloads document
            WHERE document.journal_sequence=(v->>'sequence')::bigint OFFSET 0
        ) document$new$;
    old_event TEXT := $old$JOIN deltallm_accounting_terminal_payloads document
            ON document.journal_sequence=(raw.v->>'sequence')::bigint$old$;
    new_event TEXT := $new$CROSS JOIN LATERAL (
            SELECT document.finalization_payload FROM deltallm_accounting_terminal_payloads document
            WHERE document.journal_sequence=(raw.v->>'sequence')::bigint OFFSET 0
        ) document$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_materialize_terminal_journal(bigint,text,uuid,bigint[])'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,old_operation,''))<>length(old_operation)
      OR length(definition)-length(replace(definition,old_event,''))<>length(old_event) THEN
        RAISE EXCEPTION 'accounting_streamed_document_definition';
    END IF;
    definition:=replace(definition,old_operation,new_operation);
    EXECUTE replace(definition,old_event,new_event);
END;
$migration$;

COMMIT;

