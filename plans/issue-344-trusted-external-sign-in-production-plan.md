# Trusted external customer sign in production implementation plan

Implement [issue 344](https://github.com/deltawi/deltallm/issues/344) as an extension of the DeltaLLM account and session system. A verified Console customer must enter the gateway UI, create private credentials and models, share permitted assets, and manage permitted API keys under the customer's gateway account. The Console remains the owner of Clerk authentication and payment entitlement.

This plan specifies the production release, including the gateway changes, the Console integration, security tests, operations, migration, and rollback. All release gates apply to the first enabled production deployment.

| Item | Value |
| --- | --- |
| Prepared | 2026-10-06 |
| Issue | [344](https://github.com/deltawi/deltallm/issues/344) |
| Base | `b8a4ad6fde71b20e6db1d8cf25c980ff7b8ec8bd`, verified current `main` |
| Branch | `codex/issue-344-external-customer-sign-in` |
| Worktree | `deltallm-external-customer-sign-in` in the DeltaLLM project directory |
| Status | Gateway implementation and local verification complete; disabled by default; Replit Console and deployed qualification remain release gates |
| Governing rules | [RULES.md](../RULES.md) |

The gateway now implements the trust, durable identity/binding/session state, exchange and administration, scoped asset/key authorization, revocation, mounted UI, owned runtime, cleanup, diagnostics, deployment configuration, and rollback tool described below. The [design decision](issue-344-external-auth-design-decision.md) records final ownership and deviations. Local verification is recorded in [verification evidence](issue-344-verification-evidence.json). The Console is in Replit; the user assigned its matching implementation to their Console agent. Its concrete handoff is the [Console connection contract](../docs/guides/console-gateway-connection.md). Production integrations remain disabled until the external delivery gates pass. This plan and decision remain in `plans/` because `docs/internal/` is ignored by Git.

## 1 Scope and product decisions

1. Use a short-lived assertion signed by the explicitly trusted Console backend. The gateway verifies the assertion with configured public keys. Clerk SDK calls and Clerk secrets remain in the Console. Direct Clerk bearer-token login and new OIDC providers are outside this release.
2. Register each customer's existing organization and team as a gateway-controlled tenant binding. Exchange never creates another organization or team. A signed claim alone cannot establish a new tenant binding.
3. Use the exact external identity issuer and its stable subject as the identity key. For Clerk, these are the configured Clerk instance issuer and the Clerk user ID. The Console assertion issuer identifies the trusted signer; it is a separate value.
4. Use dedicated customer accounts. One external identity maps to one gateway account and one customer binding. An organization can contain several customer accounts. A gateway account cannot belong to two customer bindings in this release. Changing a binding's organization or team requires an explicit migration outside sign-in.
5. Store workspace scope on the external session. Derive permissions from the live bound memberships and intersect them with the fixed customer permission ceiling. Extra account memberships never become session permissions. If an account gains a platform-admin role or an unrelated membership, reject its external sessions until an operator resolves the conflict.
6. Create new accounts as `org_user`, with an `org_member` membership and a `team_developer` membership. Preserve existing membership roles. Later exchanges do not recreate a removed membership or reverse a suspension.
7. Permit private creator assets and sharing with the bound organization or team under the existing asset rules. Do not permit customers to create public grants. Reading an already published asset remains subject to the existing model and inference policy.
8. Permit listing, viewing, creating, revoking, and deleting the customer's own API keys. Creation uses the existing team self-service policy. Budget increases, spend resets, user/team reassignment, key-owner changes, and tenant-wide key administration are denied. A team with self-service keys disabled remains disabled.
9. Use the existing `psk_` opaque gateway session and its approved hash format. Return the raw session only to the Console backend over TLS. The browser receives an opaque Console proxy reference cookie, never the gateway session or an assertion.
10. Existing account linking requires an explicit, audited platform-admin action and a fresh assertion from the trusted integration. Email equality is only a collision check. It is never linking authority. Platform-admin accounts and accounts with unrelated memberships or owned asset audiences are ineligible.
11. Preserve local MFA and forced-password-change controls on an eligible linked account. Verified email is not MFA. A linked account that requires gateway MFA receives a restricted session with `next_step=mfa_verify`. A new external account does not need another gateway login.
12. Authentication does not change payment state, team blocking, budgets, spend, prices, margins, tiers, model allowances, or existing customer data. Console provisioning and billing remain separate operations.
13. Browser-session logout and suspension of external sign-in do not revoke application API keys. Key revocation and inference blocking use the existing explicit controls. Document this separation in the customer and operator guides.

The dedicated-account rule is necessary because managed assets are owned by account ID, without a tenant column. Session filtering alone cannot isolate private assets owned by one account in several workspaces. Do not add a new asset ownership scheme for this issue.

## 2 Baseline code and reuse boundaries

| Current code | Reuse and required change |
| --- | --- |
| [PlatformIdentityService](../src/services/platform_identity_service.py) | Reuse account/session hashes, session issuance, account-state checks, and revocation. Extract the session repository and service before adding this concern to the 953-line module. |
| [SSOAccountService](../src/services/sso_account_service.py) and [SSO identity checks](../src/auth/sso_identity.py) | Reuse subject lookup and identity-link conflict protection. Add an explicit resolution policy that rejects email-based linking for external auth. Preserve the existing operator SSO policy and its tests. |
| [Platform membership queries](../src/db/identity/platform_memberships.py) | Reuse conflict-safe membership insertion on first provisioning only. Validate the binding and active organization before insertion. |
| [Self-registration provisioning](../src/services/self_registration_provisioning.py) | Extract a typed runtime-user provisioning seam. Do not invoke default organization/team creation, copy self-registration defaults, or select an existing runtime user by email as ownership proof. |
| [Session middleware](../src/middleware/platform_auth.py), [admin authorization](../src/middleware/admin.py), and [AuthScope](../src/api/admin/endpoints/common.py) | Propagate an external session's bound scope through every shared authorization entry point. Reject mixed privileged credentials on external customer requests. |
| [Managed asset policy](../src/services/managed_asset_access.py) and [asset routes](../src/api/admin/endpoints/managed_assets.py) | Preserve owner, reader/editor, credential-binding, and audience rules. Apply the external audience ceiling in the same policy path. |
| [Key routes](../src/api/admin/endpoints/keys.py) and [runtime scope](../src/services/runtime_scopes.py) | Reuse own-key creation and revocation. Preserve account ownership and runtime-user attribution. Extract the touched key mutation path from the 1,774-line endpoint module. |
| [Cache invalidation service](../src/services/cache_invalidation.py) and [outbox](../src/db/runtime/cache_invalidation_outbox.py) | Use durable invalidation for the touched key revoke/delete paths. Current local exception handling is insufficient for this release. |
| [Audit service](../src/services/audit_service.py), [control audit](../src/api/audit.py), and [audit ingestion](../src/db/audit/audit_ingestion.py) | Reuse transactional required audit. There is already a transaction-bound repository path; no second audit queue is needed. |
| [Auth bootstrap](../src/bootstrap/auth.py) and [infrastructure bootstrap](../src/bootstrap/infrastructure.py) | Own new services, the bounded control-plane pool, crypto executor, and maintenance task. Preserve shutdown ordering. |
| [Auth routes](../src/api/auth.py) and [public router](../src/api/v1/router.py) | Add a small external-auth router through the current route owner. Do not add feature logic to the 1,480-line auth module or to `src/main.py`. |
| [UI auth state](../ui/src/lib/auth.tsx), [transport](../ui/src/lib/api/transport.ts), [UI entry](../ui/src/main.tsx), and [Vite configuration](../ui/vite.config.ts) | Keep `auth_mode=session`. Add session-source/workspace fields and a safe proxy mount configuration. Current production assets use `/ui/`, API calls use root-relative paths, and BrowserRouter has no basename. |

## 3 Ownership and security invariants

| Concern | Production invariant |
| --- | --- |
| Durable source | PostgreSQL owns identities, tenant/subject bindings, replay claims, session state, revocation, and required audit. |
| Trust configuration | Typed startup configuration owns assertion issuers, audiences, identity issuers, algorithms, and public keys. Database rows own integration enablement and revocation epochs. Do not duplicate these owners. |
| Authentication owner | The Console validates Clerk. The gateway validates the Console assertion and its durable binding. |
| Tenant scope | Derive organization/team IDs from the registered binding. Browser-supplied IDs and assertion-supplied raw tenant IDs cannot select scope. |
| Authorization owner | Existing backend authorization and managed-asset services remain authoritative. The proxy and UI add no permission grants. |
| Account eligibility | Active ordinary account, one customer workspace, stable external identity, no implicit email linking, no platform-admin access. |
| First provisioning | Account, identity, subject binding, runtime identity, memberships, session, and required success audit commit atomically. |
| Replay | A verified assertion's nonce is durably consumed once across replicas, including when later provisioning fails. |
| Retry | Retry with a fresh signed nonce. Account/membership provisioning converges on the same IDs. Never replay a bearer assertion to recover a response. |
| Revocation | Check authoritative session, parent session, subject, binding, and integration state. TTL is an expiry limit, not the revocation mechanism. |
| Billing | Existing payment and inference controls remain the authority. Sign-in writes none of their fields. |
| Secrets | No assertion or opaque session in URLs, logs, audit payloads, browser storage, metrics, or unencrypted durable storage. |
| Failure | Reject or return unavailable on dependency, verification, or audit failure. Never use master credentials as a customer fallback. |
| Inference overhead | The exchange adds no external identity lookup, session lookup, or new network call to API-key inference requests. |

## 4 Trust configuration and assertion contract

Create typed `ExternalAuthSettings` and `ExternalAuthIntegrationSettings` in a dedicated configuration module. Reference them from `GeneralSettings`; do not grow `src/config.py` with the new implementation. Set `enabled=false` by default and reject unknown fields.

Each configured integration has a stable ID, exact assertion issuer, exact audience, exact external identity issuer, and a bounded set of public verification keys. Start with RS256 and RSA keys of at least 2,048 bits. Reuse the installed `PyJWT[crypto]` and `cryptography` packages. Algorithm selection comes from configuration, never from an unchecked token header.

Initial limits are proposed production defaults. Test them at the deployment's maximum replica count before enablement.

| Setting | Initial value or rule |
| --- | --- |
| Assertion lifetime | At most 60 seconds |
| Clock allowance | At most 10 seconds |
| Body / compact assertion | At most 16 KiB / 8 KiB |
| Integrations / keys per integration | At most 8 / 4 |
| Subject and customer reference | Non-empty, bounded to 200 bytes each |
| Email | Verified, bounded to 320 bytes; use the existing email normalization |
| Nonce | At least 128 random bits, maximum 200 bytes |
| Session lifetime | 300 seconds, bounded by parent expiry |
| Refresh | Start at 240 seconds, with bounded jitter and one renewal in flight per Console parent session |
| Parent absolute lifetime | At most 12 hours from the asserted authentication time; bounded by Clerk session expiry |
| Previous token overlap | At most 30 seconds for renewal; no overlap after explicit revocation |
| Rate limits | 12 exchanges/minute per verified subject, 60 per binding, 1,200 per integration; configurable within tested capacity |

Required exchange claims:

| Claim | Meaning and validation |
| --- | --- |
| `iss`, `aud` | Exact configured Console signer and gateway audience. |
| `sub`, `identity_issuer` | Stable external user ID and exact configured external identity issuer. |
| `iat`, `nbf`, `exp` | Integer UTC times. Require all three; reject future issuance, expired values, excessive age/lifetime, and inverted intervals. |
| `jti` | Fresh random nonce. Store only its approved digest for replay detection. |
| `purpose` | Exactly `gateway_session_exchange` for the exchange route. Other routes require their own purpose. |
| `binding_id`, `external_customer_id` | Select a previously registered binding. Both must match the trusted integration's database row. |
| `email`, `email_verified` | Current verified external email; require a boolean `true`. Email is not the subject key or account-link approval. |
| `external_session_id` | Clerk session ID. The gateway stores a namespaced digest and binds it to this subject and customer. |
| `auth_time`, `external_session_expires_at` | Backend-verified parent authentication time and expiry. A renewal cannot move the original authentication time forward. |

The signed payload has no `role`, `permissions`, gateway account ID, gateway runtime-user ID, raw organization ID, raw team ID, paid status, budget, or billing claims. Reject these fields rather than ignore an attempted privilege or billing grant.

Verification sequence:

1. Enforce body size and the cheap local ingress bound before expensive parsing.
2. Select only a configured integration/key from a bounded header. Unverified `iss` and `kid` may select a candidate; they confer no authority.
3. Reject `none`, HMAC/key-type confusion, unknown `kid`, unsupported critical headers, and token-specified key URLs such as `jku` or `x5u`.
4. Verify signature, issuer, audience, purpose, timestamps, claim types, verified email, and all bounds. Run crypto work through the owned bounded executor.
5. Apply verified-subject/integration rate limits through existing rate-limit infrastructure. Failure of the required distributed limiter returns `503` on external exchange; it does not authorize a local fallback.
6. Check durable integration enablement and consume the nonce before account provisioning.

Public keys are loaded through the existing startup/secret-resolution boundary. No per-request JWKS fetch is added. Startup fails when an enabled integration has invalid or missing trust material. Disabled configuration reports disabled, not ready.

## 5 Database changes

Use append-only Prisma migrations and repositories with typed records. Existing identity, account, membership, and platform-session tables remain the owners of their current data.

| New table | Required fields and durable constraints |
| --- | --- |
| `deltallm_externalauthintegration` | Configured integration ID, enabled state, revocation epoch, timestamps. Create disabled. No key, issuer, or audience copy. |
| `deltallm_externalauthbinding` | Binding ID, integration ID, external customer reference, organization ID, team ID, fixed customer profile version, active/suspended state, epoch, timestamps. Unique `(integration_id, external_customer_id)`. Foreign keys to existing tenant rows. Tenant IDs become immutable after registration. |
| `deltallm_externalauthsubject` | Exact identity issuer/subject, integration and binding association, state, epoch, nullable account/identity/runtime-user references, timestamps. Unique `(identity_issuer, subject)` and unique non-null account, platform-identity, and runtime-user references. An active subject requires all three references. Nullable references allow suspension before first provisioning. |
| `deltallm_externalauthparentsession` | Parent ID, integration ID, digested external session ID, subject reference, fixed authentication time, absolute expiry, revoked timestamp, generation. Unique `(integration_id, external_session_id_hash)`. A revoked row is a tombstone and cannot be reopened by exchange. |
| `deltallm_externalauthassertionuse` | Integration ID, digested nonce, purpose, received time, expiry/retention deadline, bounded outcome. Unique `(integration_id, jti_hash)` across all purposes. Store no JWT or raw session. |

Extend `deltallm_platformsession` with nullable external-parent reference, generation, and the issued security epochs needed to reject obsolete external sessions. Existing rows retain normal gateway behavior. Add indexes for exact session validation and parent revocation.

Reuse `deltallm_platformidentity` with `provider = "external:" + SHA256(exact_identity_issuer)` and the original external subject. This reserves a separate namespace from `google`, `oidc`, and other existing operators. The subject table stores the exact issuer and enforces the matching identity namespace. Issuer changes are explicit identity migrations, not string normalization.

Also require:

- A composite reference/check for the integration and tenant binding association. An integration cannot reference another integration's binding.
- Database enforcement of `team.organization_id = binding.organization_id`. Team moves or organization deletion must not leave an active binding valid. Add a deferred constraint trigger where the existing schema cannot provide a composite foreign key. Follow the current organization lifecycle and deletion services; do not bypass their locks.
- A constraint that an active subject's platform identity belongs to its stored account and its runtime user belongs to its bound team. Updates and deletions must preserve or suspend that association.
- Protection against adding outside memberships or granting platform-admin status to a dedicated active customer account. Apply the shared mutation policy and database constraints. Operators must first suspend external access before any deliberate account migration.
- Indexes on binding, subject, parent expiry, replay retention, and state/expiry cleanup paths. Establish representative-cardinality query plans before release.
- `RESTRICT` or explicit lifecycle handling for active associations. Avoid cascading deletion of account or billing records. When an organization is deactivated, deny access immediately; suspend bindings as part of the existing deletion transaction, then perform bounded cleanup through that lifecycle.

Migration does not scan and relink customers by email, modify tenant records, change asset owners, or backfill operator sessions into external sessions.

## 6 Gateway API contracts

### 6.1 Integration administration

Use the current platform-admin authorization for these gateway management operations. These are separate from customer proxy traffic.

| Endpoint | Request and behavior |
| --- | --- |
| `PUT /ui/api/external-auth/integrations/{integration_id}` | Set enabled state with `expected_version`. ID must exist in trusted startup configuration. Disable increments the revocation epoch. |
| `PUT /ui/api/external-auth/integrations/{integration_id}/bindings/{external_customer_id}` | Register the already provisioned organization/team and fixed profile. Idempotent for the same pair; `409` for a different pair. An existing binding's tenant IDs cannot change here. |
| `GET /ui/api/external-auth/integrations/{integration_id}/bindings` | Bounded cursor pagination and state/customer filters; safe metadata only. |
| `POST /ui/api/external-auth/bindings/{binding_id}/suspend` | Suspend with expected version and reason. Deny all external sessions for the binding. Do not change team billing/block state. |
| `POST /ui/api/external-auth/bindings/{binding_id}/resume` | Explicit admin resume with expected version and reason. Recheck the tenant association; registration retry cannot resume a suspended binding. |
| `POST /ui/api/external-auth/subjects/link` | Admin-approved link with account ID, runtime-user ID, binding ID, fresh purpose-bound assertion, and a bounded approval reference/reason. Never creates a browser session. |
| `POST /ui/api/external-auth/subjects/runtime-user-binding` | Pre-register an existing runtime-user ID for a pending external subject using an admin-approved, fresh `gateway_runtime_identity_bind` assertion. Validate the exact team and absence of another owner mapping. Do not create an account or change the runtime row. |
| `POST /ui/api/external-auth/subjects/{subject_id}/resume` | Explicit admin resume with expected version. Resume never reinstates removed memberships or changes tenant scope. |

The existing authorized Console provisioning path can register the binding after it creates the organization/team. Its management credential stays in that provisioning service. It is never added to customer proxy requests or asset operations. No customer session can invoke these administration routes.

### 6.2 Session exchange

```http
POST /auth/external/exchange
Content-Type: application/json

{"assertion": "<signed Console assertion>"}
```

Return a typed `200` response only to the trusted backend:

```json
{
  "session_token": "<opaque gateway session>",
  "session_generation": 1,
  "account_id": "<gateway account ID>",
  "organization_id": "<bound organization ID>",
  "team_id": "<bound team ID>",
  "inference_user_id": "<bound runtime user ID>",
  "binding_id": "<registered binding ID>",
  "expires_at": "<UTC timestamp>",
  "refresh_after_seconds": 240,
  "next_step": "ready",
  "mfa_required": false
}
```

This route sets no browser cookie and accepts no master key as exchange authority. Add `Cache-Control: no-store`, `Pragma: no-cache`, and safe response/request redaction. Do not expose credentialed CORS access to the exchange response.

| HTTP status | Stable error code and meaning |
| --- | --- |
| `400` / `413` | Invalid typed request / body too large. |
| `401` | `invalid_external_assertion`: invalid signature, issuer, audience, purpose, time, or verified-email proof. Do not return decoder details. |
| `403` | `external_access_denied`: disabled integration, suspended subject/binding, missing current membership, ineligible account, or invalid active tenant association. `external_reauthentication_required` is a separate code for the fixed parent lifetime. |
| `409` | `assertion_replayed`, `account_link_required`, or `identity_binding_conflict`. Account-link errors disclose no existing account ID, role, or tenant. |
| `429` | `external_auth_rate_limited`, with bounded `Retry-After`. |
| `503` | `external_auth_unavailable`: required database, limiter, audit, pool, or execution capacity unavailable. |

Use the current structured error envelope and typed error mapping. No upstream messages, key IDs from arbitrary input, tokens, SQL, or stack details enter the response.

### 6.3 Session and subject revocation

`POST /auth/external/revoke` accepts the same typed assertion wrapper. Its signed `purpose` is `gateway_session_revoke` or `gateway_subject_suspend`; the action cannot come from an unsigned request field. Validate signer, identity issuer, binding, subject, nonce, and time as strictly as exchange.

- Session revoke uses the external session ID and prevents any new child session for that parent. Create a suspended subject shell/parent tombstone when revocation arrives before first exchange.
- Subject suspend denies all parent sessions and future exchanges for that external identity. Do not set the existing gateway account inactive or block the runtime user as an authentication side effect.
- A fresh signed repeat is harmless and returns `204`. Reuse of the same nonce returns `409`. Unknown or foreign IDs do not disclose existence.
- The Console signs only its configured integration's requests. It cannot revoke operators or another integration's subjects.
- Existing `/auth/internal/logout` revokes the external parent when invoked with an external session. It retains its current single-session meaning for operator sessions.

Extend `/auth/me` additively with `session_source`, `workspace`, and `expires_at`. Preserve `auth_mode=session` and all existing operator response meanings. Return only the bound memberships and effective customer capabilities.

For new external accounts, keep the gateway's optional local-MFA enrollment prompt disabled in the Console mount; Clerk remains the normal factor-management surface. Existing gateway MFA on an explicitly linked account must still be verified before any protected operation.

## 7 Provisioning and concurrent exchanges

### 7.1 Durable replay claim

After verification and rate admission, consume `(integration_id, jti_hash)` in a short transaction and write a required, redacted exchange-attempt audit event. A uniqueness conflict rejects replay. Never use only process memory or Redis for this decision.

The claim commits before account provisioning. Therefore a process crash, account-link conflict, or later rollback still consumes the assertion. A later request needs a fresh nonce. Keep replay retention beyond the maximum assertion lifetime and clock allowance; never remove an unexpired claim to reduce load.

### 7.2 Atomic account and session transaction

1. Lock/validate the integration and binding, the active organization and team, and the external subject in one declared lock order. Use a transaction-scoped advisory lock on the canonical issuer/subject when the subject row does not yet exist. Bound lock waits.
2. Resolve the existing subject mapping first. Check account activity, fixed customer association, current memberships, password/MFA state, and any suspension. An existing subject is never rebound through email.
3. For a new subject, test email only for collision. If it belongs to another gateway account, stop with `account_link_required`. Do not call the current email-fallback SSO behavior.
4. Insert the account and existing platform identity, then create the subject association. Use unique constraints to settle competing first logins. A different subject cannot adopt an account produced by a concurrent request.
5. Reuse a runtime user only when an explicit registered mapping proves its ID and team. Validate uniqueness and ownership. If none is mapped, create one with `user_id=account_id`, `internal_user`, the bound team, and the normal new-user defaults. An email collision requires explicit linking; do not move or rewrite the existing row. Do not import billing, budget, or rate settings from the assertion.
6. Insert missing memberships only for first provisioning. Use `ON CONFLICT DO NOTHING`; never overwrite an existing role. An established subject with missing membership receives denial, not repaired membership.
7. Resolve or create the external parent. The same external session ID must retain its subject, binding, original authentication time, and absolute expiry. Reject reassignment, extension of that fixed lifetime, or a revoked tombstone.
8. Lock the parent, advance its generation, and issue an opaque platform session with its bound scope and security epochs. Retain at most the current and previous unexpired generation; previous-generation overlap is bounded to 30 seconds.
9. Persist required success audit through the existing transaction-bound audit repository. Return success only after the transaction commits.

Use one consistent lock order across exchange, link, revoke, membership changes, and tenant lifecycle work: integration, binding, organization/team, subject/account, parent. Existing unrelated mutations must not introduce the inverse order on a touched path.

No Clerk call, HTTP request, email delivery, or Redis invalidation occurs while database locks are held. A fresh assertion after an ambiguous response converges on the same account, memberships, runtime user, and parent. It can replace an unknown child session without needing to persist or recover a raw session token.

### 7.3 Explicit account linking

The platform-admin link service verifies a fresh `gateway_account_link` assertion and the selected tenant binding. It then validates the target gateway account, its exact memberships, runtime user, and owned asset audiences inside a transaction. All must belong to the same customer workspace. Reject platform-admin or conflicting accounts and pre-existing subject/account mappings.

The admin action establishes the subject/account/runtime-user association. It preserves email, local password, MFA, assets, membership roles, budgets, and spend. It issues no session. The Console then exchanges a new assertion through the normal route.

Use an optimistic expected version and the same uniqueness/lock rules as first provisioning. Require an explicit approval reference in audit. Do not add an automatic email-matching upgrade script.

## 8 Session authorization and ownership

Extract `PlatformSessionRepository` and `PlatformSessionService` from the current identity service, with parity tests for operator login, SSO, MFA, logout, and password-reset revocation. The identity service keeps a narrow facade for current callers.

External session validation uses an indexed primary-database read that joins session, account, external parent, subject, tenant binding, integration state, tenant lifecycle, and the exact two memberships. Validate expiry, all revocation states/epochs, parent generation, team association, account eligibility, and the customer permission ceiling before creating `PlatformAuthContext`.

Use typed `ExternalWorkspaceContext`. Pass it into `AuthScope` and shared permission checks. Every relevant helper must honor it, including `get_auth_scope`, `has_scoped_permission`, `require_authenticated`, `require_master_key`, and `require_any_admin_permission`. No helper can promote an external session through a supplied master cookie, bearer header, account role, or unfiltered membership.

Mixed privileged credentials on an external customer request are rejected. The Console strips them before forwarding. Explicit operator login endpoints can still change auth mode and clear old cookies; their existing behavior remains available outside the customer proxy.

| Operation | Customer rule |
| --- | --- |
| Credential/model create | `governance_source=creator`, owner and creator equal the verified gateway account. Existing secret encryption/write-only behavior applies. |
| Asset read/edit/delete | Apply the existing owner or explicit reader/editor grant. Missing or foreign private assets return the current non-enumerating denial. |
| Audience change | Bound organization/team only; no public grant. Preserve model/credential audience coverage and owner-delegated credential use. |
| API-key create | Owner account and mapped runtime user come from the session association. Team is the bound team. Apply live self-service key ceilings, counts, expiry, and allowed models. |
| API-key list/view/revoke/delete | Own account and bound team only. Reuse the existing owner checks. Do not broaden `KEY_UPDATE` or `KEY_REVOKE` to the full team. |
| API-key policy editing | Deny changes to money, spend, owner, runtime user, team, privilege, or scope. Unrequested rename/rotation workflows are outside this release. |
| Tenant and people administration | No team budget edit, tenant mutation, membership grant, operator promotion, or integration administration. |
| Inference | Continue API-key authentication with `owner_account_id`, bound `user_id`, team, and organization. Gateway sessions do not become inference bearer keys. |
| Other creator tools | Existing policy applies within the same fixed workspace; the session grants no new platform capability. |

A customer-owned key must keep the same account owner used by managed-asset inference authorization. Test the actual private model call, allowed sharing, disallowed sharing, and credential-binding mode. A correctly scoped UI response is insufficient proof.

For key revoke/delete, delete the key and enqueue exact durable invalidation in the same transaction. Use the existing invalidation worker and required mutation audit; do not keep the current log-and-continue exception path. Add a typed, versioned revoked marker at the existing shared auth-cache key. Change the existing cache fill to an atomic fill-if-absent operation so a pre-delete reader cannot overwrite that marker. Apply this to both `validate_key` and `get_auth_by_token_hash`; no second revocation cache or per-key discovery loop is needed.

Reuse the outbox's existing `key_hash` scope with a specific revoke/delete reason. For that reason, the existing worker installs the denied marker instead of deleting the cache entry. Other invalidation operations must preserve an unexpired denied marker. Retain completed revoke/delete records for the supported rollback window so operations can reconcile exact hashes in both cache namespaces without a wildcard scan.

An acknowledged marker denies new requests on all replicas that use the shared cache. If marker delivery is pending, return the committed revoke result with explicit pending enforcement metadata; the UI must not present immediate enforcement. For the enabled production profile, bound API-key cache TTL to at most 60 seconds and durable auth-load/fill duration to at most one second. This gives a declared maximum stale authorization window of 61 seconds during delivery failure. Keep the marker for at least that window, and durably retry its delivery. Requests already admitted can finish under the existing inference lifecycle. Do not change the default TTL for unrelated deployments; reject an external-auth production profile that does not meet these bounds.

Measure the cache protocol change. It uses the current lookup and fill call positions, with no added successful inference round trip. When Redis is unavailable, use the bounded authoritative key-auth fallback with no unsafe local positive cache. When an atomic fill observes conflicting cache state, deny or resolve it within a bounded failure path; never return the stale database snapshot as newly authorized. A mixed-version key-service rollout requires the versioned cache namespace and all-replica rollout gate described in section 16.

## 9 Console integration and UI mount

The Console repository is a separate delivery surface. Its paths must be identified before its implementation slice begins. Gateway completion does not close the issue until the deployed Console path passes the full acceptance flow.

### 9.1 Server request flow

```text
Browser with Clerk session and opaque Console proxy reference
  -> same-origin Console proxy
     -> verify Clerk authentication and current customer association
     -> find encrypted server-held gateway session
     -> if renewal is needed, sign a fresh assertion
        -> gateway external exchange
        <- scoped opaque session and identity IDs
     -> forward allowed gateway request with that session cookie
  <- safe gateway response through Console origin
```

The Console backend must:

- Verify Clerk using its supported SDK, configured authorized parties, and instance trust. At exchange/renewal, resolve verified email and current parent state/expiry from trusted backend data. A valid local JWT alone does not prove that a remote session is still active. Use the verified Clerk event path for prompt suspension and the short session lifetime for the declared delivery-outage bound. Never source the customer, subject, or gateway IDs from browser JSON or mutable browser metadata.
- Register the binding as the last step of its separate authorized tenant provisioning flow. An unresolved or conflicting binding blocks gateway entry and produces an actionable support state.
- Use a random `Secure`, `HttpOnly`, `SameSite=Lax`, host-only reference cookie for the proxy context. Store gateway sessions in an encrypted shared server store, keyed by Console/Clerk session and binding. No process-local-only vault in a multi-replica deployment.
- Ensure the reference belongs to the currently verified Clerk subject, external session, and customer. A copied reference cannot switch identity. Reject pending/incomplete Clerk sessions and unverified emails.
- Use distributed single-flight renewal and conditional generation updates in its existing server store. A late response cannot replace a newer session. Maintain the documented maximum of two gateway generations.
- At the fixed parent lifetime, direct the customer to Clerk reauthentication and obtain a new external session. Do not loop on failed renewal or show a gateway password login. A fresh assertion for the old external session cannot extend that lifetime.
- Forward only an allowlist of path/method templates and selected headers. Strip browser `Authorization`, `X-Master-Key`, all gateway session/master cookies, tenant-selection headers, and untrusted forwarding headers. Set only the server-owned customer session. Gateway authorization still checks every request.
- Validate Origin/Referer or a CSRF token for cookie-authenticated mutations. Use an exact CORS/host policy. Preserve the real browser Origin through trusted proxy metadata; do not rewrite an untrusted origin into an allowed one.
- Bound body size, streaming duration, upstream timeouts, connections, and retries. Never retry a mutating gateway API merely because exchange or forwarding timed out.
- Strip gateway authentication `Set-Cookie` and all session-token fields from browser responses. Normal API-key creation still returns the customer's new raw key once through the existing approved UI flow.
- Revoke the gateway parent on logout, then clear the vault/reference. If delivery fails, durably enqueue a signed fresh-nonce revocation using the Console's existing queue/outbox. Clerk revocation/disable events use the same verified event path and deduplication.

After a committed gateway revocation, the next session validation denies access on every replica. Before the Console delivers that revocation, its proxy denies signed-out users immediately. A copied gateway session has at most its remaining 300-second lifetime. This is the declared outage bound; do not claim instant remote Clerk revocation from local JWT verification alone.

Keep raw API keys independent from browser logout. A product action to suspend inference must call the existing authorized key/team/user controls explicitly, outside identity exchange.

### 9.2 Playground inference transport

Control-plane session cookies do not authenticate `/v1` requests. Implement a separate own-key selection flow for the Console Playground:

1. The browser submits its own API key through a protected Console selection route. The key stays in transient UI state or the encrypted server vault; never browser persistence or a URL.
2. The Console validates Clerk and invokes `POST /auth/external/inference-key` with the bound gateway session and a typed `api_key` body. This control-plane endpoint uses the existing key service, rejects master/service-account credentials, and requires exact owner account, runtime user, and bound team. It returns safe key metadata only.
3. The Console stores that validated key encrypted and returns a random selection reference. Bind the reference to the same Clerk session/customer; expire it no later than the selected key or proxy context.
4. A Playground inference request selects that server-owned reference. The Console forwards the stored key as the normal bearer credential, strips browser authorization, and omits gateway session/master cookies. The gateway performs its existing model, tenant, budget, block, rate, and key-revocation checks.

The selection endpoint is not a new inference credential or a key grant. It performs the ownership check before forwarding begins. Test wrong-owner keys, revoked/expired keys, injected master credentials, unsupported methods, streaming cancellation, and logout during inference. Existing requests accepted before logout retain their bounded cleanup/accounting lifecycle.

### 9.3 Functional gateway UI

Mount the gateway UI at Console `/gateway/`. Add a typed, same-origin runtime mount configuration shared by BrowserRouter, asset URLs, API transport, redirects, and the auth controller. The Console maps `/gateway/ui/api/*` and `/gateway/auth/*` to gateway paths. Prefer configurable Vite base and an explicit router basename over HTML rewriting.

Default mount values preserve the current self-hosted UI paths and behavior. Validate mount values as same-origin paths; no arbitrary runtime upstream URL. Keep one owner for static serving when the touched route boundary is consolidated into `src/ui/routes.py`.

Extract typed auth contracts/API into `ui/src/lib/api/auth.ts` from the oversized barrel. Add `session_source=external`, bound workspace, expiry, and capabilities. Keep the existing `session` auth mode. In the Console mount:

- Establish/renew the backend proxy session before rendering protected gateway routes.
- Use server capabilities for navigation and actions. No master-key or local-password login fallback appears in the customer flow.
- Show specific states for linking required, verified-email required, missing/suspended workspace, unavailable service, and MFA required on a linked account. Reuse the existing shared auth shell and error components.
- Send logout through the Console lifecycle and clear protected UI state. Key UI caches by principal, source, and binding. Reject stale responses after logout, account switch, or renewal.
- Register all new UI unit files in the current runner. Verify deep-link refresh, lazy chunks, redirects, mobile layout, and keyboard behavior.

## 10 Audit and observability

Add fixed audit actions for exchange attempt/success/denial, first subject provisioning, explicit link, integration/binding suspend/resume, parent revoke, and subject suspend. The audited actor distinguishes the trusted integration from the affected gateway account. Include safe binding/account IDs, purpose, fixed reason code, correlation ID, and approved link reference.

Pass typed audit inputs to the existing audit service and transaction-bound repository. Required successful mutations and their audit commit together. Enabled external auth requires the audit dependency; it cannot silently skip auditing when the service is absent. Authentication assertions, raw sessions, external session IDs, email payloads, keys, and secrets are not audit content.

For verification failures, record a bounded sanitized denial event through the existing required audit path after ingress/rate admission. Do not let unauthenticated oversized requests fill durable audit storage. Body-limit and edge-overload rejections use bounded counters; all admitted verification failures remain denied even if audit is unavailable.

Add bounded metrics for exchange duration/outcome, verification failures, replay rejects, provisioning/link conflicts, pool/crypto saturation, renewal, revocation delay, and cleanup backlog/oldest age. Labels use fixed route, purpose, outcome, and failure enums. No subject, email, nonce, customer, binding, account, session, token, or raw URL labels.

Readiness verifies enabled trust material, schema/protocol compatibility, control-plane database capacity, and required audit. Authenticated diagnostics show protocol version, disabled/degraded/ready state, cleanup health, and safe counters. Public liveness remains coarse.

## 11 Capacity and failure behavior

Use a centrally owned external-auth control-plane PostgreSQL pool with four connections per API process: two for exchange/link/revoke work, one reserved for session/selection validation, and one for bounded maintenance. Allow at most eight queued mutation requests and eight queued validation requests with short waits. Use two owned crypto workers and a bounded submission queue. Maintenance cannot consume reserved validation capacity. Adjust the allocations only with new capacity/load evidence.

No new connections are created per request. The pool and executor close through the current lifespan. Processes that do not serve external control-plane requests must not create the pool. Avoid adding external session lookups to bearer-only inference or static/public requests.

| Budget | Initial release target |
| --- | --- |
| Exchange end-to-end deadline | 1,000 ms; target p95 at most 500 ms under the certified profile |
| Pool acquisition / lock wait | At most 50 ms / 100 ms, subordinate to the request deadline |
| Statement / provisioning transaction | At most 250 ms per statement / 750 ms total; replay claim has its own smaller bounded transaction |
| External session validation | Two statements: one transaction-local UTC/deadline setup and one indexed joined read; no last-seen write; target p95 at most 50 ms |
| Exchange query budget | At most 4 statements for replay claim/audit, then at most 16 for first provisioning or 10 for a repeat exchange, including required audit; measure and ratchet the final counts |
| API-key inference | Zero added Clerk calls, external-auth SQL, JWKS calls, or new Redis calls from this feature |

The chart currently permits 12 API replicas. The deployment calculation must also include surge pods and processes per pod. For example, one process per pod and 25% surge gives 15 API processes and `15 * 4 = 60` additional PostgreSQL connections. This is an incremental example, not approval of the full deployment budget. Sum existing main, telemetry, workers, maintenance, and migrations; compare with the owned database limit and headroom. Check actual rollout settings and enforce the result in chart/profile validation before enablement.

| Failure | Required behavior |
| --- | --- |
| Database unavailable / capacity exhausted | `503`; no session, account, or local durable fallback. |
| Transaction error / cancellation | Roll back account/session changes; release acquired resources; replay claim remains consumed. Retry with a fresh nonce. |
| Redis unavailable | Distributed exchange rate limiting fails closed. Already issued external session validation uses PostgreSQL and does not require a new Redis auth cache. Existing operator behavior retains its current policy. |
| Audit unavailable / required backlog full | No successful mutation or session issuance without its required durable audit. Return unavailable after rollback. |
| Unknown signing key | Reject; no token-directed key fetch. |
| Invalid active binding or membership | Deny; no automatic repair or tenant reassignment. |
| Console vault unavailable | Deny proxy access and show retryable error. Never forward with a master credential. |
| Response lost after commit | Fresh assertion resolves the same resources and advances the parent generation; old nonce remains rejected. |
| Cleanup delayed | Alert on age/cardinality; preserve replay/revocation records needed for safety. Use backpressure rather than evict security state. |

Replay claims retain at least 15 minutes. Parent revocation tombstones retain at least 30 days and beyond their fixed authentication lifetime. Expired external child sessions retain seven days of safe metadata, subject to the existing audit retention policy. Subject/binding mappings remain durable until an explicit lifecycle operation.

Add bounded cleanup through the existing auth bootstrap task ownership and PostgreSQL claim/lease pattern. Do not add cron or an unrelated worker deployment. Use indexed expiry, `SKIP LOCKED`, bounded batches, cancellation, and measured rate. Size cleanup to exceed accepted assertion/session insertion at the configured maximum, including replica count; a nominal interval alone is not a retention guarantee. Include write/index and vacuum costs in the capacity note.

## 12 Configuration and documentation surfaces

Update all applicable settings surfaces in the same slice:

- `src/config.py`, new external-auth configuration models, `src/config_runtime/`, and bootstrap settings resolution.
- `config.example.yaml` and `.env.example` with one canonical feature configuration and key-reference guidance. Do not add conflicting environment aliases.
- Helm base/eval/production values, `values.schema.json`, secret mounts/references, and production connection-budget validation.
- API request/response models and corresponding TypeScript contracts. Keep account/tenant IDs additive and optional on existing `/auth/me` responses.
- `docs/features/authentication.md`, `docs/guides/admin-authentication.md`, `docs/configuration/external-customer-auth.md`, and `docs/guides/console-gateway-connection.md`, linked from `mkdocs.yml`. Regenerate OpenAPI and the general settings index.
- Key and managed-asset guides for the exact customer permission limits, separate inference keys, and billing boundary.

Trust metadata, public keys, pool/executor limits, and protocol mode are startup-only. Durable enablement, suspension, and security epochs support immediate operator revocation without restarting replicas. Document key rotation as an all-replica public-key rollout, followed by a Console signing-key switch, then retirement of the old public key after the assertion overlap window. On compromise, disable the integration and revoke its sessions before rotating/re-enabling it.

Add explicit Console origin allowlists, trusted direct-proxy CIDRs, and production cookie settings through the existing security configuration boundary. Use one trusted-forwarding resolver for auth, audit, and rate-limit context on the touched paths. Do not copy the current vendor-based production check or trust arbitrary `X-Forwarded-*` values. Proxy reference cookies always use the explicit production policy. Test forwarded header spoofing, direct HTTP, host mismatch, and CSRF failure.

The new guide must include complete exchange/revoke examples with fake values, identity/tenant administration, existing-account linking, session IDs/lifetimes, same-origin proxy behavior, Playground key selection, CSRF, failure codes, key rotation, suspension, cleanup/retention, and rollback. State the 300-second remote session-revocation outage bound, 61-second pending key-revocation bound, and the independence of inference keys. Pricing and checkout remain in the Console documentation. Changes to a configured issuer or audience require disablement/revocation and an explicit trust migration; normal public-key rotation does not change identity mapping.

## 13 Implementation slices and exit gates

All slices ship behind disabled external authentication until the final production gate passes. Each slice includes its own tests and can be reviewed independently.

| Slice | Implementation and main files | Exit gate |
| --- | --- | --- |
| 1 Contracts and parity seams | Check in the [design decision](issue-344-external-auth-design-decision.md); create typed settings/DTOs; extract session persistence/service and strict account-resolution policy; extract only the needed runtime-user and key mutation seams. | Existing operator SSO/login/MFA/key tests pass; current public contracts stay compatible; size/dependency ratchets cover the extraction. |
| 2 Durable state | Prisma schema/migration; `src/db/identity/external/external_auth_integrations.py`, `external_auth_bindings.py`, `external_auth_subjects.py`, `external_auth_sessions.py`, `external_auth_assertions.py`; migration fixtures and constraints. | Fresh and last-supported-release upgrade pass; race, tombstone, tenant, and mapping constraints pass against real PostgreSQL. |
| 3 Assertion trust and runtime | `src/auth/external_assertions.py`, typed config module, `src/bootstrap/external_auth.py`, infrastructure pool/executor, rate-limit integration, maintenance lifecycle. | Fixed-algorithm/purpose/time/claim tests, overload/failure tests, key-rotation fixtures, startup/readiness and shutdown tests pass. |
| 4 Exchange and administration | `src/services/external_auth_exchange.py`, `external_auth_linking.py`, `external_auth_revocation.py`; thin auth/admin endpoints; route registration; required audit actions. | Verified first/repeat exchange, explicit link, replay claim, audit rollback, suspension-before-login, and concurrent requests pass. No billing writes. |
| 5 Scope and asset/key safety | Extracted session service, platform/admin auth helpers, `AuthScope`, managed-asset policy, key ownership and durable invalidation/marker protocol, tenant lifecycle integration, own-key selection endpoint. | Every denial/ownership/MFA test passes; private/shared inference works with customer keys; no permission escalation or operator regression. |
| 6 Console proxy and gateway UI | Console signer, tenant registration, encrypted vault, renewal/logout queue, proxy allowlist/CSRF, Playground forwarding; gateway runtime mount, auth contracts/controller, transport, route redirects. | Browser E2E completes new verified-customer entry with no gateway login; deep links, Playground, and logout work; no gateway session secret reaches the browser. |
| 7 Production verification and release | Docs, Helm/schema and capacity arithmetic, retained-state cleanup, load measurements, migration/rollback rehearsal, operations guide. | All five dependency lanes, UI gates, complete acceptance suite, production image/migration smoke, and rollout/rollback evidence pass. |

Implement the new lower layers with typed dependencies, not `Request` objects or deep `app.state` access. New backend modules should remain below 500 logical lines and functions below 80. Do not copy the legacy JWT handler, SSO state store, cookie heuristics, broad exception handlers, or key invalidation failure behavior into a new external path.

The design decision in slice 1 records ownership, the signed-backend and OIDC/direct-token alternatives, dedicated-account limitations, durable replay choice, capacity cost, failure behavior, and rollback. The new feature has one implementation owner under the existing auth lifecycle.

## 14 Verification matrix

### 14.1 Deterministic service and route tests

- Valid signature and verified customer; wrong issuer/audience/purpose/key/type; invalid/missing claims; false/string verified-email value; expired/future/too-long lifetime; missing/weak nonce; oversized body; token-directed key URL; forbidden tenant/role/billing claims.
- Same issuer/subject returns stable account and runtime IDs; same email with another subject/issuer never links; verified email changes do not reassign identity or rewrite existing account data.
- Registered binding accepted; unregistered/foreign binding, customer mismatch, wrong team organization, inactive organization, moved/deleted team, and suspended integration/subject/binding denied.
- First provisioning seeds only missing memberships. Repeat exchange preserves roles, budgets, spend, key policy, blocking, and removed memberships.
- Explicit link requires platform admin and signed external proof; rejects privileged/cross-workspace accounts and conflicting runtime identities; preserves passwords, MFA, asset owners, and economic state.
- External session checks exact scope and permission ceiling across all shared helpers. Mixed master cookies/headers, later admin promotion, outside membership, runtime reassignment, and caller-supplied IDs cannot broaden access.
- Existing internal auth, ordinary SSO email policy, emergency master login, MFA, invitation/reset recovery, and operator auth mode transitions keep their current behavior.
- Dependency unavailability, deadline, cancellation, crypto/DB overload, and audit failure never yield a successful session or partial provisioning.

### 14.2 Real PostgreSQL tests

- Two replicas consume one nonce: exactly one accepted claim; every other caller gets replay denial.
- Concurrent fresh assertions for one subject create one account, platform identity, runtime user, and membership pair. Bound parent generations prevent unbounded sessions.
- Concurrent different subjects with the same email cannot adopt each other's account.
- Provisioning/link/audit failure rolls back all identity/session writes; replay claim remains consumed.
- Account disable, membership deletion, integration/binding/subject suspension, and parent revoke racing with exchange never resurrect revoked access.
- Revocation before first exchange creates a tombstone that blocks later issuance.
- Lost response and Console generation races recover with a fresh nonce; no stale session response overwrites a newer generation.
- Real foreign keys/checks/deferred triggers reject invalid account/identity/runtime/tenant mappings and unsafe team moves/deletions.
- Fresh database, last-supported-release upgrade, retained operator rows, failed-upgrade recovery, and old-version rollback preparation pass. Extend the current migration-path fixture/script.
- Representative-cardinality query plans meet the session, replay, provisioning, and cleanup budgets.

### 14.3 Real Redis and invalidation tests

- Distributed subject/integration rate limits hold across clients, with TTL, outage, recovery, and `NOSCRIPT` behavior as applicable.
- Key revoke/delete commits its invalidation outbox record atomically. A restarted worker delivers it after an outage.
- A key cached on another replica is denied within the declared bound. Test the actual inference path during both successful invalidation and outage, plus a delayed pre-delete cache fill, marker expiry, conflicting fill, Redis restore, and mixed-version cache isolation.
- Existing managed-asset policy invalidation continues to remove revoked sharing across replicas. Retain the existing Redis outage policy.

### 14.4 Browser and Console acceptance tests

1. Register a new verified Clerk customer with the existing billing-managed organization/team. Enter `/gateway/` without a gateway login. Assert no second workspace.
2. Create a private credential and model. Database owner/creator IDs match the customer account. Secrets are redacted after creation.
3. Create a permitted own API key, then call the private model directly and through the Console Playground with it. Check account owner, runtime user, tenant, usage, and spend attribution.
4. Share within the customer team/organization. Another authorized member succeeds; another customer cannot enumerate, use, edit, share, or delete the private asset or key.
5. Attempt public/cross-tenant sharing, owner/key reassignment, admin endpoints, team budget changes, and disabled-key-policy bypass. All are denied by the gateway.
6. Snapshot blocked flags, payment metadata, budgets, spend/exact spend, reservations, reset times, rate limits, tiers, and pricing before first/repeat/failed exchange and renewal. Assert unchanged values. A blocked team remains unable to infer after sign-in.
7. Test pending/unverified Clerk identity, linking-required state, suspension, MFA on an eligible linked account, session expiry, concurrent tabs, principal switch, proxy vault outage, invalid Origin, and injected credentials.
8. Test Clerk logout/revoke delivery, gateway parent tombstone, delayed delivery bound, proxy state cleanup, and continued explicit API-key semantics.
9. Inspect browser requests/responses/storage, proxy logs, gateway logs, audit, errors, and metrics. No assertion, gateway session, master credential, or provider secret is present.
10. Verify `/gateway/` assets, nested refresh, authentication redirects, lazy chunks, authenticated 404, mobile widths below/above 768 px, keyboard focus, and unchanged self-hosted paths.

### 14.5 Production and load tests

- Two or more API replicas, peak configured replicas plus surge, concurrent exchange/renewal, deployment interruption, and repeated migrations.
- Constant-arrival bearer inference using `scripts/measure_gateway_load.py` with the existing local provider mock, before/after the feature. Record SQL/Redis/network counts, offered/received rates, queue slope, in-flight work, and p50/p95/p99. Repeat with concurrent bounded sign-in traffic to prove capacity separation.
- A separate external-auth profile checks first provisioning, renewal, session validation, key verification, cleanup backlog, and crypto/DB saturation against the declared budgets.
- The repository's proposed 50 RPS inference certificate remains a direction until its canonical certificate is enforced. This change must meet the existing measured ratchet; do not report the proposed certificate as an established guarantee.
- Production image startup, exact schema/protocol readiness, secret references, non-root operation, bounded drain, pool/executor cleanup, and rollback token revocation.

## 15 Required verification commands

Run focused tests for each slice, then the shared gates. Use disposable PostgreSQL and Redis services, never production instances. New real-service tests carry the correct primary dependency markers.

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest --collect-only -qq --dependency-lane-report
uv run pytest -q -m hermetic
uv run pytest -q -m app
uv run pytest -q -m postgres
uv run pytest -q -m redis
uv run pytest -q -m helm
uv run python scripts/verify_migration_paths.py
npm --prefix ui ci
npm --prefix ui run test:unit
npm --prefix ui run build
npm --prefix ui run lint
uv lock --check
git diff --check
```

The migration command requires `MIGRATION_TEST_ADMIN_DATABASE_URL` or its documented argument and the resolved last-supported-release base ref. Set `DATABASE_URL`, `REDIS_URL`, and `DELTALLM_TEST_REDIS_URL` for their disposable services. Use the existing chart test setup to build dependencies and lint/template base, eval, and production profiles.

Require zero Ruff/ESLint errors in touched files and record pre-existing full-suite findings without hiding them. Run exact touched-file lint before broad lint. Register UI tests in the current runner. Record production bundle sizes against the current baseline; do not grow the initial chunk. Add fresh/upgrade schema assertions and keep the existing CI aggregate coverage of all five lanes.

Console signer/vault/proxy and browser E2E commands must be added to the Console repository's existing test/CI system in slice 6. Identify that system before coding; do not claim gateway tests cover the external deployment.

## 16 Rollout and rollback

### 16.1 Enablement sequence

1. Capture a restorable database backup and rehearse the additive migration and rollback preparation in staging.
2. Run one coordinated migration job before application rollout. Do not let API replicas race DDL.
3. Deploy all gateway replicas with external auth disabled. Confirm the external-auth protocol/schema version through authenticated diagnostics on every active and surge replica.
4. Load Console public keys and register the integration disabled. Register only approved existing tenant bindings. Do not auto-link existing customer records by email.
5. Deploy the Console signer, vault, same-origin UI mount, and verified logout/revoke delivery. Keep customer gateway entry disabled.
6. Enable the integration for dedicated staging/canary customers. Complete the entire acceptance flow with a blocked team and with authorized inference.
7. Confirm capacity, required audit, revocation bounds, retention, and secret redaction. Enable customer cohorts through registered active bindings; monitor denial reasons, latency, renewal failures, and cleanup.
8. Enable all approved customers only after the release gates pass. Publish the support/linking and incident runbooks.

Never issue external sessions while an old gateway binary can serve requests. Older code can read the ordinary platform-session row while ignoring its new external scope columns.

The key-cache marker protocol also needs a compatible rollout on every replica. The `KeyAuthCache` owner selects the versioned `key:v5:` namespace in the new binary; legacy binaries use `key:v4:`. Deploy every reader/filler before enabling external customer key operations. The enabled feature requires explicit `external_customer_v1` startup acknowledgement. Retain exact-hash revocation work for rollback reconciliation. Do not allow a legacy unconditional fill to overwrite the new protocol's markers.

### 16.2 Rollback procedure

1. Disable Console gateway entry and renewal.
2. Disable affected integration/bindings, then durably set `revoked_at` on every corresponding external platform-session row and revoke parent sessions, with required audit.
3. Verify those rows are denied on every replica using the old-compatible `revoked_at` check. For every key deleted under the new protocol, retain its durable revocation work and confirm both current and rollback auth-cache namespaces are absent/denied after the bounded maximum fill interval. Coordinate the cache namespace through the existing owner; do not use wildcard deletion. Only then roll back gateway binaries.
4. Remove encrypted Console vault tokens/reference cookies. Keep revocation tombstones, identity mappings, and the additive schema.
5. Keep operator authentication available. Leave accounts, memberships, assets, keys, economic records, and pricing unchanged. Inference restrictions continue through their existing controls.
6. Re-enable only after a new all-replica compatible rollout and fresh signed exchanges. A previous child token cannot become valid again.

No down migration deletes customer identity or audit data. Incident response for signer compromise uses integration disablement and old-compatible platform-session revocation before key rotation.

## 17 Acceptance traceability and delivery ledger

Gateway implementation is complete in this worktree. Console implementation and deployment qualification are owned by the Replit Console team and deployment operator. “Local pass” below means a repository test or disposable-service rehearsal, never approval to enable production.

| Slice | Implementation status | Evidence / remaining release gate |
| --- | --- | --- |
| 1 Contracts and parity seams | Gateway complete | Session/identity facades, typed DTOs, strict policy; operator regression lanes and size/dependency checks pass. |
| 2 Durable state | Gateway complete | Seven additive migrations; real constraints/races; fresh, v0.1.49, shared-feature and migration-recovery rehearsal pass. |
| 3 Assertion trust and runtime | Gateway complete | Verification matrix, distributed admission, reserved pool/crypto capacity, readiness, owned cleanup/shutdown; real runtime checks pass. |
| 4 Exchange and administration | Gateway complete | Atomic provisioning/link/replay/audit, tombstones, versioned suspend/resume, private diagnostics; SQL query budgets pass. |
| 5 Scope and asset/key safety | Gateway complete | Customer ownership/audience/key denials, economics snapshots, durable exact-hash invalidation and real Redis tests pass. |
| 6 Console proxy and gateway UI | Gateway UI complete; Console external | Mounted UI, strict customer session source, stale-principal/logout tests, lazy Playground and vault endpoint contract pass locally. Replit owns signer, shared encrypted vault, proxy, renewal, logout and browser E2E. |
| 7 Production verification and release | Local gateway checks complete; deployment qualification external | Five lanes exercised, UI no-new-lint ratchet/build, strict docs/chart, non-root image, cardinality and rollback evidence recorded. Multi-replica concurrent sign-in/inference, peak-plus-surge load, deployment interruption and connected staging journey remain required. |

| Issue acceptance criterion | Local gateway evidence | External deployment evidence |
| --- | --- | --- |
| Verified new customer enters without second sign-in | First/repeat exchange; strict mounted UI auth contracts | Replit Clerk→vault→proxy browser journey |
| Private credential/model belong to customer | Real customer asset ownership/redaction and private/shared inference checks | Create/use assets through deployed Console |
| Sharing and keys need no platform admin | Workspace audience limits, own-key route and policy checks | Two-customer UI and inference journey |
| Other customers cannot access workspace/assets | Cross-tenant/grant/key/principal denials | Deployed proxy/vault isolation |
| Invalid/expired/replayed/wrong-target assertion rejected | Signature/claim matrix, real durable nonce races | Console retry/log-redaction capture |
| Repeated/concurrent exchanges converge | Real PostgreSQL identity/session races and required-audit rollback | Concurrent Console replicas/tabs and lost responses |
| Operator authentication continues to work | Full operator application/database regressions | Operator canary during rollout/rollback |
| Billing enforcement unchanged | First/repeat/failure/renewal economic snapshots and blocked-team inference | Existing billing-managed customer snapshots |
| Auth/linking audited without secrets | Required transactional audit and bounded sanitized denial checks | Deployment HTTP/tracing/log inspection |

- [x] Gateway public and Console connection contracts documented together.
- [x] Additive migration, fresh installation, last-supported-release upgrade, and constraints verified locally.
- [x] Gateway trust, linking, concurrency, revocation, scope, and denial checks pass.
- [x] Customer asset and API-key account/runtime ownership tested locally.
- [x] Durable key invalidation, stale-fill protection, outage recovery and denial bound tested locally.
- [x] Auth operations preserve economic state and existing restrictions in gateway tests.
- [x] Five dependency lanes exercised; UI build/unit and no-new-lint checks, chart and documentation gates pass. Record skips and existing lint debt explicitly.
- [x] Pool/surge arithmetic enforced; cardinality, nominal cleanup throughput, image and short local load evidence recorded.
- [x] Old-compatible child revocation and exact old/new cache reconciliation rehearsed with the baseline session reader.
- [ ] Console agent implements and tests signer, encrypted shared vault, proxy, renewal, key selection, Clerk events, logout, and redaction in Replit.
- [ ] Joint staging tests prove browser secret handling, races, billing boundary and the complete new-customer journey.
- [ ] Operator qualifies multi-replica concurrent sign-in/inference and peak API/worker replicas plus surge, sustained cleanup/audit throughput, headroom, deployment interruption and all-replica canary.
- [ ] Enablement is approved only after those deployment gates; issue 344 remains open until the connected journey passes.

## 17.1 Recorded implementation choices and local verification limits

- External child tokens use `psk_ext1_` so mixed privileged credentials can be denied even when a customer cookie has expired. Hashing retains the existing session-salt format. No inference handler accepts these tokens as API keys.
- External session validation performs exactly two statements, including transaction-local UTC/deadline setup. The second is the indexed authorization join; validation performs no write. Real tests enforce this budget.
- Historical binding and subject IDs remain immutable after tenant/identity deletion. Five historical foreign keys were replaced by active-state deferred checks and deletion-retirement triggers; session/parent relations and uniqueness constraints remain. This permits existing deletion workflows while preserving revocation and historical associations. Ordinary SSO membership edits keep their previous lock behavior; only active external mappings take the additional account lock.
- Required invalid-signature denial audit is bounded globally to 60 records/minute; unauthenticated oversized/overload traffic produces counters. Successful operations and replay consumption retain required durable audit.
- New key readers/fillers use atomic Redis lookup/fill in `key:v5:` with Redis server time and a one-second fill deadline. The number of Redis calls is unchanged at the touched lookup/fill positions. SQL fallback is bounded and creates no local cache. Static owner checks and real Redis tests cover the protocol; the short load run does not independently certify request-by-request SQL/network counts.
- The rehearsed production image runs as UID/GID 10001. Its digest and acceptance summary are in the evidence artifact. That image predates the final cache-degradation correction. A build and smoke test of the final production image remain required; the supplied CI patch adds that gate.
- The short canonical 50-RPS comparison used one API replica and 500 requests per mode; every request succeeded and neither generator dropped work. Baseline startup overlapped the short baseline window. These numbers are smoke evidence, not evidence of a speed improvement or production capacity.
- PostgreSQL's final complete lane ran with Redis unset while the shared qualification VM was reserved by another task; its Redis-dependent cases skipped. The feature's combined database/Redis runtime cases and the full Redis lane passed separately before the task stopped its containers. No skipped case is reported as passed.
- UI lint retains 116 baseline errors with zero new normalized file/rule/message findings. The compatibility export preserves the pre-existing auth refresh finding. Full backend Ruff checks and touched-file formatting pass. Full-tree formatting retains 226 existing findings versus 231 at baseline. Live browser inspection was unavailable because the app could not verify its enforced browser security policy; UI unit/build and HTTP mount checks pass.

## 17.2 Connected staging inputs

The public handoff uses example origin `https://console-staging.example.com`, with customer signup at `/sign-up`, and placeholder team `team-zero-budget-test` under placeholder organization `org-zero-budget-test`. The real values remain in the private local handoff. Keep the actual test team's economic state unchanged. Use it for budget-denial checks before and after renewal. A second customer needs a separate workspace with permitted test inference for the successful path and isolation checks.

The Console connection and confirmation of both verified Clerk identities are pending. The public handoff uses example gateway URL `https://gateway-staging.example.com`; the actual URLs and tenant IDs are recorded only in the ignored local staging handoff. Credential-free checks confirmed HTTP 405 with `Allow: GET` for an empty exchange POST, HTTP 404 for the corresponding GET, and no `/auth/external/*` paths in the deployed OpenAPI. The current endpoint does not serve the new interface. The deployment operator must deploy the implementation or correct upstream routing. The exact Clerk issuer, installed signing key ID/public key, trusted customer reference, returned binding ID, second workspace, and test access are not yet confirmed. No connected staging test is recorded as passed.

The [Replit Console staging handoff](issue-344-console-staging-handoff.md) contains the finalized exchange interface, example staging trust values, registration body template, Console requirements, and remaining joint checks. Proposed integration ID `console-staging`, audience `deltallm-console-staging`, and signer issuer equal to the Console origin require matching operator configuration. They are not claims about an enabled deployment. Publication is tracked in the delivery evidence.

## 18 Delivery estimate and implementation dependencies

Gateway delivery review on 2026-10-07 corrected API-key cache degradation: failed cache writes keep bounded primary authorization, malformed lookups use the primary, and conflicting atomic-fill state still denies access. Fixed-label metrics and denial regressions cover these paths. The additional canonical production-image CI gate is retained in [the image-build patch](issue-344-ci-image-build.patch). It checks the non-root user, UI assets, and external-auth POST routes, and adds the image job to the required `test` check. The publication token lacks workflow-write permission, so a workflow-authorized maintainer must apply the patch. Current CI must not be reported as certifying the final production image.

The configured dev-cluster Google Cloud credentials require interactive reauthentication. No dev deployment was changed. Repository publication and CI can proceed while the operator restores deployment access.

Plan for 8 to 12 engineer-days of gateway work and 3 to 5 engineer-days of Console integration, UI mount, and deployed acceptance testing. These are planning estimates for the production scope above. Calendar time also depends on security review, staging access, and the Console team's deployment schedule. The earlier 5 to 10 day estimate assumed smaller integration and shared-auth changes.

The critical path is schema/identity constraints, scoped session authorization, the Console proxy, then end-to-end and rollout evidence. UI mount and Console integration can proceed once the contracts in slice 1 are stable. Slice completion must not substitute for a passed production gate.

The Console lives in Replit and its agent owns slice 6 server changes, as confirmed by the user. That agent must record its tenant provisioning contract, encrypted shared-store mechanism, logout/webhook delivery, route ownership, and CI system before implementation. Before enabling production, record actual replica/process/surge counts, database headroom, integration traffic, key-cache revocation bounds, and staging proof. These are concrete implementation inputs and deployment gates; they do not change the security invariants or reduce acceptance coverage.

Out of scope: replacing Clerk, pricing/checkout changes, automated editing of existing customer records, platform-admin customer sessions, multi-workspace private ownership for one external account, new inference authentication protocols, and a universal OAuth token-exchange framework.
