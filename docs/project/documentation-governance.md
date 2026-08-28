---
title: Documentation Governance
description: Ownership, review, metadata, quality metrics, and maintenance cadence for public docs.
status: policy
audience: maintainers, contributors
---

# Documentation Governance

Documentation is part of the product contract. The author of a product change owns its initial
documentation; maintainers of the affected implementation review accuracy. A separate docs team is
not a prerequisite for keeping behavior and guidance aligned.

## Ownership by surface

| Documentation surface | Accountable reviewer |
| --- | --- |
| Gateway/Admin API and generated OpenAPI | Maintainer of the affected router/service contract |
| Settings and generated field reference | Maintainer of configuration/bootstrap behavior |
| Provider capabilities/catalog | Maintainer of provider adapter and provider metadata |
| Admin UI workflows/screenshots | Maintainer of the affected UI route plus its backend endpoint |
| Deployment, migrations, backup, incidents | Maintainer of the artifact/runbook and an operator familiar with the target platform |
| Security/auth/tenancy | Maintainer of the affected trust boundary and authorization code |
| Information architecture/style | Documentation maintainer or designated release reviewer |

Repository ownership rules should name actual reviewers when maintainers adopt CODEOWNERS. This
policy deliberately does not invent usernames or teams.

## Product-change review prompts

Every pull request should answer whether it changes:

- a route, request/response/error, streaming event, auth rule, or tenant scope;
- a setting, environment variable, default, reload/restart behavior, or secret;
- database schema/data, migration ordering, mixed-version behavior, or rollback safety;
- provider support, capability, model catalog, pricing, or credential requirements;
- Admin UI navigation, permission, workflow, empty/error state, or screenshot;
- deployment manifest, probe, metric, log, alert, runbook, or security boundary; or
- a public link/heading/URL, generated artifact, release note, or deprecation.

When the answer is yes, the same pull request should update the relevant docs and tests or explain
why no public contract changes. The repository pull-request template carries these prompts.

## Page metadata expectations

New policy, concept, and substantial operational pages should use MkDocs YAML front matter:

```yaml
---
title: Human-readable title
description: One sentence used for review and discovery.
status: stable | experimental | deprecated | policy
audience: developers | operators | administrators | contributors
---
```

Add `applies_to` when guidance is version-specific and `last_reviewed` only when a named review
cadence owns that date. Do not add dates that nobody will maintain. Metadata adoption for existing
pages is incremental; the required structural baseline is an H1, navigation entry, valid links, and
an explicit audience/prerequisite where the task needs one.

## Page types and style

- **Tutorial:** a reliable learning path with one successful outcome.
- **How-to/guide:** task-focused steps, prerequisites, scope, and verification.
- **Reference:** complete contract generated or validated from code when possible.
- **Explanation/concept:** mental model, boundaries, alternatives, and tradeoffs.
- **Runbook:** trigger, safe diagnosis, containment, recovery, verification, and escalation.

Do not mix an exhaustive field catalog into a tutorial or bury production failure behavior in a UI
caption. Split pages when one file serves multiple reader intents or exceeds roughly 500 lines; the
health report flags size for review without failing solely on length.

## Quality gates and metrics

Every documentation pull request runs:

- strict MkDocs build and internal-artifact containment;
- generated OpenAPI/config/provider drift checks;
- navigation, H1, and local-image integrity checks;
- focused documentation tests and diff whitespace validation.

`scripts/docs/report_health.py --check` reports public/nav page counts, unnav pages, missing H1s,
referenced/missing/orphan images, and pages over 500 lines. Publication-blocking structural defects
fail CI; size and orphan metrics drive maintenance work rather than arbitrary deletion.

Track these trends at least once per release and review the complete site quarterly:

- strict-build/containment failures and time to repair;
- generated-reference drift incidents;
- public pages outside navigation, missing images, and oversized pages;
- stale screenshots and procedures found during UI/release review;
- top failed search terms or zero-result searches when analytics exists; and
- support issues caused by missing, inaccurate, or version-mismatched documentation.

The quarterly review should sample the quickstart, one tenant journey, one provider, one UI flow,
one upgrade, and one restore/incident procedure. Record owners and due dates for findings.

See the repository's `CONTRIBUTING_DOCS.md` for local commands and writing rules.
