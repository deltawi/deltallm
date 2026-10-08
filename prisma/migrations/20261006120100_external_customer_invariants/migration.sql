-- New tables are empty. These constraints do not backfill or rewrite customer data.
ALTER TABLE deltallm_externalauthintegration ADD CONSTRAINT external_integration_bounds
  CHECK (octet_length(integration_id) BETWEEN 1 AND 80 AND epoch >= 0 AND version >= 0);
ALTER TABLE deltallm_externalauthbinding ADD CONSTRAINT external_binding_bounds
  CHECK (octet_length(external_customer_id) BETWEEN 1 AND 200
    AND profile = 'customer_v1' AND state IN ('active', 'suspended') AND epoch >= 0 AND version >= 0);
ALTER TABLE deltallm_externalauthsubject ADD CONSTRAINT external_subject_bounds
  CHECK (octet_length(identity_issuer) BETWEEN 1 AND 512 AND octet_length(subject) BETWEEN 1 AND 200
    AND state IN ('pending', 'active', 'suspended') AND epoch >= 0 AND version >= 0
    AND (state <> 'active' OR (account_id IS NOT NULL AND identity_id IS NOT NULL AND runtime_user_id IS NOT NULL)));
ALTER TABLE deltallm_externalauthparentsession ADD CONSTRAINT external_parent_bounds
  CHECK (external_session_id_hash ~ '^[0-9a-f]{64}$' AND expires_at > auth_time
    AND expires_at <= auth_time + INTERVAL '12 hours' AND generation >= 0);
ALTER TABLE deltallm_externalauthassertionuse ADD CONSTRAINT external_assertion_bounds
  CHECK (jti_hash ~ '^[0-9a-f]{64}$' AND retain_until >= received_at + INTERVAL '15 minutes'
    AND purpose IN ('gateway_session_exchange', 'gateway_session_revoke', 'gateway_subject_suspend',
                    'gateway_account_link', 'gateway_runtime_identity_bind')
    AND outcome IN ('claimed', 'succeeded', 'denied'));
ALTER TABLE deltallm_platformsession ADD CONSTRAINT external_session_metadata
  CHECK (num_nonnulls(external_parent_id, external_generation, external_integration_epoch,
    external_binding_epoch, external_subject_epoch) = 0
    OR (num_nonnulls(external_parent_id, external_generation, external_integration_epoch,
      external_binding_epoch, external_subject_epoch) = 5 AND external_generation > 0
      AND external_integration_epoch >= 0 AND external_binding_epoch >= 0 AND external_subject_epoch >= 0));

CREATE FUNCTION deltallm_external_binding_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.integration_id IS DISTINCT FROM OLD.integration_id
    OR NEW.external_customer_id IS DISTINCT FROM OLD.external_customer_id
    OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
    OR NEW.team_id IS DISTINCT FROM OLD.team_id OR NEW.profile IS DISTINCT FROM OLD.profile THEN
    RAISE EXCEPTION 'External customer binding is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_binding_immutable BEFORE UPDATE ON deltallm_externalauthbinding
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_binding_immutable();

CREATE FUNCTION deltallm_external_subject_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.integration_id IS DISTINCT FROM OLD.integration_id OR NEW.binding_id IS DISTINCT FROM OLD.binding_id
    OR NEW.identity_issuer IS DISTINCT FROM OLD.identity_issuer OR NEW.subject IS DISTINCT FROM OLD.subject
    OR (OLD.account_id IS NOT NULL AND NEW.account_id IS DISTINCT FROM OLD.account_id)
    OR (OLD.identity_id IS NOT NULL AND NEW.identity_id IS DISTINCT FROM OLD.identity_id)
    OR (OLD.runtime_user_id IS NOT NULL AND NEW.runtime_user_id IS DISTINCT FROM OLD.runtime_user_id) THEN
    RAISE EXCEPTION 'External subject mapping is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_subject_immutable BEFORE UPDATE ON deltallm_externalauthsubject
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_subject_immutable();

CREATE FUNCTION deltallm_external_mapping_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'deltallm_externalauthbinding' THEN
    PERFORM 1 FROM deltallm_teamtable WHERE team_id = NEW.team_id FOR SHARE;
  ELSE
    PERFORM 1 FROM deltallm_platformaccount WHERE account_id = NEW.account_id FOR UPDATE;
    PERFORM 1 FROM deltallm_platformidentity WHERE identity_id = NEW.identity_id FOR SHARE;
    PERFORM 1 FROM deltallm_usertable WHERE user_id = NEW.runtime_user_id FOR SHARE;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_binding_mapping_lock BEFORE INSERT OR UPDATE ON deltallm_externalauthbinding
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_lock();
CREATE TRIGGER external_subject_mapping_lock BEFORE INSERT OR UPDATE ON deltallm_externalauthsubject
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_lock();

CREATE FUNCTION deltallm_external_parent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.integration_id IS DISTINCT FROM OLD.integration_id
    OR NEW.external_session_id_hash IS DISTINCT FROM OLD.external_session_id_hash
    OR NEW.subject_id IS DISTINCT FROM OLD.subject_id OR NEW.auth_time IS DISTINCT FROM OLD.auth_time
    OR NEW.expires_at > OLD.expires_at OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
    OR NEW.generation < OLD.generation THEN
    RAISE EXCEPTION 'External parent cannot be reassigned or reopened' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_parent_immutable BEFORE UPDATE ON deltallm_externalauthparentsession
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_parent_immutable();

CREATE FUNCTION deltallm_check_external_subject(checked_id TEXT) RETURNS void LANGUAGE plpgsql AS $$
DECLARE mapping RECORD;
BEGIN
  SELECT s.*, b.team_id, b.organization_id INTO mapping
  FROM deltallm_externalauthsubject s JOIN deltallm_externalauthbinding b USING (binding_id)
  WHERE s.subject_id = checked_id;
  IF NOT FOUND THEN RETURN; END IF;
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

-- Membership writers serialize with account provisioning and role changes. The deferred
-- check sees the final transaction state, so suspension can precede an explicit migration.
CREATE FUNCTION deltallm_external_membership_lock() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_account TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN target_account := OLD.account_id; ELSE target_account := NEW.account_id; END IF;
  PERFORM 1 FROM deltallm_platformaccount WHERE account_id = target_account FOR UPDATE;
  IF TG_OP = 'UPDATE' AND OLD.account_id IS DISTINCT FROM NEW.account_id THEN
    PERFORM 1 FROM deltallm_platformaccount WHERE account_id = OLD.account_id FOR UPDATE;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER external_org_membership_lock BEFORE INSERT OR UPDATE OR DELETE ON deltallm_organizationmembership
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_membership_lock();
CREATE TRIGGER external_team_membership_lock BEFORE INSERT OR UPDATE OR DELETE ON deltallm_teammembership
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_membership_lock();

CREATE FUNCTION deltallm_external_mapping_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item RECORD; old_row JSONB; new_row JSONB;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME = 'deltallm_externalauthsubject' THEN
    PERFORM deltallm_check_external_subject(COALESCE(new_row->>'subject_id', old_row->>'subject_id'));
  ELSE
    FOR item IN SELECT subject_id FROM deltallm_externalauthsubject
      WHERE account_id IN (new_row->>'account_id', old_row->>'account_id')
        OR identity_id IN (new_row->>'identity_id', old_row->>'identity_id')
        OR runtime_user_id IN (new_row->>'user_id', old_row->>'user_id')
    LOOP PERFORM deltallm_check_external_subject(item.subject_id); END LOOP;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_subject_mapping AFTER INSERT OR UPDATE ON deltallm_externalauthsubject
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_account_mapping AFTER UPDATE ON deltallm_platformaccount
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_identity_mapping AFTER UPDATE ON deltallm_platformidentity
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_runtime_mapping AFTER UPDATE ON deltallm_usertable
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_org_membership_mapping AFTER INSERT OR UPDATE OR DELETE ON deltallm_organizationmembership
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_team_membership_mapping AFTER INSERT OR UPDATE OR DELETE ON deltallm_teammembership
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();

CREATE FUNCTION deltallm_external_tenant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_row JSONB; new_row JSONB;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  IF EXISTS (
    SELECT 1 FROM deltallm_externalauthbinding b JOIN deltallm_teamtable t USING (team_id)
    WHERE b.state = 'active' AND t.organization_id IS DISTINCT FROM b.organization_id
      AND (b.binding_id IN (new_row->>'binding_id', old_row->>'binding_id')
           OR b.team_id IN (new_row->>'team_id', old_row->>'team_id'))
  ) THEN
    RAISE EXCEPTION 'Active external binding must retain its tenant association' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_binding_tenant AFTER INSERT OR UPDATE ON deltallm_externalauthbinding
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_tenant_guard();
CREATE CONSTRAINT TRIGGER external_team_tenant AFTER UPDATE ON deltallm_teamtable
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_tenant_guard();

CREATE FUNCTION deltallm_external_session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.external_parent_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM deltallm_externalauthparentsession p
      JOIN deltallm_externalauthsubject s USING (subject_id)
    WHERE p.parent_id = NEW.external_parent_id AND s.account_id = NEW.account_id
      AND NEW.expires_at <= p.expires_at AND NEW.external_generation <= p.generation
  ) THEN
    RAISE EXCEPTION 'External child must match its parent account and lifetime' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_child_parent AFTER INSERT OR UPDATE ON deltallm_platformsession
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_session_guard();
