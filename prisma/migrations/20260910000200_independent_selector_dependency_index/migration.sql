-- Control-plane dependency lookup; no business data, historical policy or FK rewrite.
-- Only current published selector policies occupy this index, not the history.
SET lock_timeout = '5s';
SET statement_timeout = '60s';
CREATE INDEX IF NOT EXISTS "deltallm_routepolicy_selector_dependency_idx"
ON "deltallm_routepolicy" ((policy_json->'selector'->>'classifier_deployment_id'))
WHERE status = 'published' AND semantics_version >= 3;
RESET statement_timeout;
RESET lock_timeout;
