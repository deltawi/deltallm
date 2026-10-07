# Issue 344: Replit Console staging handoff

Date: 2026-10-07. Protocol: `external_customer_v1`.

This public handoff uses example deployment URLs and tenant IDs. Replace them with the confirmed private staging values before use. The exact staging handoff remains in the local ignored `.local/issue-344/` directory.

The gateway implementation is in branch `codex/issue-344-external-customer-sign-in`. The example dev gateway URL is `https://gateway-staging.example.com`. The actual dev endpoint did not serve the external-auth interface at the last check. The Console connection is pending. The values below do not mean that an integration or binding is enabled.

This document gives the Console agent the staging values and connection steps. The complete interface specification is [Console and gateway connection contract](../docs/guides/console-gateway-connection.md). The gateway operator steps are in [external customer sign-in](../docs/configuration/external-customer-auth.md).

## 1. Staging values

| Value | State |
| --- | --- |
| Console origin | Example: `https://console-staging.example.com` |
| Customer signup | Example: `https://console-staging.example.com/sign-up` |
| Console UI mount | `/gateway/`; assets under `/gateway/ui/` |
| Assertion issuer | Proposed: `https://console-staging.example.com` |
| Integration ID | Proposed: `console-staging` |
| Audience | Proposed: `deltallm-console-staging` |
| Negative-test organization | Example: `org-zero-budget-test` |
| Negative-test team | Example: `team-zero-budget-test`; keep its zero budget |
| Gateway HTTPS base URL | Example: `https://gateway-staging.example.com`; feature routes not available yet |
| Exact Clerk identity issuer | Pending: Console agent; retain any path or trailing slash |
| Signing key ID and RSA public PEM | Pending: Console agent; RSA at least 2,048 bits |
| Trusted Console customer reference | Pending: Console agent; use the existing backend customer record |
| Gateway binding ID | Pending: returned by registration below |
| Two verified Clerk test subjects | Pending: Console team confirmation and test access |
| Second customer organization/team | Pending: separate workspace with permitted test inference |

Keep the signing private key and vault encryption keys in Console backend secrets. The gateway needs only the public signing key. Do not use the Console URL as the gateway upstream URL. The signer issuer and Clerk identity issuer are separate values.

One exact Clerk issuer/subject maps to one customer binding in this release. Use two distinct verified subjects and separate customer workspaces for isolation tests. The supplied zero-budget team is the negative case. Use the second team for successful inference. Do not change the zero-budget team to make the positive case pass.

## 2. Gateway staging setup

### Current dev gateway check

Credential-free checks on the actual private dev endpoint on 2026-10-06 confirmed:

- `POST /auth/external/exchange` with an empty JSON body returns HTTP 405 and `Allow: GET`.
- `GET /auth/external/exchange` returns HTTP 404.
- `GET /openapi.json` returns HTTP 200. It lists no `/auth/external/*` paths.

These results show that the current dev endpoint does not serve the new interface. They do not establish whether the cause is an older gateway binary or upstream routing. The gateway operator must deploy the issue 344 implementation or correct the upstream routing before the Console can connect. A configured signing key alone cannot add the route. In the new implementation, the route is registered even when the feature is disabled; disabled external auth returns HTTP 503, not HTTP 405.

Use `https://gateway-staging.example.com` for `{GATEWAY_BASE_URL}` below after the route is available. Do not change the request to GET to work around HTTP 405. No credentials, assertions, or sessions were sent in these checks.

### Configuration and registration

The gateway operator can use this configuration after the missing values are set. This is a setup template, not the current deployment state. Replace the placeholders before startup.

```yaml
general_settings:
  audit_ingestion_mode: outbox
  audit_ingestion_worker_enabled: true
  cache_invalidation_worker_enabled: true
  api_key_auth_cache_ttl_seconds: 60
  external_auth:
    enabled: true
    deployment_protocol: external_customer_v1
    allowed_origins: [https://console-staging.example.com]
    trusted_proxy_cidrs: []
    integrations:
      - integration_id: console-staging
        issuer: https://console-staging.example.com
        audience: deltallm-console-staging
        identity_issuer: <EXACT_CLERK_ISSUER>
        keys:
          - kid: <CONSOLE_KEY_ID>
            public_key: os.environ/CONSOLE_SIGNING_PUBLIC_KEY
  ui_mount:
    mount_path: /gateway
    external_console: true
```

Apply all seven external-auth migrations and deploy the compatible gateway binary to every API replica. Use PostgreSQL, Redis, durable audit delivery, and the cache invalidation worker. Include the additional four PostgreSQL connections per API process in the deployment budget. For Helm, set the certified external-auth capacity values from the operator guide. Trust settings are startup-only.

Use the operator management credential only in the existing customer provisioning workflow. Register the supplied existing organization/team:

```http
PUT {GATEWAY_BASE_URL}/ui/api/external-auth/integrations/console-staging/bindings/{TRUSTED_CONSOLE_CUSTOMER_ID}
Content-Type: application/json
X-Master-Key: <BACKEND_OPERATOR_CREDENTIAL>

{"organization_id":"org-zero-budget-test","team_id":"team-zero-budget-test"}
```

Store the returned `binding_id` in that trusted Console customer record. Retrying the same customer/pair is idempotent. A different pair is a conflict. Registration must not change budgets, blocking, billing metadata, roles, or prices.

The database integration starts disabled even when its public key is configured. The operator enables it separately:

```http
PUT {GATEWAY_BASE_URL}/ui/api/external-auth/integrations/console-staging
Content-Type: application/json
X-Master-Key: <BACKEND_OPERATOR_CREDENTIAL>

{"enabled":true,"expected_version":0,"reason":"Issue 344 isolated staging connection"}
```

Version `0` applies only to a new integration. Use the current version for later changes. Check the private operator endpoint `GET /ui/api/external-auth/status` for protocol `external_customer_v1` and state `ready`.

## 3. Signed exchange

Verify the current Clerk user, verified email, and active session on the Console backend. Resolve the customer and binding from backend authorization data. Do not accept tenant IDs or permissions as identity proof from the browser.

The JWT header contains only `alg: RS256`, `typ: JWT`, and the installed `kid`. The payload contains exactly these fields:

```json
{
  "iss": "https://console-staging.example.com",
  "aud": "deltallm-console-staging",
  "sub": "user_VERIFIED_CLERK_SUBJECT",
  "identity_issuer": "EXACT_CLERK_ISSUER",
  "iat": 1700000000,
  "nbf": 1700000000,
  "exp": 1700000060,
  "jti": "C5AkI3WdBQgCHXYCs8YXJw",
  "purpose": "gateway_session_exchange",
  "binding_id": "RETURNED_GATEWAY_BINDING_ID",
  "external_customer_id": "TRUSTED_CONSOLE_CUSTOMER_ID",
  "email": "verified@example.com",
  "email_verified": true,
  "external_session_id": "sess_VERIFIED_CLERK_SESSION",
  "auth_time": 1699999000,
  "external_session_expires_at": 1700040000
}
```

All values above are examples. Generate current integer UTC seconds for `iat`, `nbf`, and `exp`. The default assertion lifetime is at most 60 seconds, with a 10-second clock allowance. Generate a new random unpadded base64url `jti` for every attempt. Keep the original verified Clerk `auth_time` and session expiry on every renewal. Do not substitute the expiry of a refreshed Clerk JWT for the original session deadline.

Extra claims and an audience array are rejected. Do not add organization, team, account, role, runtime-user, or payment claims. Compact JWT limit: 8 KiB. JSON body limit: 16 KiB. Subject/binding/customer/session references: 200 UTF-8 bytes. Email: 320 bytes.

```http
POST {GATEWAY_BASE_URL}/auth/external/exchange
Content-Type: application/json

{"assertion":"<SIGNED_COMPACT_JWT>"}
```

This backend call sends no Cookie, Origin, Authorization, or master header. An optional `X-Correlation-ID` must match `[A-Za-z0-9_-]{1,80}`. A successful response is HTTP 200, with no Set-Cookie and `Cache-Control: no-store`:

```json
{
  "session_token": "psk_ext1_BACKEND_ONLY_SECRET",
  "session_generation": 1,
  "account_id": "GATEWAY_ACCOUNT_ID",
  "organization_id": "org-zero-budget-test",
  "team_id": "team-zero-budget-test",
  "inference_user_id": "GATEWAY_RUNTIME_USER_ID",
  "binding_id": "RETURNED_GATEWAY_BINDING_ID",
  "expires_at": "2026-10-06T12:05:00Z",
  "refresh_after_seconds": 240,
  "next_step": "ready",
  "mfa_required": false
}
```

Response IDs and times are examples. Preserve `next_step=mfa_verify` or `password_change` when returned. Existing account adoption or runtime-user linking requires the operator approval routes in the complete contract; never adopt an account by email automatically.

## 4. Token transport and renewal

The raw `session_token` is returned only to the Console backend. Encrypt it in the shared vault. The browser receives an opaque vault reference cookie, not the gateway token. Verify the active Clerk principal and exact vault scope before each request.

For customer UI/auth APIs, the Console sends this upstream header:

```http
Cookie: deltallm_session=<RAW_GATEWAY_SESSION_TOKEN_FROM_VAULT>
```

The Console generates that header. It removes browser-supplied Authorization, X-Master-Key, and gateway/master cookies. On unsafe customer calls, forward `Origin: https://console-staging.example.com` only after the Console has verified the browser Origin and CSRF. Preserve upstream status codes and safe error codes. `/auth/me` must report `auth_mode=session` and `session_source=external_customer`, with the expected workspace.

Renewal uses the same exchange endpoint and response schema:

```http
POST {GATEWAY_BASE_URL}/auth/external/exchange
Content-Type: application/json

{"assertion":"<FRESH_SIGNED_EXCHANGE_JWT>"}
```

Use `purpose=gateway_session_exchange` again. Keep the same verified `sub`, `identity_issuer`, customer/binding, Clerk session ID, original `auth_time`, and original Clerk session expiry. Generate new `iat`, `nbf`, `exp`, and `jti`. Send no existing gateway cookie, Origin, Authorization, or master credential on the renewal exchange.

Renew at the returned `refresh_after_seconds`, normally 240 seconds, with bounded jitter. Use a shared lease and generation compare-and-set across Console replicas. Verify that returned account, organization, team, runtime user, and binding match the existing vault scope. Replace the stored token only with a newer generation while the vault is open and the same Clerk session is active. Reject stale, wrong-scope, closing, or inactive-Clerk responses.

A child lives at most 300 seconds. At most two generations can be unexpired, with at most 30 seconds of overlap for the previous generation. The parent ends at the earlier of original Clerk expiry or original `auth_time` plus 12 hours. Renewal cannot extend that deadline. A revoked parent cannot reopen. After a lost exchange response, retry only within a bounded policy and sign a new nonce.

## 5. Signed logout and revocation

Atomically mark the vault closing before any upstream logout work. Stop renewal and reject in-flight completions. Send:

```http
POST {GATEWAY_BASE_URL}/auth/external/revoke
Content-Type: application/json

{"assertion":"<FRESH_SIGNED_REVOCATION_JWT>"}
```

Use the same complete claim set and RS256 header as exchange. Set `purpose=gateway_session_revoke` for logout or verified Clerk session revocation. Keep the original subject, customer/binding, Clerk session ID, `auth_time`, and session expiry. Generate fresh `iat`, `nbf`, `exp`, and random `jti`. Send no Cookie, Origin, Authorization, or master header. Success is HTTP 204 with an empty body, no Set-Cookie, and `Cache-Control: no-store`.

The original Clerk expiry may be in the past for revocation delivery, but the signed assertion must be fresh. Retain verified identity/session metadata in the encrypted closing/retry record. A revocation before first exchange creates a parent tombstone. Repeating revocation with a fresh nonce is harmless. The same parent/session ID cannot be exchanged again after revocation.

For verified user suspension, use the same endpoint and complete claims with `purpose=gateway_subject_suspend`. Verify Clerk webhook signatures before signing. Use trusted, previously verified identity metadata; do not claim verified email from untrusted webhook input.

Proxied `POST /auth/internal/logout` can revoke a parent while its child is valid. An expired child can return a logged-out response without identifying the parent. Always ensure that the signed parent revocation above is delivered.

Deny Console access immediately, clear the reference cookie and selected key, and retain bounded encrypted durable delivery retries if the gateway is unavailable. Report incomplete upstream revocation without reopening the local vault. During a delivery outage, a previously issued child can remain valid at the gateway for at most 300 seconds. Application API keys remain active unless separately revoked or denied by existing inference controls.

## 6. Console implementation requirements

1. Store gateway tokens and selected raw API keys with authenticated encryption in a shared backend vault. Bind each record to Clerk issuer/user/session, customer, account, and binding. The browser receives only an opaque Secure, HttpOnly, SameSite=Lax reference cookie with Path `/gateway/`. Never expose assertions or gateway tokens in browser responses, URLs, storage, logs, or traces.
2. Renew at `refresh_after_seconds` with bounded jitter, a shared lease, and a generation compare-and-set. Discard old, wrong-scope, closing, or inactive-Clerk results. Child lifetime is at most 300 seconds; previous-generation overlap is at most 30 seconds. The parent ends at the earlier of original Clerk expiry or original `auth_time` plus 12 hours. A revoked parent cannot reopen.
3. Serve `/gateway/` and `/gateway/ui/` with runtime mount configuration `{"mount_path":"/gateway","external_console":true}`. Verify Clerk and vault scope on every proxied request. Use a fixed allowlisted upstream and strip `/gateway` once. Strip browser bearer/master credentials and gateway cookies. UI APIs receive `Cookie: deltallm_session=<vault token>`. Validate Origin and CSRF before forwarding the approved Console Origin for unsafe calls. Do not cache customer responses. Preserve MFA/password-change controls and bounded streaming cancellation.
4. Implement Console-owned `GET /gateway/console/login` with a validated local `returnTo`, and `GET`/`POST /gateway/console/inference-key`. Key selection uses `{"api_key":"..."}` and is verified through gateway `POST /auth/external/inference-key` with the vault cookie and approved Origin. Store the raw key only after returned account/runtime/team match. Browser selection responses contain only `{"selected":false}` or `{"selected":true,"key_name":"..."}`. Inference uses that normal API-key bearer upstream, without the gateway session cookie. The browser sends no inference bearer.
5. Mark the vault closing before logout. Send a fresh full assertion to `POST /auth/external/revoke` with purpose `gateway_session_revoke`; success is HTTP 204. Use purpose `gateway_subject_suspend` for verified user suspension events. Verify Clerk webhooks before signing. Always deliver signed parent revocation, including when the child has expired. Stop local access immediately, reject stale renewal results, clear the reference cookie/key, and retain bounded encrypted durable retries if delivery fails. Application API keys need separate explicit revocation or existing inference blocking.

Exchange errors use the gateway error envelope. Reject invalid input on 400/413, invalid assertions on 401, and denied access on 403. `external_reauthentication_required` needs fresh Clerk authentication. For 409 `assertion_replayed`, use a fresh nonce within a bounded retry policy; `account_link_required` or `identity_binding_conflict` needs operator resolution. Honor Retry-After on 429/503. Never replace customer access with master access after failure.

## 7. Joint test completion

Console implementation and both verified customer identities remain pending. No connected end-to-end test has passed yet.

- Prove signup, first/repeat exchange, mounted navigation, owned credential/model/key creation, key selection, successful attributed inference, renewal, logout, and verified Clerk revocation.
- Use the two separate customers to deny foreign workspace, private asset, key, vault, and principal access. Test mixed master credentials, CSRF, concurrent tabs, principal change, renewal/logout races, lost responses, and outages.
- Use `team-zero-budget-test` for budget denial before and after renewal. Assert no provider dispatch. Compare billing, budget, blocking, pricing, and spend snapshots before and after sign-in operations.
- Inspect browser traffic/storage and backend logs for secret disclosure. Confirm gateway MFA/password-change behavior and pending API-key revocation enforcement.
- Record multi-replica concurrent sign-in/inference, peak replicas plus surge, database headroom, sustained cleanup/audit throughput, deployment interruption, and all-replica rollback evidence before production enablement.

The Console agent can implement this interface without gateway source access. To start connected testing, the dev gateway must serve the new routes. Also provide the exact Clerk issuer, installed `kid` and public key, registered customer/binding references, access to both verified test users, and the second isolated workspace.
