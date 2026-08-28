## Summary

Describe the user-visible outcome and why the change is needed.

## Validation

List exact commands and results. Include success, failure, authorization-denial, migration, or
rollback evidence appropriate to the risk.

## Product and documentation impact

- [ ] Public API, errors, streaming, authentication, or tenant scope reviewed
- [ ] Settings, environment variables, defaults, secrets, and restart/reload behavior reviewed
- [ ] Schema, migrations, mixed-version sequencing, and rollback safety reviewed
- [ ] Provider capabilities, model metadata, pricing, and credentials reviewed
- [ ] Admin UI workflow, permissions, states, and screenshots reviewed
- [ ] Deployment, probes, metrics, alerts, security, backup, and runbooks reviewed
- [ ] Public docs/nav/links and release/deprecation notes updated, or no-doc-impact reason below
- [ ] Generated OpenAPI/config/provider artifacts regenerated and checked when their source changed

No-documentation-impact rationale:

<!-- Explain why users, operators, integrators, and contributors observe no contract change. -->

## Rollout and rollback

State feature gates, migration/cutover order, compatibility window, monitoring, and the last safe
rollback point. Write `Not applicable` only when there is no deployment-state change.
