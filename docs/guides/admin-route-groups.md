# Manage route groups

A route group puts several deployments behind one public name. Use one when you need planned
traffic splitting, a standby deployment, or a routing policy that can be tested before release.

## Create a route group

You need platform administrator access and at least one healthy deployment of the same request type.

1. Open **AI Gateway**, then **Route Groups**.
2. Select **Create Group**.
3. Enter the public group key that applications will call.
4. Add the deployments that may receive traffic.
5. Start with the default routing behavior unless you need a weighted or primary-and-backup policy.
6. Test the result in the simulator.
7. Publish the policy and mark the group live.

A live route group takes control of its group key. Do not reuse an existing public model name unless
you intend the route group to replace it.

## Change a live group

Test policy changes in the simulator before publishing them. After publication, send a test request
through the group key and confirm the selected deployment and any expected fallback behavior.

Make sure the organization, team, and application key can use the group key. Access to a member
deployment does not automatically grant access to the route group.

## Learn more

- [Route traffic and handle failures](routing-and-failover.md)
- [Route-group administration details](../admin-ui/route-groups.md)
- [Routing configuration](../configuration/router.md)
