# RBAC Hardening TODO

Status legend: `[ ]` pending, `[~]` in progress, `[x]` done.

## Phase 1 — Canonical platform roles
- [x] Merge `platform_co_admin` into `platform_admin` everywhere.
- [x] Remove `platform_co_admin` acceptance in backend role validation.
- [x] Remove UI references to `platform_co_admin`.
- [x] Update docs to list only canonical platform roles.

## Phase 2 — Remove unused/non-enforced role paths
- [x] Remove unused API-key role gate helper (`require_role`) if not used.
- [x] Stop carrying non-enforced `user_role` in API-key auth model/path.
- [x] Validate/normalize user-table role-like field as non-RBAC metadata.

## Phase 3 — Enforce scoped RBAC on legacy UI endpoints
- [x] Replace broad `require_authenticated` with permission-based guards where applicable.
- [x] Keep `master_key` as explicit super-admin bypass only.
- [x] Add/adjust tests for permission checks and regressions.

## Phase 4 — Clarify profile types vs RBAC roles
- [x] Update Users/Teams UI labels from "Role" to "Profile Type" where tied to `deltallm_usertable.user_role`.
- [x] Add contextual UI guidance that authorization is managed in Access Control memberships.
- [x] Update API/admin docs to describe `user_role` as non-RBAC profile metadata.
- [x] Add tests for profile type validation and alias normalization.

## Rollup
- [x] All phases merged to `main`.
