# Hierarchical Rate Limits Plan (Org, Team, User, API Key)

## Phase 0 - Setup
- [x] Define phase strategy and worktree layout
- [x] Create branch/worktree per phase
- [ ] Keep each phase independently verifiable before moving forward

## Phase 1 - Data & Context Plumbing
- [x] Add organization-level RPM/TPM fields in schema
- [x] Extend auth model/context to carry resolved org/team/user/key limits
- [x] Load team/user/org limits from DB in key validation path
- [x] Preserve backward compatibility when fields are missing

## Phase 2 - Enforcement Engine
- [ ] Implement atomic multi-scope limiter in Redis (all-or-nothing increment)
- [ ] Add scope-aware limit resolution policy (org/team/user/key)
- [ ] Enforce hierarchical RPM/TPM in shared rate-limit middleware
- [ ] Keep parallel-request limit behavior for API keys
- [ ] Add structured 429 reason metadata and retry-after

## Phase 3 - Management APIs
- [ ] Teams: allow create/update of `rpm_limit` and `tpm_limit`
- [ ] Users: allow create/update of `rpm_limit` and `tpm_limit`
- [ ] Organizations: add list/create/update endpoints and include RPM/TPM
- [ ] Ensure responses expose configured values clearly

## Phase 4 - Tests & Validation
- [ ] Unit tests for limit resolution precedence and null behavior
- [ ] Unit/integration tests for middleware enforcement across all scopes
- [ ] Atomic limiter tests (no partial increments on failure)
- [ ] Regression checks for chat + embeddings paths
- [ ] Run test suite and lint for touched modules

## Phase Transitions
- [ ] Phase 1 -> Phase 2: merge/cherry-pick only after passing phase checks
- [ ] Phase 2 -> Phase 3: merge/cherry-pick only after passing phase checks
- [ ] Phase 3 -> Phase 4: merge/cherry-pick only after passing phase checks

## Worktree Layout
- `.worktrees/phase-1-context`
- `.worktrees/phase-2-enforcement`
- `.worktrees/phase-3-admin-api`
- `.worktrees/phase-4-tests`
