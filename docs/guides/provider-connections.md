# Tune provider connections

DeltaLLM keeps a pool of connections to model providers. The default settings are a good starting point for most installations. Change them only when a load test or production measurements show a clear limit.

## Start with the defaults

Run a representative load test before changing the connection settings. Include streaming requests if your users rely on streaming, because each stream holds a connection until it finishes.

Watch for:

- `upstream_pool_timeout` errors
- rising provider response time
- high pod CPU or memory use
- file descriptor limits
- pressure on your NAT gateway or outbound proxy

## Work out the total limit

The maximum connection setting applies to each process, not the whole cluster:

```text
connections per process × processes per pod × pod count
```

Check that providers and your outbound network can accept that total before increasing it. Adding pods and raising the per-process limit at the same time can create a much larger increase than expected.

## Make one measured change

Change one setting at a time, repeat the same load test, and compare the result. Keep the pool wait time short enough that overloaded requests fail clearly instead of waiting unnoticed inside the gateway.

These settings are read when DeltaLLM starts. Restart the processes or use a rolling Kubernetes deployment after a change. Watch existing streams while old pods drain.

For setting names, sizing examples, timeout behavior, and failure details, see the [provider connection tuning reference](../deployment/upstream-http.md).
