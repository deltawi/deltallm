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
- [x] Add password-change flow marker (force change flag)
- [x] Integrate SSO callback with DB identity linking/upsert
- [x] Issue session cookie for SSO login

## Phase 4 - RBAC Enforcement
- [x] Add permission-based dependency for admin operations
- [x] Map roles to permissions for platform/org/team scopes
- [x] Start enforcing on org/team/user/key admin endpoints

## Phase 5 - Membership Management + Scoped Access
- [x] Add platform account management endpoints
- [x] Add org/team membership management endpoints
- [x] Add tenant-scoped org/team read endpoints
- [x] Enforce team/org scoped permissions (without org->team inheritance)

## Validation
- [x] Compile checks on changed modules
- [x] End-to-end login + admin route checks (internal path)
- [x] End-to-end SSO login callback/session checks
- [x] End-to-end tenant membership scoped access checks
- [x] Verify master key remains functional (break-glass)
