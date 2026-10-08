-- Keep immutable historical IDs and revocation tombstones after normal tenant cleanup.
-- Deferred guards validate every live association. Deletion suspends access atomically.
ALTER TABLE deltallm_externalauthbinding DROP CONSTRAINT deltallm_externalauthbinding_organization_id_fkey;
ALTER TABLE deltallm_externalauthbinding DROP CONSTRAINT deltallm_externalauthbinding_team_id_fkey;
ALTER TABLE deltallm_externalauthsubject DROP CONSTRAINT deltallm_externalauthsubject_account_id_fkey;
ALTER TABLE deltallm_externalauthsubject DROP CONSTRAINT deltallm_externalauthsubject_identity_id_fkey;
ALTER TABLE deltallm_externalauthsubject DROP CONSTRAINT deltallm_externalauthsubject_runtime_user_id_fkey;

CREATE OR REPLACE FUNCTION deltallm_check_external_subject(checked_id TEXT) RETURNS void LANGUAGE plpgsql AS $$
DECLARE mapping RECORD;
BEGIN
  SELECT s.*, b.team_id, b.organization_id INTO mapping
  FROM deltallm_externalauthsubject s JOIN deltallm_externalauthbinding b USING (binding_id)
  WHERE s.subject_id = checked_id;
  IF NOT FOUND OR mapping.state = 'suspended' THEN RETURN; END IF;
  IF mapping.runtime_user_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM deltallm_usertable u WHERE u.user_id = mapping.runtime_user_id AND u.team_id = mapping.team_id
  ) THEN
    RAISE EXCEPTION 'External runtime identity is outside its workspace' USING ERRCODE = '23514';
  END IF;
  IF mapping.identity_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM deltallm_platformidentity i WHERE i.identity_id = mapping.identity_id
      AND i.account_id = mapping.account_id AND i.subject = mapping.subject
      AND i.provider = 'external:' || encode(digest(mapping.identity_issuer, 'sha256'), 'hex')
  ) THEN
    RAISE EXCEPTION 'External platform identity does not match its account' USING ERRCODE = '23514';
  END IF;
  IF mapping.state = 'active' AND (
    NOT EXISTS (SELECT 1 FROM deltallm_platformaccount a WHERE a.account_id = mapping.account_id AND a.role = 'org_user')
    OR EXISTS (SELECT 1 FROM deltallm_organizationmembership m WHERE m.account_id = mapping.account_id
                AND m.organization_id <> mapping.organization_id)
    OR EXISTS (SELECT 1 FROM deltallm_teammembership m WHERE m.account_id = mapping.account_id AND m.team_id <> mapping.team_id)
  ) THEN
    RAISE EXCEPTION 'Active external customer account must be dedicated' USING ERRCODE = '23514';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION deltallm_external_tenant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_row JSONB; new_row JSONB;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  IF EXISTS (
    SELECT 1 FROM deltallm_externalauthbinding b LEFT JOIN deltallm_teamtable t USING (team_id)
      LEFT JOIN deltallm_organizationtable o ON o.organization_id = b.organization_id
    WHERE b.state = 'active' AND (t.organization_id IS DISTINCT FROM b.organization_id
      OR o.organization_id IS NULL OR o.lifecycle_state <> 'active')
      AND (b.binding_id IN (new_row->>'binding_id', old_row->>'binding_id')
           OR b.team_id IN (new_row->>'team_id', old_row->>'team_id'))
  ) THEN
    RAISE EXCEPTION 'Active external binding must retain its tenant association' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE FUNCTION deltallm_external_retire_references(
  organization TEXT DEFAULT NULL, team TEXT DEFAULT NULL, account TEXT DEFAULT NULL,
  identity TEXT DEFAULT NULL, runtime_user TEXT DEFAULT NULL
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  WITH retired_bindings AS (
    UPDATE deltallm_externalauthbinding b
    SET state = 'suspended', epoch = epoch + 1, version = version + 1,
        updated_at = clock_timestamp() AT TIME ZONE 'UTC'
    WHERE b.state <> 'suspended'
      AND (b.organization_id = organization OR b.team_id = team)
    RETURNING binding_id
  ), matching AS MATERIALIZED (
    SELECT s.subject_id FROM deltallm_externalauthsubject s
    JOIN deltallm_externalauthbinding b USING (binding_id)
    WHERE b.organization_id = organization OR b.team_id = team OR s.account_id = account
      OR s.identity_id = identity OR s.runtime_user_id = runtime_user
  ), retired_subjects AS (
    UPDATE deltallm_externalauthsubject s
    SET state = 'suspended', epoch = epoch + 1, version = version + 1,
        updated_at = clock_timestamp() AT TIME ZONE 'UTC'
    FROM matching m WHERE s.subject_id = m.subject_id AND s.state <> 'suspended'
    RETURNING s.subject_id
  ), revoked_parents AS (
    UPDATE deltallm_externalauthparentsession p
    SET revoked_at = clock_timestamp() AT TIME ZONE 'UTC',
        updated_at = clock_timestamp() AT TIME ZONE 'UTC'
    FROM matching m WHERE p.subject_id = m.subject_id AND p.revoked_at IS NULL
    RETURNING p.parent_id
  )
  UPDATE deltallm_platformsession c
  SET revoked_at = clock_timestamp() AT TIME ZONE 'UTC'
  WHERE c.revoked_at IS NULL AND c.external_parent_id IN (
    SELECT p.parent_id FROM deltallm_externalauthparentsession p JOIN matching m USING (subject_id)
  );
END $$;

CREATE FUNCTION deltallm_external_lifecycle_retirement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  CASE TG_TABLE_NAME
    WHEN 'deltallm_organizationtable' THEN
      IF TG_OP = 'DELETE' OR NEW.lifecycle_state <> 'active' THEN
        PERFORM deltallm_external_retire_references(organization => OLD.organization_id);
      END IF;
    WHEN 'deltallm_teamtable' THEN
      PERFORM deltallm_external_retire_references(team => OLD.team_id);
    WHEN 'deltallm_platformaccount' THEN
      PERFORM deltallm_external_retire_references(account => OLD.account_id);
    WHEN 'deltallm_platformidentity' THEN
      PERFORM deltallm_external_retire_references(identity => OLD.identity_id);
    WHEN 'deltallm_usertable' THEN
      PERFORM deltallm_external_retire_references(runtime_user => OLD.user_id);
  END CASE;
  RETURN OLD;
END $$;
CREATE TRIGGER external_organization_retirement BEFORE DELETE ON deltallm_organizationtable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_lifecycle_retirement();
-- Return NEW for updates; a BEFORE UPDATE trigger must preserve the lifecycle mutation.
CREATE FUNCTION deltallm_external_organization_retirement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.lifecycle_state <> 'active' AND OLD.lifecycle_state = 'active' THEN
    PERFORM deltallm_external_retire_references(organization => OLD.organization_id);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_organization_deactivation BEFORE UPDATE OF lifecycle_state ON deltallm_organizationtable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_organization_retirement();
CREATE TRIGGER external_team_retirement BEFORE DELETE ON deltallm_teamtable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_lifecycle_retirement();
CREATE TRIGGER external_account_retirement BEFORE DELETE ON deltallm_platformaccount
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_lifecycle_retirement();
CREATE TRIGGER external_identity_retirement BEFORE DELETE ON deltallm_platformidentity
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_lifecycle_retirement();
CREATE TRIGGER external_runtime_retirement BEFORE DELETE ON deltallm_usertable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_lifecycle_retirement();
