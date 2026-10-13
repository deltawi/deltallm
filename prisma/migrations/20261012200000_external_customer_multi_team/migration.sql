-- One external binding can cover additional teams in its organization.
-- The binding's own team_id stays the immutable, implicit default team: it has
-- no row in the new tables, so bindings, subjects and sessions written by an
-- older gateway binary remain valid during a rolling deployment.
CREATE TABLE "deltallm_externalauthbindingteam" (
    "binding_id" TEXT NOT NULL,
    "team_id" TEXT NOT NULL,
    "state" TEXT NOT NULL DEFAULT 'active',
    "version" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthbindingteam_pkey" PRIMARY KEY ("binding_id", "team_id"),
    CONSTRAINT "deltallm_externalauthbindingteam_binding_id_fkey" FOREIGN KEY ("binding_id")
        REFERENCES "deltallm_externalauthbinding"("binding_id") ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "external_binding_team_bounds" CHECK (
        octet_length("team_id") BETWEEN 1 AND 200 AND "state" IN ('active', 'detached') AND "version" >= 0)
);
CREATE INDEX "deltallm_externalauthbindingteam_team_id_idx" ON "deltallm_externalauthbindingteam"("team_id");
-- Bounded rollback previews count live attachments without scanning detached history.
CREATE INDEX "deltallm_external_binding_team_active_idx" ON "deltallm_externalauthbindingteam"("binding_id", "team_id")
    WHERE "state" = 'active';

-- A grant must use the subject's own binding.
CREATE UNIQUE INDEX "deltallm_externalauthsubject_subject_id_binding_id_key"
    ON "deltallm_externalauthsubject"("subject_id", "binding_id");

CREATE TABLE "deltallm_externalauthsubjectteam" (
    "subject_id" TEXT NOT NULL,
    "binding_id" TEXT NOT NULL,
    "team_id" TEXT NOT NULL,
    "runtime_user_id" TEXT,
    "state" TEXT NOT NULL DEFAULT 'active',
    "epoch" INTEGER NOT NULL DEFAULT 0,
    "version" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_externalauthsubjectteam_pkey" PRIMARY KEY ("subject_id", "team_id"),
    CONSTRAINT "deltallm_externalauthsubjectteam_subject_id_binding_id_fkey" FOREIGN KEY ("subject_id", "binding_id")
        REFERENCES "deltallm_externalauthsubject"("subject_id", "binding_id") ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "deltallm_externalauthsubjectteam_binding_id_team_id_fkey" FOREIGN KEY ("binding_id", "team_id")
        REFERENCES "deltallm_externalauthbindingteam"("binding_id", "team_id") ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "external_subject_team_bounds" CHECK (
        "state" IN ('active', 'removed') AND "epoch" >= 0 AND "version" >= 0)
);
CREATE UNIQUE INDEX "deltallm_externalauthsubjectteam_runtime_user_id_key" ON "deltallm_externalauthsubjectteam"("runtime_user_id");
CREATE INDEX "deltallm_externalauthsubjectteam_team_id_binding_id_idx" ON "deltallm_externalauthsubjectteam"("team_id", "binding_id");

-- Expand only. A child without a team acts in the binding's default team, which is
-- what every child written by an older binary means. A later release can require it.
ALTER TABLE "deltallm_platformsession" ADD COLUMN "external_team_id" TEXT,
ADD COLUMN "external_team_epoch" INTEGER;
ALTER TABLE "deltallm_platformsession" ADD CONSTRAINT "external_session_team"
  CHECK (num_nonnulls(external_team_id, external_team_epoch) = 0
    OR (external_parent_id IS NOT NULL AND num_nonnulls(external_team_id, external_team_epoch) = 2
      AND external_team_epoch >= 0)) NOT VALID;
ALTER TABLE "deltallm_platformsession" VALIDATE CONSTRAINT "external_session_team";

CREATE FUNCTION deltallm_external_team_grant_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'deltallm_externalauthbindingteam' THEN
    IF NEW.binding_id IS DISTINCT FROM OLD.binding_id OR NEW.team_id IS DISTINCT FROM OLD.team_id THEN
      RAISE EXCEPTION 'External team attachment is immutable' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.subject_id IS DISTINCT FROM OLD.subject_id OR NEW.binding_id IS DISTINCT FROM OLD.binding_id
    OR NEW.team_id IS DISTINCT FROM OLD.team_id OR NEW.epoch < OLD.epoch
    OR (OLD.runtime_user_id IS NOT NULL AND NEW.runtime_user_id IS DISTINCT FROM OLD.runtime_user_id
        AND NOT (NEW.runtime_user_id IS NULL AND NEW.state = 'removed')) THEN
    RAISE EXCEPTION 'External team grant mapping is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_binding_team_immutable BEFORE UPDATE ON deltallm_externalauthbindingteam
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_immutable();
CREATE TRIGGER external_subject_team_immutable BEFORE UPDATE ON deltallm_externalauthsubjectteam
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_immutable();

-- Attachment and grant writers serialize with team deletion and with membership writers.
CREATE FUNCTION deltallm_external_team_grant_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM 1 FROM deltallm_teamtable WHERE team_id = NEW.team_id FOR SHARE;
  IF TG_TABLE_NAME = 'deltallm_externalauthsubjectteam' THEN
    PERFORM 1 FROM deltallm_platformaccount a JOIN deltallm_externalauthsubject s ON s.account_id = a.account_id
      WHERE s.subject_id = NEW.subject_id FOR UPDATE OF a;
    PERFORM 1 FROM deltallm_usertable WHERE user_id = NEW.runtime_user_id FOR SHARE;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER external_binding_team_lock BEFORE INSERT OR UPDATE ON deltallm_externalauthbindingteam
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_lock();
CREATE TRIGGER external_subject_team_lock BEFORE INSERT OR UPDATE ON deltallm_externalauthsubjectteam
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_lock();

-- An active account may hold memberships only in its default team and in its
-- active grants on active attachments. Each granted runtime user stays in its team.
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
  IF EXISTS (
    SELECT 1 FROM deltallm_externalauthsubjectteam g
    WHERE g.subject_id = checked_id AND g.state = 'active' AND g.runtime_user_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM deltallm_usertable u WHERE u.user_id = g.runtime_user_id AND u.team_id = g.team_id)
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
    OR EXISTS (
      SELECT 1 FROM deltallm_teammembership m WHERE m.account_id = mapping.account_id AND m.team_id <> mapping.team_id
        AND NOT EXISTS (
          SELECT 1 FROM deltallm_externalauthsubjectteam g
          JOIN deltallm_externalauthbindingteam bt ON bt.binding_id = g.binding_id AND bt.team_id = g.team_id
          WHERE g.subject_id = checked_id AND g.team_id = m.team_id AND g.state = 'active' AND bt.state = 'active'))
  ) THEN
    RAISE EXCEPTION 'Active external customer account must be dedicated' USING ERRCODE = '23514';
  END IF;
END $$;

-- Rechecks also find the subject of an additional team's runtime user.
CREATE OR REPLACE FUNCTION deltallm_external_mapping_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item RECORD; old_row JSONB; new_row JSONB;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME IN ('deltallm_externalauthsubject', 'deltallm_externalauthsubjectteam') THEN
    PERFORM deltallm_check_external_subject(COALESCE(new_row->>'subject_id', old_row->>'subject_id'));
  ELSIF TG_TABLE_NAME = 'deltallm_externalauthbindingteam' THEN
    FOR item IN SELECT subject_id FROM deltallm_externalauthsubjectteam
      WHERE team_id = COALESCE(new_row->>'team_id', old_row->>'team_id')
        AND binding_id = COALESCE(new_row->>'binding_id', old_row->>'binding_id')
    LOOP PERFORM deltallm_check_external_subject(item.subject_id); END LOOP;
  ELSE
    FOR item IN SELECT subject_id FROM deltallm_externalauthsubject
      WHERE account_id IN (new_row->>'account_id', old_row->>'account_id')
        OR identity_id IN (new_row->>'identity_id', old_row->>'identity_id')
        OR runtime_user_id IN (new_row->>'user_id', old_row->>'user_id')
      UNION
      SELECT subject_id FROM deltallm_externalauthsubjectteam
      WHERE runtime_user_id IN (new_row->>'user_id', old_row->>'user_id')
    LOOP PERFORM deltallm_check_external_subject(item.subject_id); END LOOP;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_subject_team_mapping AFTER INSERT OR UPDATE ON deltallm_externalauthsubjectteam
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();
CREATE CONSTRAINT TRIGGER external_binding_team_mapping AFTER UPDATE ON deltallm_externalauthbindingteam
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_mapping_guard();

-- An active attachment must name another team of the binding's organization.
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
  ) OR EXISTS (
    SELECT 1 FROM deltallm_externalauthbindingteam bt
      JOIN deltallm_externalauthbinding b ON b.binding_id = bt.binding_id
      LEFT JOIN deltallm_teamtable t ON t.team_id = bt.team_id
    WHERE bt.state = 'active' AND (t.organization_id IS DISTINCT FROM b.organization_id OR bt.team_id = b.team_id)
      AND (bt.binding_id IN (new_row->>'binding_id', old_row->>'binding_id')
           OR bt.team_id IN (new_row->>'team_id', old_row->>'team_id'))
  ) THEN
    RAISE EXCEPTION 'Active external binding must retain its tenant association' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_binding_team_tenant AFTER INSERT OR UPDATE ON deltallm_externalauthbindingteam
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION deltallm_external_tenant_guard();

-- A child names its default team, a granted team, or no team (default, older binary).
CREATE OR REPLACE FUNCTION deltallm_external_session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.external_parent_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM deltallm_externalauthparentsession p
      JOIN deltallm_externalauthsubject s USING (subject_id)
      JOIN deltallm_externalauthbinding b ON b.binding_id = s.binding_id
    WHERE p.parent_id = NEW.external_parent_id AND s.account_id = NEW.account_id
      AND NEW.expires_at <= p.expires_at AND NEW.external_generation <= p.generation
      AND (NEW.external_team_id IS NULL OR NEW.external_team_id = b.team_id OR EXISTS (
        SELECT 1 FROM deltallm_externalauthsubjectteam g
        WHERE g.subject_id = s.subject_id AND g.team_id = NEW.external_team_id))
  ) THEN
    RAISE EXCEPTION 'External child must match its parent account and lifetime' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;

-- Deleting an additional team, or the runtime user of one grant, retires only
-- that access. The default team keeps the existing binding retirement.
CREATE FUNCTION deltallm_external_retire_team_grants(team TEXT DEFAULT NULL, runtime_user TEXT DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  -- A deleted runtime user is cleared from its grant, so a later grant creates a
  -- new runtime user on first use. The team membership goes with the grant.
  WITH detached AS (
    UPDATE deltallm_externalauthbindingteam bt
    SET state = 'detached', version = version + 1, updated_at = clock_timestamp() AT TIME ZONE 'UTC'
    WHERE bt.team_id = team AND bt.state <> 'detached'
    RETURNING bt.binding_id
  ), removed AS (
    UPDATE deltallm_externalauthsubjectteam g
    SET state = 'removed',
        epoch = CASE WHEN g.state = 'removed' THEN g.epoch ELSE g.epoch + 1 END,
        version = version + 1,
        runtime_user_id = CASE WHEN g.runtime_user_id = runtime_user THEN NULL ELSE g.runtime_user_id END,
        updated_at = clock_timestamp() AT TIME ZONE 'UTC'
    WHERE (g.state <> 'removed' AND g.team_id = team) OR g.runtime_user_id = runtime_user
    RETURNING g.subject_id, g.team_id
  ), memberships AS (
    DELETE FROM deltallm_teammembership m
    USING removed r JOIN deltallm_externalauthsubject s ON s.subject_id = r.subject_id
    WHERE m.account_id = s.account_id AND m.team_id = r.team_id
    RETURNING m.membership_id
  )
  UPDATE deltallm_platformsession c
  SET revoked_at = clock_timestamp() AT TIME ZONE 'UTC'
  FROM deltallm_externalauthparentsession p, removed r, (SELECT count(*) FROM memberships) AS cleared
  WHERE c.revoked_at IS NULL AND c.external_parent_id = p.parent_id
    AND p.subject_id = r.subject_id AND c.external_team_id = r.team_id;
END $$;

CREATE FUNCTION deltallm_external_team_grant_retirement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'deltallm_teamtable' THEN
    PERFORM deltallm_external_retire_team_grants(team => OLD.team_id);
  ELSE
    PERFORM deltallm_external_retire_team_grants(runtime_user => OLD.user_id);
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER external_team_grant_retirement BEFORE DELETE ON deltallm_teamtable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_retirement();
CREATE TRIGGER external_runtime_grant_retirement BEFORE DELETE ON deltallm_usertable
  FOR EACH ROW EXECUTE FUNCTION deltallm_external_team_grant_retirement();
