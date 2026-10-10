-- One PostgreSQL lease bounds cleanup across all gateway replicas.
CREATE TABLE deltallm_externalauthmaintenancelease (
    lease_id TEXT PRIMARY KEY CHECK (lease_id = 'external-auth-cleanup'),
    next_run_at TIMESTAMPTZ NOT NULL
);
INSERT INTO deltallm_externalauthmaintenancelease VALUES ('external-auth-cleanup', '-infinity');
CREATE INDEX deltallm_external_child_cleanup_idx ON deltallm_platformsession (expires_at, session_id)
WHERE external_parent_id IS NOT NULL;
