BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
-- Each of at most seven canonical/legacy aliases stops after its first row,
-- including scopes containing many equal-priority bindings.
CREATE INDEX deltallm_promptbinding_enabled_top_idx
    ON deltallm_promptbinding (scope_type, scope_id, priority, created_at, prompt_binding_id)
    WHERE enabled = TRUE;
COMMIT;
