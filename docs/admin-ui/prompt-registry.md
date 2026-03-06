# Prompt Registry

The Prompt Registry page lets platform admins manage prompts as first-class versioned objects. Each prompt template can have immutable versions, movable labels such as `production`, scoped bindings, and render or precedence testing.

## What to do first

The recommended setup order in the UI is:

1. Create the prompt shell
2. Author the first version
3. Promote a label to that version
4. Bind the label to the right scope
5. Run render and resolution tests

The page is structured around that order so users do not have to understand every advanced control before getting the first prompt into service.

## Creating a prompt

Click **Create Prompt** and start with the minimum required fields:

- **Template Key** — Stable identifier used by labels, bindings, and requests
- **Prompt Name** — Human-readable name

Optional metadata such as description and owner scope is grouped separately so the first step stays lightweight.

## Detail page flow

The detail page is organized as a rollout workflow:

### 1. Template

Use this section for low-frequency metadata updates:

- Name
- Description
- Optional owner scope

### 2. Author Version

Start with the prompt body only. This is the main authoring step.

Advanced version settings are available when needed:

- **Variables Schema** for structured input validation
- **Model Hints** for runtime guidance
- **Route Preferences** for optional routing influence
- **Publish immediately** for already-reviewed versions

### 3. Promote and Bind

This section groups two related tasks:

- **Promote label** — Move a stable label such as `production` or `staging` onto a chosen version
- **Bind label to scope** — Decide which key, team, org, or model group should resolve that prompt

The UI shows current labels and bindings so operators can confirm rollout state at a glance.

### 4. Test Resolution

Use tests before relying on a prompt in production:

- **Render test** validates variables and shows the rendered output
- **Binding precedence preview** shows which prompt would resolve for a given request context

Render actions stay disabled until at least one version exists.

### 5. History

Use the history view to:

- Review immutable prompt versions
- Publish a previous version
- Compare two versions side-by-side

History is intentionally separated from authoring so the main rollout flow stays focused.

## UI guidance

The page includes:

- Summary cards for version, label, and binding counts
- A rollout-progress checklist so operators know the next required step
- Empty-state guidance when labels, bindings, or versions do not yet exist
- Prerequisite-aware actions that reduce dead-end interactions

## Related docs

- [Model Groups](model-groups.md)
- [Routing & Failover](../features/routing.md)
