# Route Groups

Route Groups are the runtime surface for grouping deployments behind one stable model key.

![Route Groups List](images/route-groups-list.png)

![Route Group Detail](images/route-group-detail.png)

## Recommended flow

1. Create the group shell
2. Add member deployments
3. Keep the default shuffle unless you truly need an override
4. Optionally bind a prompt
5. Turn on live traffic

This is the intended first-run path in the current UI.

## What the list page tells you

- Group key and display name
- Workload type
- Effective routing state
- Member count
- Whether the group is live

## What the detail page covers

- **Basics**: name, workload type, and live-traffic toggle
- **Members**: eligible deployments for the group
- **How to call this group**: generated curl example based on the group key and prompt variables
- **Advanced**: routing override, prompt binding, and policy history

## Prompt binding

Prompt binding now lives on the route-group page. If a prompt is bound, the usage example includes the required prompt variables so the request example is immediately usable.

## Routing policy

Groups work with default shuffle out of the box. Advanced routing override is only needed for:

- ordered fallback
- weighted splits
- explicit strategy control
- advanced JSON-only policy fields
