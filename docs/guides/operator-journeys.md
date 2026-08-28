# Operator Journeys

These journeys join the UI, API, configuration, and operational checks around outcomes. Use test
tenants and scoped credentials where possible. Each journey ends with evidence that can be retained
in a change record.

## 1. Connect a model and send the first request

**Access:** platform admin for model creation; any authenticated user plus a valid API key for the
Playground.

1. Add provider credentials through [Named Credentials](../admin-ui/named-credentials.md), or enter
   deployment-local credentials for a one-off evaluation.
2. In [Models](../admin-ui/models.md), create a chat deployment with a public model name, upstream
   model ID, provider, and pricing metadata if spend accuracy is required.
3. Wait for the deployment to report healthy.
4. Create a scoped [API key](../admin-ui/api-keys.md) allowed to call that target.
5. Send one streamed request in the [Playground](../admin-ui/playground.md), then repeat it using
   the curl example from Inspect.

**Pass:** the target appears in authenticated `GET /v1/models`, both requests complete, Usage &
Spend attributes the request to the expected key/team/model, and an invalid key returns `401`.

## 2. Onboard a tenant with least-privilege access

**Access:** platform admin creates an organization; an applicable organization owner/admin can
manage its teams and keys according to the [access matrix](../admin-ui/access-requirements.md).

1. Create an [organization](../admin-ui/organizations.md) and set tenant-wide budget/rate ceilings.
2. Grant the organization only the required callable targets or access groups.
3. Create a [team](../admin-ui/teams.md), decide its self-service key policy, and narrow its target
   set if needed.
4. Invite the operator/developer through [People & Access](../admin-ui/people-and-access.md).
5. Create a team-owned key with an expiry and limits below the parent ceilings.
6. Call one allowed target and one target outside the tenant ceiling.

**Pass:** the allowed call succeeds; the disallowed call receives a non-success authorization
response; the user cannot see another tenant; usage is attributed to the new organization/team.

## 3. Rotate a shared provider credential

**Access:** platform admin.

1. Identify every linked deployment from [Named Credentials](../admin-ui/named-credentials.md).
2. Add/activate the new upstream secret without revoking the old secret yet.
3. Update the named credential. Omitted fields are preserved and secret-bearing fields remain redacted.
4. Wait for runtime convergence and test one request per affected workload/provider region.
5. Revoke the old upstream secret only after the new request evidence succeeds.

**Pass:** linked deployments remain healthy, new gateway requests succeed, the old upstream
credential is rejected directly, and no raw credential appears in API responses, logs, or screenshots.

## 4. Publish a route-group canary and test failover

**Access:** platform admin.

1. Create two healthy deployments with compatible workload modes.
2. Create a [Route Group](../admin-ui/route-groups.md), add both members, and keep it non-live while editing.
3. Define a weighted policy with a small canary share or a priority policy with a standby.
4. Use policy simulation with success and assumed-failure outcomes; retain the selection summary.
5. Publish the policy, mark the group live, and use a scoped key to call the group key.
6. Observe private deployment health and fallback events, then restore the healthy steady state.

**Pass:** simulation follows the intended split/order, live calls resolve through the group, an
assumed or controlled primary failure selects the expected fallback, and no out-of-scope key sees it.

## 5. Promote a prompt without changing application code

**Access:** platform admin.

1. Create a template and immutable first version in [Prompt Registry](../admin-ui/prompt-registry.md).
2. Define required variables and run the render/resolution test with representative input.
3. Point a stable label such as `production` at the reviewed version.
4. Bind the prompt at the supported route-group/scope and call it with a scoped key.
5. Create a second version, test it, move the label, and retain the prior version for rollback.

**Pass:** missing variables fail before provider execution, valid variables render the expected
prompt, the label move changes new resolutions, and rollback to the prior label target restores behavior.

## 6. Apply and prove a scoped guardrail

**Access:** platform admin in the current Guardrails UI.

1. Create or select a [guardrail](../admin-ui/guardrails.md) with the intended mode/action/threshold.
2. Assign it to a test organization, team, or API key rather than enabling it globally first.
3. Send a benign request and a synthetic request designed to trigger the policy.
4. Review the client response and authorized audit/telemetry record; ensure sensitive test content
   follows the environment's retention policy.
5. Test a neighboring scope that should inherit a different policy.

**Pass:** benign traffic succeeds, triggering traffic produces the configured action, and the
neighboring scope proves the expected isolation/inheritance boundary.

## 7. Investigate a spend or traffic anomaly

**Access:** platform admin or a spend-read role for the affected scope; `audit.read` when audit
correlation is required.

1. Start at [Dashboard](../admin-ui/dashboard.md) to bound the time window and affected model/provider.
2. In [Usage & Spend](../admin-ui/usage.md), select exactly one platform/org/team/self view and narrow
   by model/key where the role permits it.
3. Capture request/correlation IDs and compare rate-limit, provider, and cache behavior.
4. Open [Audit Logs](../admin-ui/audit-logs.md) only for authorized metadata/payload context.
5. Contain with a narrow key revocation, budget/rate change, or deployment action; avoid global changes first.

**Pass:** totals reconcile to one explicit reporting scope, the causal key/model/provider or gap is
identified, containment stops the anomalous series, and audit/spend ingestion backlogs are healthy.

## 8. Run and operate a batch

**Access:** scoped key for submission; platform admin or `key.read` to view; applicable update
permission for cancel/retry/replay actions.

1. Follow the [Batch API quick start](../features/batching.md#quick-start) to upload JSONL and create a batch.
2. Open [Batch Jobs](../admin-ui/batch-jobs.md), find the ID, and watch queued/running/finalizing state.
3. Inspect item failures and cost; cancel only while the lifecycle allows it.
4. Download the output/error files when complete. For terminal webhooks, reconcile an uncertain
   outcome before replaying.

**Pass:** counts reconcile with input lines, the terminal status and output/error files agree,
spend attribution is in the submitting scope, and no unauthorized tenant can open the batch.

## 9. Expose one governed MCP tool

**Access:** `key.read` to inspect; `org.update` for server/binding/policy changes; `key.update` for
manual approval decisions.

1. Register the server in [MCP Servers](../admin-ui/mcp.md) with reviewed URL, auth, timeout, and
   forwarded-header allowlist.
2. Refresh capabilities and run the health check.
3. Bind the server to a test tenant and allowlist only the intended tool.
4. Add an enabled tool policy with rate/concurrency limits and, if required, manual approval.
5. Call through the [MCP API](../api/mcp.md) using an allowed key and a neighboring denied key.

**Pass:** only the allowlisted tool is discoverable, the allowed call succeeds or enters the
approval queue as designed, the denied scope cannot see/call it, and audit/operations data records it.

## 10. Upgrade a production deployment safely

**Access:** deployment, database-migration, observability, and rollback owners.

1. Complete the [production checklist](../deployment/production-checklist.md) and read every
   release-specific rollout page between versions.
2. Back up and restore-rehearse critical data.
3. Run one target-image [migration job](../deployment/database-migrations.md) before new replicas.
4. Deploy a canary with migration bootstrap disabled; verify success and intentional denials.
5. Expand traffic while watching readiness, errors, latency, durable backlogs, and provider state.
6. Exercise the documented application rollback while schema compatibility still permits it.

**Pass:** migration evidence predates rollout, every replica uses the pinned image and explicit app
command, smoke/denial checks pass, alerts remain stable, and the rollback result is recorded.
