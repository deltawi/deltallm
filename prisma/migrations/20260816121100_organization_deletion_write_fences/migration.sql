-- Corrective lifecycle fences. Concrete scopes fail closed; a NULL result is
-- reserved for a live referent that is explicitly global/unowned.
CREATE TABLE IF NOT EXISTS deltallm_organizationprincipaltombstone (
  principal_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  deletion_job_id TEXT NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT deltallm_organizationprincipaltombstone_pkey
    PRIMARY KEY (principal_id, organization_id)
);

CREATE INDEX IF NOT EXISTS deltallm_orgprincipaltombstone_org_idx
  ON deltallm_organizationprincipaltombstone (organization_id);

CREATE INDEX IF NOT EXISTS deltallm_orgprincipaltombstone_job_idx
  ON deltallm_organizationprincipaltombstone (deletion_job_id);

CREATE OR REPLACE FUNCTION deltallm_resolve_scope_organization(
  requested_scope_type TEXT,
  requested_scope_id TEXT
)
RETURNS TEXT AS $$
DECLARE
  resolved_organization_id TEXT;
  resolved_team_id TEXT;
BEGIN
  IF requested_scope_id IS NULL OR btrim(requested_scope_id) = '' THEN
    RAISE EXCEPTION 'scope identifier is required'
      USING ERRCODE = '23514';
  END IF;

  IF requested_scope_type = 'organization' THEN
    PERFORM 1
    FROM deltallm_organizationtable o
    WHERE o.organization_id = requested_scope_id;
    IF FOUND THEN
      RETURN requested_scope_id;
    END IF;
    PERFORM 1
    FROM deltallm_organizationtombstone tombstone
    WHERE tombstone.organization_id = requested_scope_id;
    IF FOUND THEN
      RETURN requested_scope_id;
    END IF;
    RAISE EXCEPTION 'organization scope does not exist'
      USING ERRCODE = '23514';
  ELSIF requested_scope_type = 'team' THEN
    SELECT t.organization_id INTO resolved_organization_id
    FROM deltallm_teamtable t
    WHERE t.team_id = requested_scope_id;
    IF FOUND THEN
      RETURN resolved_organization_id;
    END IF;
    PERFORM 1
    FROM deltallm_teamtombstone tombstone
    WHERE tombstone.team_id = requested_scope_id;
    IF FOUND THEN
      RAISE EXCEPTION 'team scope is tombstoned'
        USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'team scope does not exist'
      USING ERRCODE = '23514';
  ELSIF requested_scope_type = 'api_key' THEN
    SELECT COALESCE(v.team_id, u.team_id, s.team_id) INTO resolved_team_id
    FROM deltallm_verificationtoken v
    LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
    LEFT JOIN deltallm_serviceaccount s
      ON s.service_account_id = v.owner_service_account_id
    WHERE v.token = requested_scope_id
    LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'api key scope does not exist'
        USING ERRCODE = '23514';
    END IF;
    IF resolved_team_id IS NULL THEN
      RETURN NULL;
    END IF;
    RETURN deltallm_resolve_scope_organization('team', resolved_team_id);
  ELSIF requested_scope_type = 'user' THEN
    SELECT u.team_id INTO resolved_team_id
    FROM deltallm_usertable u
    WHERE u.user_id = requested_scope_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'user scope does not exist'
        USING ERRCODE = '23514';
    END IF;
    IF resolved_team_id IS NULL THEN
      RETURN NULL;
    END IF;
    RETURN deltallm_resolve_scope_organization('team', resolved_team_id);
  ELSIF requested_scope_type IN ('global', 'anonymous') THEN
    RETURN NULL;
  END IF;

  RAISE EXCEPTION 'unsupported scope type'
    USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_lock_active_organization(
  requested_organization_id TEXT
)
RETURNS VOID AS $$
BEGIN
  IF requested_organization_id IS NULL THEN
    RETURN;
  END IF;
  PERFORM 1
  FROM deltallm_organizationtable o
  WHERE o.organization_id = requested_organization_id
    AND o.lifecycle_state = 'active'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'organization is not active'
      USING ERRCODE = '23514';
  END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_lock_approval_claims(row_data JSONB)
RETURNS VOID AS $$
DECLARE
  candidate_organization_id TEXT;
  claim_organization_id TEXT;
  requested_scope_type TEXT;
  requested_scope_id TEXT;
  requester_api_key TEXT;
  requester_user_id TEXT;
  ownership_claim_seen BOOLEAN;
BEGIN
  candidate_organization_id := NULLIF(row_data->>'organization_id', '');
  requested_scope_type := NULLIF(row_data->>'scope_type', '');
  requested_scope_id := NULLIF(row_data->>'scope_id', '');
  requester_api_key := NULLIF(row_data->>'requested_by_api_key', '');
  requester_user_id := NULLIF(row_data->>'requested_by_user', '');
  ownership_claim_seen := candidate_organization_id IS NOT NULL;

  IF requested_scope_type IN ('organization', 'team', 'api_key') THEN
    claim_organization_id := deltallm_resolve_scope_organization(
      requested_scope_type,
      requested_scope_id
    );
    IF ownership_claim_seen
       AND candidate_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND candidate_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'approval organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      candidate_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  ELSIF requested_scope_type = 'user' THEN
    claim_organization_id := deltallm_resolve_scope_organization(
      'user',
      requested_scope_id
    );
    PERFORM deltallm_lock_active_organization(claim_organization_id);
  ELSE
    RAISE EXCEPTION 'unsupported approval scope type'
      USING ERRCODE = '23514';
  END IF;

  IF requester_api_key IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization(
      'api_key',
      requester_api_key
    );
    IF ownership_claim_seen
       AND candidate_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND candidate_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'approval organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      candidate_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;

  IF NOT ownership_claim_seen
     AND requested_scope_type <> 'user'
     AND requester_user_id IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization(
      'user',
      requester_user_id
    );
    PERFORM deltallm_lock_active_organization(claim_organization_id);
  END IF;

  PERFORM deltallm_lock_active_organization(candidate_organization_id);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_approval_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_owner_organization_id TEXT;
  target_owner_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' AND (
    to_jsonb(NEW) - ARRAY[
      'status',
      'decision_comment',
      'decided_by_account_id',
      'decided_at',
      'expires_at',
      'updated_at'
    ]::text[]
  ) IS NOT DISTINCT FROM (
    to_jsonb(OLD) - ARRAY[
      'status',
      'decision_comment',
      'decided_by_account_id',
      'decided_at',
      'expires_at',
      'updated_at'
    ]::text[]
  ) THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    PERFORM deltallm_lock_approval_claims(to_jsonb(OLD));
    SELECT s.owner_scope_id INTO previous_owner_organization_id
    FROM deltallm_mcpserver s
    WHERE s.mcp_server_id = OLD.mcp_server_id
      AND s.owner_scope_type = 'organization';
    PERFORM deltallm_lock_active_organization(previous_owner_organization_id);
  END IF;

  PERFORM deltallm_lock_approval_claims(to_jsonb(NEW));
  SELECT s.owner_scope_id INTO target_owner_organization_id
  FROM deltallm_mcpserver s
  WHERE s.mcp_server_id = NEW.mcp_server_id
    AND s.owner_scope_type = 'organization';
  PERFORM deltallm_lock_active_organization(target_owner_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_team_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.team_id IS DISTINCT FROM NEW.team_id
     AND OLD.team_id IS NOT NULL THEN
    previous_organization_id := deltallm_resolve_scope_organization(
      'team',
      OLD.team_id
    );
    PERFORM deltallm_lock_active_organization(previous_organization_id);
  END IF;
  IF NEW.team_id IS NULL THEN
    RAISE EXCEPTION 'team scope identifier is required'
      USING ERRCODE = '23514';
  END IF;
  target_organization_id := deltallm_resolve_scope_organization(
    'team',
    NEW.team_id
  );
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_resolve_key_owner_organization(
  requested_team_id TEXT,
  requested_user_id TEXT,
  requested_service_account_id TEXT
)
RETURNS TEXT AS $$
DECLARE
  resolved_team_id TEXT;
BEGIN
  IF requested_team_id IS NOT NULL THEN
    RETURN deltallm_resolve_scope_organization('team', requested_team_id);
  END IF;
  IF requested_user_id IS NOT NULL THEN
    RETURN deltallm_resolve_scope_organization('user', requested_user_id);
  END IF;
  IF requested_service_account_id IS NOT NULL THEN
    SELECT service_account.team_id INTO resolved_team_id
    FROM deltallm_serviceaccount service_account
    WHERE service_account.service_account_id = requested_service_account_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'service account owner does not exist'
        USING ERRCODE = '23514';
    END IF;
    RETURN deltallm_resolve_scope_organization('team', resolved_team_id);
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_key_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    previous_organization_id := deltallm_resolve_key_owner_organization(
      OLD.team_id,
      OLD.user_id,
      OLD.owner_service_account_id
    );
    PERFORM deltallm_lock_active_organization(previous_organization_id);
  END IF;
  target_organization_id := deltallm_resolve_key_owner_organization(
    NEW.team_id,
    NEW.user_id,
    NEW.owner_service_account_id
  );
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_user_team()
RETURNS TRIGGER AS $$
DECLARE
  target_organization_id TEXT;
BEGIN
  IF NEW.team_id IS NULL THEN
    RETURN NEW;
  END IF;
  target_organization_id := deltallm_resolve_scope_organization(
    'team',
    NEW.team_id
  );
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_usertable_active_org_guard
  ON deltallm_usertable;
CREATE TRIGGER deltallm_usertable_active_org_guard
BEFORE INSERT OR UPDATE OF team_id ON deltallm_usertable
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_user_team();

CREATE OR REPLACE FUNCTION deltallm_require_active_scope_organization()
RETURNS TRIGGER AS $$
DECLARE
  previous_organization_id TEXT;
  target_organization_id TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    previous_organization_id := deltallm_resolve_scope_organization(
      OLD.scope_type,
      OLD.scope_id
    );
    PERFORM deltallm_lock_active_organization(previous_organization_id);
  END IF;

  target_organization_id := deltallm_resolve_scope_organization(
    NEW.scope_type,
    NEW.scope_id
  );
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_prompt_log_owner()
RETURNS TRIGGER AS $$
DECLARE
  candidate_organization_id TEXT;
  claim_organization_id TEXT;
  ownership_claim_seen BOOLEAN;
BEGIN
  candidate_organization_id := NEW.organization_id;
  ownership_claim_seen := NEW.organization_id IS NOT NULL;

  IF NEW.team_id IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization('team', NEW.team_id);
    IF ownership_claim_seen
       AND candidate_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND candidate_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'prompt log organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      candidate_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;

  IF NEW.api_key IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization('api_key', NEW.api_key);
    IF ownership_claim_seen
       AND candidate_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND candidate_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'prompt log organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      candidate_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;

  IF NOT ownership_claim_seen AND NEW.user_id IS NOT NULL THEN
    IF EXISTS (
      SELECT 1
      FROM deltallm_organizationprincipaltombstone tombstone
      WHERE tombstone.principal_id = NEW.user_id
    ) OR EXISTS (
      SELECT 1
      FROM deltallm_organizationmembership membership
      WHERE membership.account_id = NEW.user_id
    ) OR EXISTS (
      SELECT 1
      FROM deltallm_usertable u
      JOIN deltallm_teamtable t ON t.team_id = u.team_id
      WHERE u.user_id = NEW.user_id AND t.organization_id IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'organization-associated prompt log requires durable ownership'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  PERFORM deltallm_lock_active_organization(candidate_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_promptrenderlog_active_org_guard
  ON deltallm_promptrenderlog;
CREATE TRIGGER deltallm_promptrenderlog_active_org_guard
BEFORE INSERT OR UPDATE OF organization_id, team_id, api_key, user_id
ON deltallm_promptrenderlog
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_prompt_log_owner();

CREATE OR REPLACE FUNCTION deltallm_require_active_created_owner()
RETURNS TRIGGER AS $$
DECLARE
  row_json JSONB;
  target_organization_id TEXT;
  target_team_id TEXT;
  target_api_key TEXT;
  target_user_id TEXT;
  claim_organization_id TEXT;
  ownership_claim_seen BOOLEAN;
BEGIN
  row_json := to_jsonb(NEW);
  target_organization_id := NULLIF(row_json->>'created_by_organization_id', '');
  target_team_id := NULLIF(row_json->>'created_by_team_id', '');
  target_api_key := NULLIF(row_json->>'created_by_api_key', '');
  target_user_id := NULLIF(row_json->>'created_by_user_id', '');
  ownership_claim_seen := target_organization_id IS NOT NULL;

  IF target_team_id IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization('team', target_team_id);
    IF ownership_claim_seen
       AND target_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND target_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'created owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      target_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;
  IF target_api_key IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization('api_key', target_api_key);
    IF ownership_claim_seen
       AND target_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND target_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'created owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      target_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;
  IF NOT ownership_claim_seen AND target_user_id IS NOT NULL THEN
    target_organization_id := deltallm_resolve_scope_organization('user', target_user_id);
  END IF;

  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_batchcreatesession_active_org_guard
  ON deltallm_batch_create_session;
CREATE TRIGGER deltallm_batchcreatesession_active_org_guard
BEFORE INSERT OR UPDATE OF created_by_organization_id, created_by_team_id,
  created_by_api_key, created_by_user_id
ON deltallm_batch_create_session
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_created_owner();

DROP TRIGGER IF EXISTS deltallm_batchjob_active_org_guard ON deltallm_batch_job;
CREATE TRIGGER deltallm_batchjob_active_org_guard
BEFORE INSERT OR UPDATE OF created_by_organization_id, created_by_team_id,
  created_by_api_key, created_by_user_id
ON deltallm_batch_job
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_created_owner();

CREATE OR REPLACE FUNCTION deltallm_require_active_webhook_owner()
RETURNS TRIGGER AS $$
DECLARE
  target_organization_id TEXT;
  claim_organization_id TEXT;
  ownership_claim_seen BOOLEAN;
  source_organization_id TEXT;
  source_team_id TEXT;
  source_api_key TEXT;
  source_user_id TEXT;
  source_claim_seen BOOLEAN;
BEGIN
  target_organization_id := NEW.created_by_organization_id;
  ownership_claim_seen := target_organization_id IS NOT NULL;
  IF NEW.created_by_team_id IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization(
      'team',
      NEW.created_by_team_id
    );
    IF ownership_claim_seen
       AND target_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND target_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'webhook owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      target_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  END IF;

  SELECT j.created_by_organization_id, j.created_by_team_id,
         j.created_by_api_key, j.created_by_user_id
      INTO source_organization_id, source_team_id, source_api_key, source_user_id
    FROM deltallm_batch_job j
    WHERE j.batch_id = NEW.batch_id;
  IF FOUND THEN
    claim_organization_id := source_organization_id;
    source_claim_seen := source_organization_id IS NOT NULL;
    IF source_team_id IS NOT NULL THEN
      source_organization_id := deltallm_resolve_scope_organization(
        'team', source_team_id
      );
      IF source_claim_seen
         AND claim_organization_id IS NOT NULL
         AND source_organization_id IS NOT NULL
         AND claim_organization_id <> source_organization_id THEN
        RAISE EXCEPTION 'batch owner organization claims conflict'
          USING ERRCODE = '23514';
      END IF;
      IF NOT source_claim_seen THEN
        claim_organization_id := source_organization_id;
        source_claim_seen := TRUE;
      END IF;
    END IF;
    IF source_api_key IS NOT NULL THEN
      source_organization_id := deltallm_resolve_scope_organization(
        'api_key', source_api_key
      );
      IF source_claim_seen
         AND claim_organization_id IS NOT NULL
         AND source_organization_id IS NOT NULL
         AND claim_organization_id <> source_organization_id THEN
        RAISE EXCEPTION 'batch owner organization claims conflict'
          USING ERRCODE = '23514';
      END IF;
      IF NOT source_claim_seen THEN
        claim_organization_id := source_organization_id;
        source_claim_seen := TRUE;
      END IF;
    END IF;
    IF NOT source_claim_seen AND source_user_id IS NOT NULL THEN
      claim_organization_id := deltallm_resolve_scope_organization(
        'user', source_user_id
      );
      source_claim_seen := TRUE;
    END IF;
    IF ownership_claim_seen AND source_claim_seen
       AND target_organization_id IS NOT NULL
       AND claim_organization_id IS NOT NULL
       AND target_organization_id <> claim_organization_id THEN
      RAISE EXCEPTION 'webhook owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen AND source_claim_seen THEN
      target_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  ELSIF NOT ownership_claim_seen THEN
    RAISE EXCEPTION 'webhook batch does not exist and no owner is recorded'
      USING ERRCODE = '23514';
  END IF;
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_batchwebhook_active_org_guard
  ON deltallm_batch_webhook_outbox;
CREATE TRIGGER deltallm_batchwebhook_active_org_guard
BEFORE INSERT OR UPDATE OF created_by_organization_id, created_by_team_id, batch_id
ON deltallm_batch_webhook_outbox
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_webhook_owner();

CREATE OR REPLACE FUNCTION deltallm_require_active_invitation_scopes()
RETURNS TRIGGER AS $$
DECLARE
  requested_organization_id TEXT;
  requested_team_id TEXT;
  declared_organization_id TEXT;
  resolved_organization_id TEXT;
BEGIN
  IF jsonb_array_length(COALESCE(NEW.metadata->'organization_invites', '[]'::jsonb)) > 100
     OR jsonb_array_length(COALESCE(NEW.metadata->'team_invites', '[]'::jsonb)) > 100 THEN
    RAISE EXCEPTION 'invitation scope count exceeds limit'
      USING ERRCODE = '23514';
  END IF;

  FOR requested_organization_id IN
    SELECT DISTINCT NULLIF(item->>'organization_id', '')
    FROM jsonb_array_elements(
      COALESCE(NEW.metadata->'organization_invites', '[]'::jsonb)
    ) item
  LOOP
    IF requested_organization_id IS NULL THEN
      RAISE EXCEPTION 'invitation organization scope is required'
        USING ERRCODE = '23514';
    END IF;
    PERFORM deltallm_lock_active_organization(requested_organization_id);
  END LOOP;

  FOR requested_team_id, declared_organization_id IN
    SELECT DISTINCT
      NULLIF(item->>'team_id', ''),
      NULLIF(item->>'organization_id', '')
    FROM jsonb_array_elements(
      COALESCE(NEW.metadata->'team_invites', '[]'::jsonb)
    ) item
  LOOP
    IF requested_team_id IS NULL OR declared_organization_id IS NULL THEN
      RAISE EXCEPTION 'invitation team scope is incomplete'
        USING ERRCODE = '23514';
    END IF;
    resolved_organization_id := deltallm_resolve_scope_organization(
      'team',
      requested_team_id
    );
    IF resolved_organization_id IS DISTINCT FROM declared_organization_id THEN
      RAISE EXCEPTION 'invitation team organization does not match'
        USING ERRCODE = '23514';
    END IF;
    PERFORM deltallm_lock_active_organization(resolved_organization_id);
  END LOOP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_platforminvitation_active_scope_guard
  ON deltallm_platforminvitation;
CREATE TRIGGER deltallm_platforminvitation_active_scope_guard
BEFORE INSERT OR UPDATE OF metadata ON deltallm_platforminvitation
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_invitation_scopes();

CREATE OR REPLACE FUNCTION deltallm_require_active_scheduler_flow_scope()
RETURNS TRIGGER AS $$
DECLARE
  target_organization_id TEXT;
  source_team_id TEXT;
BEGIN
  IF NEW.tenant_scope_type IN ('organization', 'team', 'user') THEN
    target_organization_id := deltallm_resolve_scope_organization(
      NEW.tenant_scope_type,
      NEW.tenant_scope_id
    );
  ELSIF NEW.tenant_scope_type = 'api_key' THEN
    SELECT j.created_by_organization_id, j.created_by_team_id
      INTO target_organization_id, source_team_id
    FROM deltallm_batch_job j
    WHERE j.tenant_scope_type = 'api_key'
      AND j.tenant_scope_id = NEW.tenant_scope_id
      AND j.status IN ('queued', 'in_progress', 'finalizing')
    ORDER BY j.created_at ASC
    LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'scheduler api key scope has no active job'
        USING ERRCODE = '23514';
    END IF;
    IF target_organization_id IS NULL AND source_team_id IS NOT NULL THEN
      target_organization_id := deltallm_resolve_scope_organization(
        'team',
        source_team_id
      );
    END IF;
  ELSIF NEW.tenant_scope_type <> 'anonymous' THEN
    RAISE EXCEPTION 'unsupported scheduler scope type'
      USING ERRCODE = '23514';
  END IF;
  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_batchschedulerflow_active_scope_guard
  ON deltallm_batch_scheduler_flow;
CREATE TRIGGER deltallm_batchschedulerflow_active_scope_guard
BEFORE INSERT OR UPDATE OF tenant_scope_type, tenant_scope_id
ON deltallm_batch_scheduler_flow
FOR EACH ROW EXECUTE FUNCTION deltallm_require_active_scheduler_flow_scope();

CREATE OR REPLACE FUNCTION deltallm_skip_removed_team_spend_counter()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM 1
  FROM deltallm_teamtable t
  WHERE t.team_id = NEW.team_id
  FOR KEY SHARE;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deltallm_teammodelspend_team_guard
  ON deltallm_teammodelspend;
CREATE TRIGGER deltallm_teammodelspend_team_guard
BEFORE INSERT OR UPDATE OF team_id ON deltallm_teammodelspend
FOR EACH ROW EXECUTE FUNCTION deltallm_skip_removed_team_spend_counter();
