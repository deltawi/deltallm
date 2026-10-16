# Manage route groups

A route group puts several deployments behind one public name. Use one when you need planned
traffic splitting, a standby deployment, or a routing policy that can be tested before release.

--8<-- "videos/routing-and-failover.html"

## Create a route group

You need platform administrator access and at least one healthy deployment of the same request type.

1. Open **Gateway**, then **Route groups**.
2. Select **Create Group**.
3. Enter the public group key that applications will call.
4. In **Models**, select the deployments that may receive traffic.
5. Select **Create group**. DeltaLLM publishes the default routing: traffic is spread evenly,
   and a call that times out or is rate limited is retried on another model.
6. On the group page, select **Use this group**. Send a sample request, or open the group in the Playground.

To change how traffic is shared, open the **Routing** tab.
Select **Spread evenly**, **Primary & backup**, or **Custom split**, then select **Save changes**.

A live route group takes control of its group key. Do not reuse an existing public model name unless
you intend the route group to replace it.

## Change a live group

For a small change, use simple routing on the **Routing** tab. After a save, select **Undo** if the result is not correct.

For a larger change, open **Advanced routing**. Save a draft, run a dry run, then select **Review & publish**.
The review lists each change in plain words. **History** shows who published each version,
and **Restore as draft** copies an old version into the draft.

After publication, send a test request through the group key. Confirm the selected deployment and any expected fallback behavior.

Make sure the organization, team, and application key can use the group key. Access to a member
deployment does not automatically grant access to the route group.

## Learn more

- [Route traffic and handle failures](routing-and-failover.md)
- [Route-group administration details](../admin-ui/route-groups.md)
- [Routing configuration](../configuration/router.md)
