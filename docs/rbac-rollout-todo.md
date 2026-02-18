# RBAC Rollout TODO

## Worktrees
- [x] Create `.worktrees/rbac-phase-1-foundation`
- [x] Create `.worktrees/rbac-phase-2-schema`
- [x] Create `.worktrees/rbac-phase-3-auth`
- [x] Create `.worktrees/rbac-phase-4-enforcement`

## Phase 1 - Foundation
- [x] Define canonical RBAC roles and permission constants
- [x] Add platform auth/session context model
- [x] Add platform identity/session service scaffold
- [x] Add optional MFA enrollment + verification helpers
- [x] Wire app startup + request middleware for session auth context
- [x] Add internal login/logout/me endpoints with session cookie
- [x] Add MFA enroll endpoints (start/confirm)
- [x] Allow platform-admin session access in admin dependency

## Phase 2 - Schema
- [x] Add Prisma models/tables for platform accounts, sessions, identities
- [x] Add Prisma models/tables for org/team memberships
- [x] Add indexes/unique constraints for memberships and sessions

## Phase 3 - Auth + SSO Integration
- [x] Bootstrap first platform admin from config (internal login)
- [ ] Add password-change flow marker (force change flag)
- [x] Integrate SSO callback with DB identity linking/upsert
- [x] Issue session cookie for SSO login

## Phase 4 - RBAC Enforcement
- [ ] Add permission-based dependency for admin operations
- [ ] Map roles to permissions for platform/org/team scopes
- [ ] Start enforcing on org/team/user/key admin endpoints

## Validation
- [ ] Compile checks on changed modules
- [ ] End-to-end login + admin route checks (internal + SSO path)
- [ ] Verify master key remains functional (break-glass)
