# Guardrails

Guardrails are the policy surface for safety checks and scoped enforcement.

![Guardrails](images/guardrails.png)

## What this page does

- Define reusable guardrail policies
- Set mode, action, threshold, and default enabled state
- Assign policies at organization, team, or API-key scope

## Page structure

- **Top table** lists the current global guardrail definitions
- **Create / edit modal** manages the policy definition
- **Scoped Assignments** applies policies where they should take effect

## Scope resolution

Assignments follow the same hierarchy used elsewhere in the gateway:

- global
- organization
- team
- API key

Use the scoped editor when a policy should apply only to a specific tenant or integration instead of the full platform.
