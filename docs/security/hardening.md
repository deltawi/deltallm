# Production Hardening

Use this checklist with your organization's threat model. DeltaLLM provides application-level
authentication and tenant policy, but the platform must also enforce transport, network, secret,
and workload boundaries.

## Edge and route exposure

Expose only routes required by each listener:

| Route family | Public gateway | Admin listener | Operator network |
| --- | --- | --- | --- |
| `/v1/*`, approved `/mcp/*` | Yes | Optional | Optional |
| Admin UI, `/auth/*`, `/ui/api/*` | No unless explicitly required | Yes | Optional |
| `/health/liveliness` | Optional | Optional | Yes |
| `/health`, `/health/readiness` | No | No | Yes |
| `/health/deployments`, `/health/fallback-events`, `/metrics` | No | No | Yes |
| `/docs`, `/redoc`, `/openapi.json` | No by default | Optional | Yes |

- Terminate TLS with a trusted certificate and redirect or reject plaintext traffic.
- Configure an explicit host allowlist and request-body/time limits at the reverse proxy.
- Strip client-supplied forwarding headers and set them only at the trusted proxy. The application
  uses `X-Forwarded-Proto` when deciding whether login cookies are secure and reads forwarding
  headers for client-address metadata.
- Browser auth cookies are HTTP-only and `SameSite=Lax`; their `Secure` attribute depends on the
  request being recognized as HTTPS. Verify this from the deployed public URL.
- Do not treat CORS, UI navigation, or hidden routes as authorization. The application does not
  add a general CORS or trusted-host middleware policy by default.

## Identity and authorization

- Generate unique master and salt keys; never deploy example or placeholder values.
- Reserve the master key for bootstrap and break-glass use. Create scoped keys for workloads with
  model allowlists, expiration, rate limits, and budgets.
- Use SSO and MFA where supported for human administrators. Remove bootstrap passwords after
  enrollment and review role assignments regularly.
- Separate organizations and teams according to real ownership boundaries. Test both allowed and
  denied requests before rollout.
- Send audit data to durable storage, monitor ingestion failures, and restrict audit readers.

## Secret lifecycle

- Inject secrets from a platform secret manager or Kubernetes Secret referenced by name. Do not
  place secret values in Git, `config.yaml`, shell history, generated docs, or screenshots.
- Give PostgreSQL, Redis, object storage, email, telemetry, and each provider distinct credentials
  with only the permissions they require.
- Document the owner, creation time, rotation interval, consumers, and revocation procedure for
  every credential.
- Rotate provider and integration credentials by adding the new version, verifying traffic, and
  then revoking the old version. Plan master- and salt-key rotation separately because they can
  affect existing clients or stored-key verification.
- Treat accidental log, issue, or artifact exposure as compromise: revoke first, then investigate.

## PostgreSQL and Redis

- Use managed or separately operated production services with authentication, encryption in
  transit where supported, private networking, monitoring, and independent backups.
- Restrict security groups/network policies so only application, worker, migration, and approved
  operator identities can connect.
- Do not expose either service to the internet. Do not share the application database credential
  with people or unrelated workloads.
- Set capacity and connection limits from load tests; alerts should cover saturation, latency,
  replication health, storage, and failed authentication.

## Workload and supply chain

- Pin the DeltaLLM image by immutable digest and record the matching application/chart versions.
- Scan application and dependency images, verify provenance available from your release process,
  and apply security updates through the tested upgrade workflow.
- The current runtime image runs as root. Until a supported non-root image is available, enforce
  strong pod/container isolation: drop all capabilities, deny privilege escalation, use the
  default seccomp profile, avoid host mounts, and test a read-only root filesystem before relying
  on it. Do not claim these controls are active unless your rendered workload proves them.
- Disable service-account token mounting unless the workload needs Kubernetes API access. Apply
  resource limits, topology controls, and narrowly scoped ingress/egress policy.

## Outbound traffic and data

- Allow egress only to approved providers, identity services, callbacks, MCP servers, telemetry
  sinks, DNS, and required dependency endpoints.
- Batch webhooks reject unsafe destinations by default and hard-deny common cloud metadata
  addresses. Keep HTTPS-only/port restrictions and add private CIDRs only for reviewed targets.
- Treat configured MCP servers and callback integrations as privileged egress. Review destination,
  authentication, forwarded-header allowlists, tool permissions, and data classification.
- Disable message-content logging when content retention is not required:

```yaml
deltallm_settings:
  turn_off_message_logging: true
```

- Apply retention and access controls to PostgreSQL, object storage, Prometheus labels, tracing,
  provider logs, backups, and support bundles.

## Verification before exposure

- From the public network, confirm detailed health, metrics, schema, database, and Redis are
  unreachable.
- Confirm unauthenticated gateway and admin requests fail, and scoped credentials cannot cross
  organization/team/model boundaries.
- Confirm login cookies are `Secure`, `HttpOnly`, and have the expected same-site policy.
- Run a restore rehearsal, key revocation test, failed-migration test, and application rollback.
- Review the rendered workload for pinned images, security context, secrets, service-account
  mounting, and network policies.

Continue with the [production checklist](../deployment/production-checklist.md).
