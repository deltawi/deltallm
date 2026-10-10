-- Bound recovery scans before eligibility checks. Preserve every money effect.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE deltallm_accounting_recovery_cursors (
    protocol_name TEXT NOT NULL DEFAULT 'primary',
    generation BIGINT NOT NULL,
    last_expires_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00+00',
    last_grant_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (protocol_name,generation),
    FOREIGN KEY (protocol_name,generation)
        REFERENCES deltallm_accounting_protocols(protocol_name,generation)
        ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE INDEX deltallm_accounting_grant_expiry_work_idx
    ON deltallm_accounting_grants(generation,expires_at,grant_id)
    WHERE protocol_name='primary' AND state='active';
CREATE INDEX deltallm_accounting_grant_drain_work_idx
    ON deltallm_accounting_grants(generation,expires_at,grant_id)
    WHERE protocol_name='primary' AND state='draining';

DO $migration$
DECLARE
    definition TEXT;
    old_declaration TEXT := $old$    candidate_ids TEXT[];$old$;
    new_declaration TEXT := $new$    candidate_ids TEXT[];
    inspected_ids TEXT[];
    cursor_row deltallm_accounting_recovery_cursors%ROWTYPE;
    next_expires_at TIMESTAMPTZ;
    next_grant_id TEXT;$new$;
    old_selection TEXT := $old$    SELECT array_agg(candidate.grant_id ORDER BY candidate.grant_id)
    INTO candidate_ids
    FROM (
        SELECT g.grant_id FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.state='draining'
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_accounting_terminal_journal j
              WHERE j.grant_id=g.grant_id AND j.status<>'completed' OFFSET 0
          )
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_billing_operations b
              WHERE b.accounting_grant_id=g.grant_id
                AND b.accounting_state='reserved'
          )
        ORDER BY g.expires_at,g.grant_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ) candidate;$old$;
    new_selection TEXT := $new$    IF NOT EXISTS (
        SELECT 1 FROM deltallm_accounting_protocols p
        WHERE p.protocol_name='primary' AND p.generation=p_generation
    ) THEN RETURN 0; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM deltallm_accounting_recovery_cursors c
        WHERE c.protocol_name='primary' AND c.generation=p_generation
    ) THEN
        INSERT INTO deltallm_accounting_recovery_cursors(protocol_name,generation)
            VALUES ('primary',p_generation) ON CONFLICT DO NOTHING;
    END IF;
    SELECT c.* INTO cursor_row FROM deltallm_accounting_recovery_cursors c
        WHERE c.protocol_name='primary' AND c.generation=p_generation
        FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN 0; END IF;
    WITH forward_scan AS MATERIALIZED (
        SELECT g.grant_id,g.expires_at,0 AS scan_order
        FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation AND g.state='draining'
          AND (g.expires_at,g.grant_id)>(cursor_row.last_expires_at,cursor_row.last_grant_id)
        ORDER BY g.expires_at,g.grant_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ), wrapped_scan AS MATERIALIZED (
        SELECT g.grant_id,g.expires_at,1 AS scan_order
        FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation AND g.state='draining'
          AND (g.expires_at,g.grant_id)<=(cursor_row.last_expires_at,cursor_row.last_grant_id)
        ORDER BY g.expires_at,g.grant_id FOR UPDATE SKIP LOCKED
        LIMIT (p_limit-(SELECT count(*) FROM forward_scan))
    ), inspected AS MATERIALIZED (
        SELECT * FROM forward_scan UNION ALL SELECT * FROM wrapped_scan
    ) SELECT array_agg(grant_id ORDER BY scan_order,expires_at,grant_id),
        (array_agg(expires_at ORDER BY scan_order DESC,expires_at DESC,grant_id DESC))[1],
        (array_agg(grant_id ORDER BY scan_order DESC,expires_at DESC,grant_id DESC))[1]
        INTO inspected_ids,next_expires_at,next_grant_id FROM inspected;
    IF inspected_ids IS NULL THEN RETURN 0; END IF;
    UPDATE deltallm_accounting_recovery_cursors SET
        last_expires_at=next_expires_at,last_grant_id=next_grant_id
        WHERE protocol_name='primary' AND generation=p_generation;
    SELECT array_agg(selected.grant_id ORDER BY selected.grant_id) INTO candidate_ids
    FROM unnest(inspected_ids) selected(grant_id)
    WHERE NOT EXISTS (
        SELECT 1 FROM deltallm_accounting_terminal_journal j
        WHERE j.grant_id=selected.grant_id AND j.status<>'completed' OFFSET 0
    ) AND NOT EXISTS (
        SELECT 1 FROM deltallm_billing_operations b
        WHERE b.accounting_grant_id=selected.grant_id AND b.accounting_state='reserved' OFFSET 0
    );$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_reconcile_grants(bigint,integer)'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,old_declaration,''))<>length(old_declaration)
        OR length(definition)-length(replace(definition,old_selection,''))<>length(old_selection) THEN
        RAISE EXCEPTION 'accounting_recovery_scan_definition';
    END IF;
    definition:=replace(definition,old_declaration,new_declaration);
    EXECUTE replace(definition,old_selection,new_selection);
END;
$migration$;

COMMIT;
