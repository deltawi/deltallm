-- Add per-model rate limit columns to team and organization tables
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS model_rpm_limit JSONB;
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS model_tpm_limit JSONB;
ALTER TABLE deltallm_organizationtable ADD COLUMN IF NOT EXISTS model_rpm_limit JSONB;
ALTER TABLE deltallm_organizationtable ADD COLUMN IF NOT EXISTS model_tpm_limit JSONB;
