# UI Authorization Rollout TODO

## Phase 1 - Contract and Backend Primitives
- [x] Define session-level `ui_access` contract for page visibility
- [x] Add shared backend capability builders for organizations, teams, and batches
- [x] Expose `ui_access` from `/auth/me`
- [x] Expose team capabilities on list/detail/create/update responses
- [x] Expose organization capabilities on list/detail/update responses
- [x] Expose batch capabilities on list/detail responses

## Phase 2 - Navigation and Route Gating
- [x] Add shared frontend authorization helpers
- [x] Gate nav visibility from `ui_access`
- [x] Gate routes from `ui_access`
- [x] Hide dead-end pages from developer roles
- [x] Keep dashboard behind the same spend-read contract as the current dashboard data sources
- [x] Keep team creation behind org-scoped team-create authority, not team-scoped update alone

## Phase 3 - Action-Level Capability Gating
- [x] Hide model mutation controls unless `model_admin`
- [x] Hide organization mutation controls unless resource capabilities allow them
- [x] Hide team mutation controls unless resource capabilities allow them
- [x] Hide batch cancel unless resource capabilities allow it
- [x] Keep developer batch pages available in read-only mode while gating cancel by capability
- [x] Keep MCP on the existing capability-driven pattern and align it with shared helpers

## Validation
- [x] Focused backend tests for `ui_access` and capability payloads
- [ ] Focused frontend tests for nav/route/action visibility
- [x] UI build and focused regression checks
