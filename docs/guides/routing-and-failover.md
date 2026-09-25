# Route traffic and handle failures

Use more than one deployment when you need extra capacity, a backup provider, or a controlled
traffic split. Keep one public name so applications do not need to know which deployment answered.

## Before you start

- Connect and test one model first.
- Add a second healthy deployment that supports the same type of request.
- Give each deployment a distinct public name, such as `support-chat-primary` and
  `support-chat-backup`.

The route-group key becomes the public name that your application calls. Avoid reusing an existing
public model name unless you intend the route group to replace it. A live route group takes control
of a matching name, even when none of its members are healthy.

## Choose a routing approach

| Goal | Strategy |
| --- | --- |
| Spread traffic without extra tuning | `simple-shuffle` |
| Send a planned percentage to each deployment | `weighted` |
| Use one deployment first and another as backup | `priority-based-routing` |
| Prefer the deployment with fewer active requests | `least-busy` |
| Avoid provider rate limits | `rate-limit-aware` |

Use `simple-shuffle` unless you have a specific reason to choose another strategy.

## Create a route group

1. Open **AI Gateway**, then **Route Groups**.
2. Select **Create Group**.
3. Enter a stable group key, such as `support-chat`.
4. Add the healthy deployments that should receive traffic.
5. Choose a strategy from the table above.
6. For a weighted rollout, give the primary deployment weight `9` and the canary weight `1` for
   an approximate 90/10 split.
7. For primary-and-backup routing, give the primary priority `0` and the backup priority `1`.
8. Test the policy in the simulator, publish it, and mark the group live.

If your application key has limited model access, allow it to use `support-chat` before testing.
See [Create a key for your application](../getting-started/first-api-key.md).

## Test the route

```bash
export BASE_URL="http://localhost:4002"
export API_KEY="YOUR_APPLICATION_KEY"
```

Use `http://localhost:8000` for the manual development setup.

Send several requests to the route-group key:

```bash
curl "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "support-chat",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

Check deployment health and recent fallback events before sending production traffic.

## Next steps

- [Route Groups in the Admin UI](../admin-ui/route-groups.md)
- [Routing and failover reference](../features/routing.md)
- [Routing configuration](../configuration/router.md)
