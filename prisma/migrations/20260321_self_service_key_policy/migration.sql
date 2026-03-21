-- Self-service key creation policy columns on team table
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS self_service_keys_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS self_service_max_keys_per_user INTEGER;
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS self_service_budget_ceiling DOUBLE PRECISION;
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS self_service_require_expiry BOOLEAN DEFAULT FALSE;
ALTER TABLE deltallm_teamtable ADD COLUMN IF NOT EXISTS self_service_max_expiry_days INTEGER;
