# Monitor DeltaLLM

Start with a small set of checks that tell you whether DeltaLLM is available, can reach its dependencies, and is serving requests successfully.

## Protect the monitoring endpoints

`/health/*` and `/metrics` are not authenticated. Expose only `/health/liveliness` through a public load balancer when a public probe is required. Keep readiness, deployment details, fallback events, and metrics on a trusted operator network.

## Add the basic checks

1. Use `GET /health/liveliness` to check that the process is running.
2. Use `GET /health/readiness` from inside the trusted network to check PostgreSQL and Redis.
3. Scrape `GET /metrics` from the private service address with Prometheus or a compatible system.
4. Open **Usage & Spend** in the Admin UI to check request and cost trends.

## Create the first alerts

Alert on conditions that need a person to act:

- readiness checks failing for several minutes
- a sustained rise in failed requests or response time
- repeated provider errors or failovers
- PostgreSQL or Redis becoming unavailable
- audit, spend, batch, or webhook work falling behind
- pods approaching their CPU, memory, or connection limits

Choose thresholds from normal traffic in your own environment. Test each alert in staging and make sure it reaches the correct on-call owner.

## Check after a deployment

After every release, confirm:

- liveness and readiness stay healthy
- a normal authenticated request succeeds
- streaming works if you use it
- request failures and response time stay near their normal levels
- background queues drain instead of growing
- usage and spending continue to appear in the Admin UI

For metric names, Prometheus examples, callbacks, and OpenTelemetry settings, see the [metrics and monitoring reference](../features/observability.md).
