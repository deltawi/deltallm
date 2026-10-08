-- Bounded rollback must not repeatedly scan old revoked rows.
CREATE INDEX deltallm_external_parent_live_idx ON deltallm_externalauthparentsession(parent_id) WHERE revoked_at IS NULL;
CREATE INDEX deltallm_external_child_live_idx ON deltallm_platformsession(session_id) WHERE external_parent_id IS NOT NULL AND revoked_at IS NULL;
CREATE INDEX deltallm_external_key_revocation_retained_idx ON deltallm_cacheinvalidationoutbox(invalidation_id) WHERE scope_type = 'key_hash' AND metadata->>'auth_revocation' = 'true';
