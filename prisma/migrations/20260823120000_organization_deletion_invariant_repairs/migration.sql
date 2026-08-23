-- Append-only corrections for lifecycle drain semantics and durable batch
-- ownership. Keep transactional DDL bounded; legacy data is normalized by the
-- coordinated, paged rollout command before deletion requests are enabled.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

CREATE OR REPLACE FUNCTION deltallm_resolve_created_user_organization(
  requested_user_id TEXT
)
RETURNS TEXT AS $$
DECLARE
  resolved_organization_id TEXT;
  resolved_count INTEGER;
BEGIN
  IF requested_user_id IS NULL OR btrim(requested_user_id) = '' THEN
    RAISE EXCEPTION 'created user identifier is required'
      USING ERRCODE = '23514';
  END IF;

  WITH candidate_organizations AS (
    SELECT team.organization_id
    FROM deltallm_usertable legacy_user
    JOIN deltallm_teamtable team ON team.team_id = legacy_user.team_id
    WHERE legacy_user.user_id = requested_user_id
      AND team.organization_id IS NOT NULL
    UNION
    SELECT membership.organization_id
    FROM deltallm_organizationmembership membership
    WHERE membership.account_id = requested_user_id
    UNION
    SELECT team.organization_id
    FROM deltallm_teammembership membership
    JOIN deltallm_teamtable team ON team.team_id = membership.team_id
    WHERE membership.account_id = requested_user_id
      AND team.organization_id IS NOT NULL
    UNION
    SELECT tombstone.organization_id
    FROM deltallm_organizationprincipaltombstone tombstone
    WHERE tombstone.principal_id = requested_user_id
  )
  SELECT MIN(organization_id), COUNT(*)::int
    INTO resolved_organization_id, resolved_count
  FROM candidate_organizations;

  IF resolved_count > 1 THEN
    RAISE EXCEPTION 'created user organization ownership is ambiguous'
      USING ERRCODE = '23514';
  END IF;
  IF resolved_count = 1 THEN
    RETURN resolved_organization_id;
  END IF;
  IF EXISTS (
    SELECT 1 FROM deltallm_usertable WHERE user_id = requested_user_id
  ) OR EXISTS (
    SELECT 1 FROM deltallm_platformaccount WHERE account_id = requested_user_id
  ) THEN
    RETURN NULL;
  END IF;

  RAISE EXCEPTION 'created user does not exist'
    USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION deltallm_resolve_created_owner_organization(
  row_data JSONB
)
RETURNS TEXT AS $$
DECLARE
  target_organization_id TEXT;
  target_team_id TEXT;
  target_api_key TEXT;
  target_user_id TEXT;
  target_owner_account_id TEXT;
  claim_organization_id TEXT;
  ownership_claim_seen BOOLEAN;
BEGIN
  target_organization_id := NULLIF(row_data->>'created_by_organization_id', '');
  target_team_id := NULLIF(row_data->>'created_by_team_id', '');
  target_api_key := NULLIF(row_data->>'created_by_api_key', '');
  target_user_id := NULLIF(row_data->>'created_by_user_id', '');
  target_owner_account_id := NULLIF(row_data->>'created_by_owner_account_id', '');
  ownership_claim_seen := target_organization_id IS NOT NULL;

  IF target_organization_id IS NOT NULL THEN
    target_organization_id := deltallm_resolve_scope_organization(
      'organization', target_organization_id
    );
  END IF;

  IF target_team_id IS NOT NULL THEN
    claim_organization_id := deltallm_resolve_scope_organization('team', target_team_id);
    IF ownership_claim_seen
       AND target_organization_id IS DISTINCT FROM claim_organization_id THEN
      RAISE EXCEPTION 'created owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    target_organization_id := claim_organization_id;
    ownership_claim_seen := TRUE;
  END IF;

  IF target_api_key IS NOT NULL AND EXISTS (
    SELECT 1 FROM deltallm_verificationtoken token
    WHERE token.token = target_api_key
  ) THEN
    claim_organization_id := deltallm_resolve_scope_organization('api_key', target_api_key);
    IF ownership_claim_seen
       AND claim_organization_id IS NOT NULL
       AND target_organization_id IS DISTINCT FROM claim_organization_id THEN
      RAISE EXCEPTION 'created owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    IF NOT ownership_claim_seen THEN
      target_organization_id := claim_organization_id;
      ownership_claim_seen := TRUE;
    END IF;
  ELSIF target_api_key IS NOT NULL AND NOT ownership_claim_seen THEN
    -- Master-key, JWT, and custom-auth actors may have no verification-token
    -- row. With no durable tenant claim they remain explicitly global.
    ownership_claim_seen := TRUE;
  END IF;

  IF NOT ownership_claim_seen AND target_owner_account_id IS NOT NULL THEN
    target_organization_id := deltallm_resolve_created_user_organization(
      target_owner_account_id
    );
    ownership_claim_seen := TRUE;
  END IF;
  IF NOT ownership_claim_seen AND target_user_id IS NOT NULL THEN
    target_organization_id := deltallm_resolve_created_user_organization(target_user_id);
    ownership_claim_seen := TRUE;
  END IF;

  RETURN target_organization_id;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION deltallm_require_active_created_owner()
RETURNS TRIGGER AS $$
DECLARE
  target_organization_id TEXT;
BEGIN
  target_organization_id := deltallm_resolve_created_owner_organization(to_jsonb(NEW));
  NEW.created_by_organization_id := target_organization_id;

  -- Filling the immutable snapshot on an already-admitted record is cleanup,
  -- not new tenant work. No other ownership claim may change on this path.
  IF TG_OP = 'UPDATE'
     AND OLD.created_by_organization_id IS NULL
     AND NEW.created_by_organization_id IS NOT NULL
     AND OLD.created_by_team_id IS NOT DISTINCT FROM NEW.created_by_team_id
     AND OLD.created_by_api_key IS NOT DISTINCT FROM NEW.created_by_api_key
     AND OLD.created_by_user_id IS NOT DISTINCT FROM NEW.created_by_user_id
     AND OLD.created_by_owner_account_id
         IS NOT DISTINCT FROM NEW.created_by_owner_account_id THEN
    RETURN NEW;
  END IF;

  PERFORM deltallm_lock_active_organization(target_organization_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_webhook_owner()
RETURNS TRIGGER AS $$
DECLARE
  target_organization_id TEXT;
  source_organization_id TEXT;
  source_status TEXT;
BEGIN
  target_organization_id := deltallm_resolve_created_owner_organization(to_jsonb(NEW));

  SELECT deltallm_resolve_created_owner_organization(to_jsonb(job)), job.status::text
    INTO source_organization_id, source_status
  FROM deltallm_batch_job job
  WHERE job.batch_id = NEW.batch_id;

  IF FOUND THEN
    IF target_organization_id IS NOT NULL
       AND source_organization_id IS NOT NULL
       AND target_organization_id IS DISTINCT FROM source_organization_id THEN
      RAISE EXCEPTION 'webhook owner organization claims conflict'
        USING ERRCODE = '23514';
    END IF;
    target_organization_id := COALESCE(
      target_organization_id,
      source_organization_id
    );
  ELSIF target_organization_id IS NULL THEN
    RAISE EXCEPTION 'webhook batch does not exist and no owner is recorded'
      USING ERRCODE = '23514';
  END IF;

  NEW.created_by_organization_id := target_organization_id;
  IF target_organization_id IS NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE'
     AND OLD.created_by_organization_id IS NULL
     AND NEW.created_by_organization_id IS NOT NULL
     AND OLD.created_by_team_id IS NOT DISTINCT FROM NEW.created_by_team_id
     AND OLD.batch_id IS NOT DISTINCT FROM NEW.batch_id THEN
    RETURN NEW;
  END IF;

  PERFORM 1
  FROM deltallm_organizationtable organization
  WHERE organization.organization_id = target_organization_id
    AND organization.lifecycle_state = 'active'
  FOR SHARE;
  IF FOUND THEN
    RETURN NEW;
  END IF;

  -- Terminal deliveries are inert and may be ownership-backfilled after the
  -- lifecycle transition. A terminal batch admitted before deletion may also
  -- atomically record its outcome, but the callback is suppressed.
  IF NEW.status IN ('delivered', 'failed') THEN
    RETURN NEW;
  END IF;
  IF source_status IN ('completed', 'failed', 'cancelled', 'expired')
     AND NEW.event_type = 'batch.' || source_status THEN
    NEW.status := 'failed';
    NEW.last_error := 'organization_deletion_requested';
    NEW.locked_by := NULL;
    NEW.lease_expires_at := NULL;
    NEW.delivered_at := NULL;
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'organization is not active'
    USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deltallm_require_active_invitation_scopes()
RETURNS TRIGGER AS $$
DECLARE
  requested_organization_id TEXT;
  requested_team_id TEXT;
  declared_organization_id TEXT;
  resolved_organization_id TEXT;
  is_scope_removal BOOLEAN := FALSE;
BEGIN
  IF jsonb_array_length(COALESCE(NEW.metadata->'organization_invites', '[]'::jsonb)) > 100
     OR jsonb_array_length(COALESCE(NEW.metadata->'team_invites', '[]'::jsonb)) > 100 THEN
    RAISE EXCEPTION 'invitation scope count exceeds limit'
      USING ERRCODE = '23514';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    is_scope_removal := NOT EXISTS (
      SELECT item
      FROM jsonb_array_elements(
        COALESCE(NEW.metadata->'organization_invites', '[]'::jsonb)
      ) item
      EXCEPT
      SELECT item
      FROM jsonb_array_elements(
        COALESCE(OLD.metadata->'organization_invites', '[]'::jsonb)
      ) item
    ) AND NOT EXISTS (
      SELECT item
      FROM jsonb_array_elements(
        COALESCE(NEW.metadata->'team_invites', '[]'::jsonb)
      ) item
      EXCEPT
      SELECT item
      FROM jsonb_array_elements(
        COALESCE(OLD.metadata->'team_invites', '[]'::jsonb)
      ) item
    );
    IF is_scope_removal THEN
      RETURN NEW;
    END IF;
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
      'team', requested_team_id
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
