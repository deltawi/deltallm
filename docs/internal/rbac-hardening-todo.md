# RBAC Hardening TODO

Status legend: `[ ]` pending, `[~]` in progress, `[x]` done.

## Phase 1 — Canonical platform roles
- [x] Merge `platform_co_admin` into `platform_admin` everywhere.
- [x] Remove `platform_co_admin` acceptance in backend role validation.
- [x] Remove UI references to `platform_co_admin`.
- [x] Update docs to list only canonical platform roles.

## Phase 2 — Remove unused/non-enforced role paths
- [ ] Remove unused API-key role gate helper (`require_role`) if not used.
- [ ] Stop carrying non-enforced `user_role` in API-key auth model/path.
- [ ] Validate/normalize user-table role-like field as non-RBAC metadata.

## Phase 3 — Enforce scoped RBAC on legacy UI endpoints
- [ ] Replace broad `require_authenticated` with permission-based guards where applicable.
- [ ] Keep `master_key` as explicit super-admin bypass only.
- [ ] Add/adjust tests for permission checks and regressions.

## Rollup
- [ ] All phases merged to `main`.
