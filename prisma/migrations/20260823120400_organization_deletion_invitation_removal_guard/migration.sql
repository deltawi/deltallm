SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

CREATE OR REPLACE FUNCTION deltallm_require_active_invitation_scopes()
RETURNS TRIGGER AS $$
DECLARE
  requested_organization_id TEXT;
  requested_team_id TEXT;
  declared_organization_id TEXT;
  resolved_organization_id TEXT;
  old_organization_invites JSONB;
  new_organization_invites JSONB;
  old_team_invites JSONB;
  new_team_invites JSONB;
  is_scope_removal BOOLEAN := FALSE;
BEGIN
  new_organization_invites := COALESCE(
    NEW.metadata->'organization_invites', '[]'::jsonb
  );
  new_team_invites := COALESCE(NEW.metadata->'team_invites', '[]'::jsonb);

  IF jsonb_array_length(new_organization_invites) > 100
     OR jsonb_array_length(new_team_invites) > 100 THEN
    RAISE EXCEPTION 'invitation scope count exceeds limit'
      USING ERRCODE = '23514';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    old_organization_invites := COALESCE(
      OLD.metadata->'organization_invites', '[]'::jsonb
    );
    old_team_invites := COALESCE(OLD.metadata->'team_invites', '[]'::jsonb);
    is_scope_removal := (
      new_organization_invites IS DISTINCT FROM old_organization_invites
      OR new_team_invites IS DISTINCT FROM old_team_invites
    ) AND (
      NEW.metadata - ARRAY['organization_invites', 'team_invites']::text[]
    ) IS NOT DISTINCT FROM (
      OLD.metadata - ARRAY['organization_invites', 'team_invites']::text[]
    ) AND NOT EXISTS (
      SELECT item FROM jsonb_array_elements(new_organization_invites) item
      EXCEPT
      SELECT item FROM jsonb_array_elements(old_organization_invites) item
    ) AND NOT EXISTS (
      SELECT item FROM jsonb_array_elements(new_team_invites) item
      EXCEPT
      SELECT item FROM jsonb_array_elements(old_team_invites) item
    );
    IF is_scope_removal THEN
      RETURN NEW;
    END IF;
  END IF;

  FOR requested_organization_id IN
    SELECT DISTINCT NULLIF(item->>'organization_id', '')
    FROM jsonb_array_elements(new_organization_invites) item
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
    FROM jsonb_array_elements(new_team_invites) item
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
