-- A renewal may retain gateway MFA only when the exact secret was verified.
ALTER TABLE deltallm_platformsession ADD COLUMN external_mfa_secret_digest TEXT;
ALTER TABLE deltallm_platformsession ADD CONSTRAINT deltallm_external_mfa_proof_check CHECK (
    external_mfa_secret_digest IS NULL OR (
        external_parent_id IS NOT NULL AND mfa_verified AND external_mfa_secret_digest ~ '^[0-9a-f]{64}$'
    )
);
