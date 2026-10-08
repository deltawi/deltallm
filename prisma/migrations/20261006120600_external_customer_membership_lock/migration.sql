-- Keep the account lock for dedicated external customers. Ordinary operator
-- membership edits must retain their existing concurrency behavior.
CREATE OR REPLACE FUNCTION deltallm_external_membership_lock() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_account TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN target_account := OLD.account_id; ELSE target_account := NEW.account_id; END IF;
  IF EXISTS (SELECT 1 FROM deltallm_externalauthsubject
             WHERE account_id = target_account AND state = 'active') THEN
    PERFORM 1 FROM deltallm_platformaccount WHERE account_id = target_account FOR UPDATE;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.account_id IS DISTINCT FROM NEW.account_id
    AND EXISTS (SELECT 1 FROM deltallm_externalauthsubject
                WHERE account_id = OLD.account_id AND state = 'active') THEN
    PERFORM 1 FROM deltallm_platformaccount WHERE account_id = OLD.account_id FOR UPDATE;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
