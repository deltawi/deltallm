# Route Groups

Route Groups let you place multiple deployments behind one stable runtime target.

Use a route group when one public model name should:

- balance across several deployments
- fail over in a controlled way
- carry its own routing policy
- bind to a prompt at the group level

![Route Groups List](images/route-groups-list.png)

![Route Group Detail](images/route-group-detail.png)

## Quick Success Workflow

1. Create the route group shell
2. Add one or more member deployments
3. Keep the default routing behavior at first
4. Mark the group live
5. Use the generated call example to test traffic

For most teams, this is the right first path. You do not need an advanced policy on day one.

## What the Group Owns

A route group defines:

- a stable group key
- the workload type, such as chat or embeddings
- which deployments are members
- whether the group is live
- optional prompt binding
- optional routing policy history and overrides

Route groups are also callable targets. Their runtime visibility is governed through the same callable-target bindings and scope policies used for public model names.

## What the List Page Shows

- group key and display name
- workload type
- whether the group is live
- member count
- current routing state

## What the Detail Page Lets You Do

- edit the basic group metadata
- add and remove member deployments
- see the current usage example for calling the group
- bind a prompt
- inspect and publish routing policy changes

## When You Need an Advanced Policy

Start with the default behavior unless you need one of these:

- ordered failover
- weighted traffic splits
- a specific routing strategy
- a draft, publish, rollback, or simulation workflow for routing changes

## Routing Policy Basics

A route-group policy should stay small and explicit.

In practice, that means:

- choose one routing strategy
- optionally override member `enabled`, `weight`, or `priority`
- optionally override the group timeout
- optionally override retry behavior

The supported policy fields today are:

- `mode`
- `strategy`
- `members`
- `timeouts.global_ms` or `timeouts.global_seconds`
- `retry.max_attempts`
- `retry.retryable_error_classes`

## Policy Modes

The UI can present policy modes as shortcuts:

- `weighted`: use weighted traffic splitting
- `fallback`: use ordered primary and standby behavior

How they behave:

- `weighted` maps to the `weighted` strategy if you do not set a strategy explicitly
- `fallback` maps to `priority-based-routing` if you do not set a strategy explicitly

Do not plan around these as live runtime modes yet:

- `conditional`
- `adaptive`

Those are not active route-policy behaviors in the runtime today.

## Which Policy Should I Use?

Choose by goal:

- use `simple-shuffle` when the deployments are roughly equal
- use `weighted` when you want a controlled traffic split
- use `priority-based-routing` or `fallback` when one deployment should be primary
- use `least-busy` when you are smoothing burst traffic
- use `latency-based-routing` when end-user latency matters most
- use `cost-based-routing` when cost matters most
- use `rate-limit-aware` when provider limits are the problem

For most teams, one of these three is enough:

- `simple-shuffle`
- `weighted`
- `priority-based-routing`

## Simple Policy Examples

Weighted rollout:

```json
{
  "mode": "weighted",
  "members": [
    {"deployment_id": "dep-primary", "weight": 9},
    {"deployment_id": "dep-canary", "weight": 1}
  ]
}
```

Primary plus standby:

```json
{
  "mode": "fallback",
  "members": [
    {"deployment_id": "dep-primary", "priority": 0},
    {"deployment_id": "dep-standby", "priority": 1}
  ]
}
```

Latency-sensitive route group:

```json
{
  "strategy": "latency-based-routing",
  "timeouts": {"global_seconds": 45},
  "retry": {"max_attempts": 1}
}
```

Quota-aware route group:

```json
{
  "strategy": "rate-limit-aware"
}
```

## Member Overrides

Member overrides let the group behave differently without editing the underlying deployment definition.

- `enabled`: take a member out of rotation without removing it
- `weight`: change traffic share for `weighted`
- `priority`: control order for `priority-based-routing` or `fallback`

## Good Operating Pattern

Use this workflow:

1. Create the route group and add members
2. Start with `simple-shuffle` or `weighted`
3. Simulate before publishing policy changes
4. Publish only after the selection summary looks right
5. Check `/health/deployments` and `/health/fallback-events` after rollout

The simulation view is especially useful for:

- checking weighted splits
- confirming fallback order
- confirming prompt-derived tag routing

## Prompt Binding

Prompt binding belongs on the route group because the group decides where a prompt is applied.

That means:

- Prompt Registry defines the prompt template and its versions
- Route Groups decide which prompt is active for live traffic

If a prompt is bound, the usage example on the page should include the variables needed to call it correctly.

## Related API Surface

The backend exposes route-group endpoints for:

- listing and editing groups
- managing group members
- reading and publishing routing policy
- validating and simulating policy changes

See [Admin Endpoints](../api/admin.md) for the route-group API reference.

## Related Pages

- [Models](models.md)
- [Prompt Registry](prompt-registry.md)
- [Routing & Failover](../features/routing.md)
