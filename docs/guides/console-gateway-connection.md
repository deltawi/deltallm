# Console and gateway connection contract

This document is the handoff to the Console agent in Replit. Implement the signer, encrypted session vault, proxy, renewal, key selection, and logout in the Console. The gateway implements verification, binding, account provisioning, scoped authorization, revocation, and required audit.

## Values to agree before connection

| Value | Owner and requirement |
| --- | --- |
| Gateway base URL | Operator; fixed HTTPS origin. Never use a request parameter as the upstream URL. |
| Integration ID | Operator; stable ID registered in gateway startup config. |
| Assertion issuer | Console/operator; exact HTTPS URL. |
| Audience | Operator; exact deployment-specific string. |
| Identity issuer | Console/operator; exact verified Clerk instance issuer. |
| Public keys and `kid` | Console publishes RSA public keys; operator installs at most four per integration. |
| Console browser origin | Operator installs the exact HTTPS origin in `allowed_origins`. |
| Customer reference and binding | Provisioning service registers an existing gateway organization/team and stores the returned binding ID. |
| Shared salt | Gateway-only; not needed by the Console. |
| UI mount | `/gateway` for this connection. Serve the built gateway assets under `/gateway/ui/`. |

Private signing keys and management credentials stay in backend secrets. A customer route must never attach the management credential. Use an explicitly configured upstream origin with TLS verification and bounded connection/read deadlines.

## Sign an assertion

Verify the current Clerk user and session on the backend. Select the customer from the authenticated user's permitted customers, then resolve its immutable binding from the trusted Console database. Do not copy a browser-selected organization, team, role, account, entitlement, or runtime-user ID into the assertion.

The JWT header must contain only `alg=RS256`, `typ=JWT`, and the configured `kid`. Use a fresh cryptographically random base64url `jti` with at least 16 random bytes for every attempt. Do not reuse it after a timeout or conflict.

```json
{
  "iss": "https://console.example.com",
  "aud": "deltallm-production",
  "sub": "user_clerk_stable_id",
  "identity_issuer": "https://your-clerk-instance.example.com",
  "iat": 1700000000,
  "nbf": 1700000000,
  "exp": 1700000060,
  "jti": "C5AkI3WdBQgCHXYCs8YXJw",
  "purpose": "gateway_session_exchange",
  "binding_id": "registered-gateway-binding",
  "external_customer_id": "trusted-console-customer-reference",
  "email": "verified@example.com",
  "email_verified": true,
  "external_session_id": "sess_clerk_stable_id",
  "auth_time": 1699999000,
  "external_session_expires_at": 1700040000
}
```

Use current integer UTC seconds. The example times are placeholders. Require verified email from Clerk. Keep the original authenticated session time and expiry on renewal. The child lifetime is at most 300 seconds. The parent ends at the earlier of the original Clerk expiry or 12 hours after `auth_time`. A different customer under the same Clerk session needs a separate approved session scope; do not reuse an external session ID to reassign a gateway parent. In this release, one exact issuer/subject belongs to one customer binding.

The compact JWT is at most 8 KiB and JSON request body at most 16 KiB. Subject, binding, customer, and session references are at most 200 UTF-8 bytes; email is at most 320 bytes. Extra claims are rejected. An audience array is rejected.

## Exchange and vault

Call `POST {gateway}/auth/external/exchange` with `Content-Type: application/json` and `{"assertion":"..."}`. Send no Cookie, Origin, Authorization, or master header. A bounded opaque `X-Correlation-ID` is optional. Do not log the request body or response token.

The success response has `session_token`, `session_generation`, `account_id`, `organization_id`, `team_id`, `inference_user_id`, `binding_id`, `expires_at`, `refresh_after_seconds`, `next_step`, and `mfa_required`. It sets no cookie. A successful response has this shape (IDs and times are illustrative):

```json
{
  "session_token": "psk_ext1_backend-only-example",
  "session_generation": 1,
  "account_id": "gateway-account",
  "organization_id": "existing-customer-org",
  "team_id": "existing-billing-team",
  "inference_user_id": "gateway-account",
  "binding_id": "registered-gateway-binding",
  "expires_at": "2026-10-06T12:05:00Z",
  "refresh_after_seconds": 240,
  "next_step": "ready",
  "mfa_required": false
}
```

The raw `psk_ext1_...` token is a gateway secret.

Store the token with authenticated encryption in the Console backend. Bind the record to the Clerk issuer/user/session, customer, gateway account, and returned binding. Include the selected API key only in that same scope. Use a stable, rotated vault encryption key from backend secrets; retain old decrypt keys during rotation. Store an opaque random reference in a Secure, HttpOnly, SameSite=Lax Console cookie, scoped to `/gateway/`. Apply CSRF checks to every unsafe request. Never return the assertion or gateway token in HTML, JavaScript, JSON to the browser, a URL, browser storage, logs, traces, or an analytics event.

Renew by calling the same `POST {gateway}/auth/external/exchange` endpoint with a fresh complete `gateway_session_exchange` assertion and `{"assertion":"..."}` body. Keep the subject, identity issuer, customer/binding, external session ID, original authentication time, and original Clerk expiry. Generate new assertion times and nonce. Send no Cookie, Origin, Authorization, or master header. Renewal returns the same response schema with a newer generation.

Renew at the returned delay, with bounded jitter and one operation in flight per vault record across all Console replicas. Use a shared lease and compare-and-set the stored generation. Discard a renewal response if its generation is older, its scope differs, logout has started, or its Clerk session is no longer active. The gateway keeps at most two unexpired generations and allows at most 30 seconds for the previous token. Do not use the overlap as a retry loop.

After a lost exchange response, sign a fresh assertion. Provisioning is idempotent but nonce reuse returns conflict. Bound retries and honor Retry-After. Stop renewal at the fixed parent deadline and require fresh Clerk authentication. A revoked parent cannot be reopened.

For a linked account, `next_step=mfa_verify` or `password_change` means the UI must complete the existing gateway control. Do not omit, simulate, or clear those controls in the proxy.

## Customer proxy and UI

Serve the gateway UI at `/gateway/` and its assets at `/gateway/ui/`. Configure `general_settings.ui_mount` as shown in the gateway guide, or inject exactly this non-secret JSON block into the served index before the module script:

```html
<script id="deltallm-ui-config" type="application/json">{"mount_path":"/gateway","external_console":true}</script>
```

Prefix built `/ui/` asset URLs with `/gateway`. Preserve SPA navigation under the router basename. UI runtime configuration contains no secrets. Never cache authenticated HTML, `/auth/me`, asset data, mutation results, or key selection across customers. Use `Cache-Control: no-store` for customer responses. Plain static files can be cached independently.

Strip `/gateway` once to form a fixed allowlisted upstream path. Validate the Clerk session and vault scope on every request. Remove browser Authorization, X-Master-Key, gateway/master cookies, arbitrary forwarding headers, and upstream-target headers. Supply only `Cookie: deltallm_session=<vault token>` for UI APIs. On unsafe requests, preserve the Console Origin **only after** verifying it and CSRF. Generate forwarding headers from the trusted ingress chain; do not accept them from browser input.

The allowlist must cover only the UI customer capabilities and required auth actions. Allow `/auth/me`, `/auth/internal/logout`, `/auth/mfa/verify`, `/auth/internal/change-password`, permitted `/ui/api/` customer routes, and the listed inference paths. Deny internal/master login, external exchange/revoke from browser traffic, external-auth administration, RBAC administration, all `/ui/api/batches` and `/ui/api/batch-create-sessions` routes, settings writes, tenant-wide key mutations, and unrequested upload or streaming paths. Gateway checks remain authoritative for every permitted route.

Implement `GET /gateway/console/login` as the Console-owned Clerk sign-in/re-authentication entry. Its `returnTo` must be a relative path within `/gateway/`; reject foreign origins, `//`, backslashes, encoded separators, control characters, and loops into Console/auth endpoints. The UI shows this link when the session is anonymous. It does not invoke master login.

## Playground key selection

The gateway browser session is not an inference credential. Implement these Console-owned endpoints:

| Endpoint | Contract |
| --- | --- |
| `GET /gateway/console/inference-key` | Return `{"selected":false}` or safe `{"selected":true,"key_name":"..."}` for the current vault scope. Do not return raw keys, token hashes, or gateway sessions. |
| `POST /gateway/console/inference-key` | Strict bounded `{"api_key":"..."}` body. Validate active Clerk/vault/CSRF; call the gateway validation below; store the raw key encrypted only after success. Return the same safe selection DTO. Clear a failed or revoked selection. |

To validate selection, call `POST {gateway}/auth/external/inference-key` with `{"api_key":"..."}`, the gateway session cookie, and the approved Console Origin. This checks account owner, exact mapped runtime user and team, expiry, active workspace, MFA, and forced-password-change state. Master and service-account keys are denied. Success returns safe metadata only; verify the returned account/runtime/team match the vault before storing the raw key.

For `/gateway/v1/chat/completions`, `/gateway/v1/audio/speech`, and `/gateway/v1/audio/transcriptions`, remove the gateway session cookie and send `Authorization: Bearer <selected raw API key>` to the normal gateway inference API. Do not attach master credentials. Limit body size, upload size, duration, and parallel work. Relay streaming with cancellation and backpressure; release upstream work on browser disconnect. Keep provider error bodies and raw request/response content out of logs. On invalid key responses, clear the selection and ask the user to select another key.

The UI clears its raw key entry after selection. It sends inference through the Console with no bearer header. Do not persist a pasted raw key in browser storage.

## Logout, Clerk events, and errors

On logout, atomically mark the vault record closing so renewals cannot replace it. Revoke the gateway parent with a fresh signed `gateway_session_revoke` assertion. Proxied `/auth/internal/logout` also revokes a parent when its child is still valid, but an expired child can return a harmless logged-out response without identifying a live parent. The Console must ensure signed parent revocation is delivered even in that case; preserve the verified scope/session metadata in its closing retry record. Revoke Clerk as required by the product flow, remove the vault record and selected API key, expire the reference cookie, and reject stale in-flight completions. If upstream revocation fails, deny the local vault immediately and retain a bounded encrypted durable retry record. Report incomplete upstream logout without reopening local access.

Verified Clerk session-revocation events use `POST /auth/external/revoke` with `gateway_session_revoke`. User suspension uses `gateway_subject_suspend`. Verify webhook signatures before generating an assertion. A revoke before first exchange creates a parent tombstone; subject suspension creates a suspended subject shell. Use the same complete claim set as exchange, replacing `purpose` and the assertion time/nonce fields. A revoke assertion includes the original Clerk session ID, authentication time, and expiry even after that session has expired; its **assertion** must still be fresh and signed. Submit `{"assertion":"<fresh signed JWT>"}` as the only JSON field. Success is `204` with no body or cookie. Subject suspension uses the authenticated subject's last verified identity metadata; do not invent verified email from untrusted webhook input. Repeat with a fresh nonce is harmless. Application API keys remain active unless explicitly revoked.

| Status | Console behavior |
| --- | --- |
| 400 / 413 | Correct the request; no automatic retries. |
| 401 | Reject signer/proof; surface an operator-safe error and require valid authentication. |
| 403 | Stop customer access. `external_reauthentication_required` requires fresh Clerk authentication. Do not switch to master credentials. |
| 409 | `assertion_replayed`: use a fresh nonce within the bounded retry policy. `account_link_required` and `identity_binding_conflict`: stop and request operator resolution. |
| 429 | Honor Retry-After with bounded retries; avoid concurrent renewals. |
| 503 | Show temporary unavailability, honor Retry-After, and retain only the existing still-valid scoped vault record. Never serve another customer's cached data. |

Preserve the gateway's safe error code and correlation ID. Redact bodies, cookies, Authorization, assertion, session token, and raw API key in HTTP client, proxy, tracing, and exception logs.

## Implementation checklist for the Replit Console agent

- Configure the fixed gateway HTTPS URL, exact signer/Clerk issuers, audience, `kid`, private signing key, allowed browser origin, and vault encryption keys in backend secrets.
- Extend the existing customer provisioning workflow to register its existing organization/team and retain the immutable binding ID. Keep the operator management credential in that workflow.
- Add the strict assertion signer and fresh-nonce retry policy; verify Clerk and select the customer from backend authorization data.
- Implement the encrypted shared vault, reference cookie, cross-replica renewal lease, generation compare-and-set, fixed parent deadline, and principal-switch cleanup.
- Serve mounted gateway assets and customer APIs through the fixed allowlist with Origin/CSRF checks, secret-header stripping, uncached customer responses, deadlines, cancellation, and streaming backpressure.
- Implement the Console-owned login and inference-key endpoints above. Preserve gateway MFA/password-change handling and safe pending key-revocation status.
- Implement closing-state logout, verified Clerk events, encrypted durable retry delivery, and local denial during outages.
- Add Console unit/integration/browser tests to its existing CI and return the shared staging evidence below.

The gateway's operator configuration and registration steps are in [external customer sign-in](../configuration/external-customer-auth.md). No Console repository access is needed to implement this published contract. Production remains disabled until both teams qualify the connected deployment.

## Release proof shared with the gateway team

Provide staging evidence for first/repeat login, multi-replica renewal and logout races, Clerk revocation before login, suspension/resume, linked MFA and password changes, two distinct customers, foreign workspace and mixed-master denials, private credential/model creation, bound sharing, owner-key selection, normal inference attribution, Redis/database/audit failure, rate saturation, and key revocation enforcement after Redis recovery. Snapshot billing, budgets, blocking, pricing, margins, and existing records before and after sign-in.

Gateway local tests and an unpublished Console implementation do not complete these checks. Keep production integrations disabled until this joint proof and the certified connection/load budget are approved.
