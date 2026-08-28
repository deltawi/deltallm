---
title: Versions, Compatibility, and Deprecation
description: Documentation channels, release compatibility, deprecation, and upgrade policy.
status: policy
audience: operators, integrators, contributors
---

# Versions, Compatibility, and Deprecation

This page defines how DeltaLLM release documentation should behave. It also states what the current
site does not yet provide.

## Current documentation channel

As of August 27, 2026, `https://docs.deltallm.io/` publishes one rolling documentation set from the
main development line. Treat it as **latest**, not as a promise that every page matches the last
production release. Version snapshot URLs, a separate stable alias, and stale-version banners are
not deployed yet.

Until those channels exist:

- use the release notes and the source tree at the exact application tag for production changes;
- do not cite the rolling site as proof that a setting or endpoint exists in an older release; and
- require rollout pages and generated OpenAPI/config references in the same product change.

## Channel contract

When versioned publication is enabled, it must implement this contract:

| Channel | Source | Mutability | Intended reader |
| --- | --- | --- | --- |
| `latest` | Default branch | Changes continuously | Evaluators and contributors tracking upcoming behavior |
| `stable` | Newest supported production release | Moves only when a release is promoted | Most operators and integrators |
| `<major>.<minor>` snapshot | Matching release tag | Immutable except clearly labeled documentation corrections | Operators maintaining that release line |

The root custom domain should redirect to or visibly identify `stable` once stable snapshots exist.
Every page should expose the selected version and offer links to stable/latest.

An unsupported snapshot must show a persistent banner above page content:

> This DeltaLLM documentation version is no longer supported. It may contain insecure or obsolete
> behavior. View the stable documentation before operating or upgrading.

The release process must publish the actual support matrix. A snapshot becomes unsupported only
through that recorded release decision; age alone is not a support contract.

## Compatibility surfaces

Review compatibility separately for:

- public gateway endpoints and streaming event shapes;
- Admin API and session behavior;
- Pydantic/YAML/environment settings and defaults;
- database schema, migration history, and durable worker records;
- Helm values, manifests, probes, and image startup commands;
- provider capability/model metadata; and
- Admin UI routes, roles, screenshots, and operator workflows.

Generated OpenAPI/config/provider references describe the revision that produced them. They do not
guarantee an upstream provider will preserve model availability, price, quota, or behavior.

## Versioning policy

Release tags use a major/minor/patch shape. Before 1.0, a minor release may contain a breaking
change, but it still requires an explicit migration/compatibility note. After 1.0:

- patch releases should contain compatible fixes and documentation corrections;
- minor releases may add compatible endpoints, fields, settings, and behavior; and
- major releases may remove deprecated contracts or require incompatible migration.

Database schema changes remain forward migrations. Rolling application rollback is supported only
when the prior binary is compatible with the migrated schema; there is no implied schema downgrade.

## Deprecation lifecycle

1. **Announce:** mark the old contract deprecated in docs, schema/response warnings where feasible,
   and release notes. Name the replacement and earliest removal version.
2. **Overlap:** keep old and new behavior together for at least one normal release cycle when safe,
   with tests for both and telemetry that can prove remaining use.
3. **Remove:** remove only in an allowed breaking release, publish migration and rollback boundaries,
   and delete obsolete generated reference entries/tests.

Critical security, integrity, legal, or upstream-provider constraints may require an accelerated
removal. The release note must state why the normal overlap was unsafe and provide the narrowest
available mitigation.

Changing a default can be breaking even if the field remains. Document default, explicit override,
restart/reload behavior, tenant scope, and mixed-version behavior.

## Release documentation requirements

A release is not documentation-complete until it includes, as applicable:

- user-visible summary and compatibility classification;
- exact upgrade prerequisites and supported source versions;
- one coordinated database migration/cutover sequence;
- configuration/default/deprecation changes;
- API/schema and provider-reference regeneration;
- security, operational, metrics, and alert changes;
- Admin UI workflow and screenshot updates; and
- verified rollback boundaries and known limitations.

Follow [Upgrades and rollbacks](../deployment/upgrade-and-rollback.md) for operational sequencing.
