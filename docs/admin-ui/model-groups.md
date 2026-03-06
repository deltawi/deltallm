# Model Groups

The Model Groups page lets platform admins create explicit routing pools on top of existing deployments. A model group has a stable key, a workload type, a set of member deployments, and a routing policy.

## What to do first

The recommended setup order in the UI is:

1. Create the model group shell
2. Add one or more member deployments
3. Validate and publish a routing policy
4. Enable live traffic when the group is ready

This order is reflected directly in the page so first-time setup is easier to follow.

## Creating a model group

Click **Create Model Group** and start with only the required fields:

- **Group Key** — Stable identifier used by clients, bindings, and policies
- **Workload Type** — `chat`, `embedding`, `image_generation`, `audio_speech`, `audio_transcription`, or `rerank`
- **Display Name** — Optional human-readable label

Optional defaults such as preferred routing strategy, default prompt fallback, and immediate live-traffic enablement are grouped under advanced settings.

## Detail page flow

The detail page is organized as an ordered workflow:

### 1. Basics

Use this section to:

- Rename the group
- Change workload type
- Set a preferred routing strategy
- Enable or disable live traffic
- Attach an optional default prompt fallback

### 2. Add Models

Start by searching for compatible deployments and selecting them from the searchable picker.

Advanced member controls are available when needed:

- **Weight** for weighted traffic splits
- **Priority** for ordered fallback setups
- **Manual deployment ID entry** when the deployment is not discoverable from search
- **Eligible for routing** toggle to keep a member attached but temporarily inactive

### 3. Routing Policy

Use the guided policy editor for the first working policy. The usual flow is:

1. Choose the routing mode
2. Select which members are included
3. Set timeout and retry behavior if needed
4. Validate the policy
5. Save a draft or publish

Advanced JSON editing is available for cases that need extra conditions or fields beyond the guided form.

### 4. History and Rollback

Use the history section to review policy versions and roll back to a previous published policy when needed. This is a maintenance workflow, not part of initial setup.

## UI guidance

The page includes:

- Summary cards for members, policy state, and traffic state
- A setup-progress checklist so operators know what to do next
- Empty states when the group does not yet have members
- Disabled policy actions until the group has at least one member

## Related docs

- [Managing Models](models.md)
- [Routing & Failover](../features/routing.md)
- [Model Deployments](../configuration/models.md)
