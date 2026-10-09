# Provider HTTP connection pool

Use the released `httpcore2[asyncio]==2.13.1` pool behind the shared HTTPX
provider client. Keep HTTPX request, response, timeout and exception contracts.
This removes repeated pool scans and duplicate HTTP/1.1 connection assignment
without increasing the configured connection budget.

## Evidence and alternatives

The 1,000 RPS diagnosis found high API CPU use in connection assignment while
Redis command execution remained fast. An isolated idle-pool check also found
quadratic scan growth in the locked `httpcore==1.0.9` implementation.
The maintained pool includes fixes for
[repeated scans](https://github.com/pydantic/httpx2/pull/974) and
[duplicate assignment](https://github.com/pydantic/httpx2/pull/1075).
These dependency benchmarks do not establish gateway capacity.

A larger Redis or HTTP pool does not remove this work. An application-owned
pool, dependency monkeypatch or copied pool algorithm would make DeltaLLM own
complex connection state and cancellation. A full migration to HTTPX2 would
change provider, control, accounting and SDK contracts at once. The public
HTTPX transport seam limits this change to provider connections.

## Ownership and capacity

`src/bootstrap/infrastructure.py` creates one shared provider client and owns
its shutdown. `src/upstream_http.py` supplies the unchanged timeout and capacity
settings and environment proxy mounts. `src/providers/http_transport.py`
translates public types only. The dependency owns connection reservation,
expiry, reuse and cancellation. No dependency internals are replaced.

Each direct or proxy pool keeps the configured maximum connections, maximum
idle connections and keepalive expiry. Defaults stay at 500 connections and
100 idle connections per pool. The deployment capacity calculation counts the
same proxy pools as before. Admission and total request deadlines still bound
queued work. HTTP/2 stays disabled and transport retries stay at zero.

## Failure and compatibility contracts

Translate transport failures to their most specific HTTPX exception. In
particular, pool exhaustion remains local capacity failure rather than a
provider health failure. Do not translate cancellation into a network error.
Request extensions carry the existing phase timeouts and TLS hostname. Response
extensions, headers, streaming and the bounded provider error hook remain intact.
Closing a response releases its pool reservation; closing the client closes all
direct and proxy pools.

Use HTTPX SSL contexts to retain its certificate roots and CA environment
configuration instead of adopting the new dependency's operating-system trust
default. Keep the existing environment proxy and NO_PROXY parser in its current
bounded dependency seam. Control discovery keeps its separate pinned-origin
transport and zero-keepalive policy. Accounting HTTP and Prisma clients do not
change.

## Migration and rollback

Install the manifest and frozen lock together, then deploy a new immutable
image. No database or Redis migration is needed. Roll back the image to restore
the old provider transport. Do not roll back accounting migrations for this
change. If HTTPX adopts a released pool with these fixes, remove this adapter
and its direct dependency together after the same compatibility and load checks.

## Verification

Test actual connection reuse and finite pool timeouts, queued and active
cancellation, read failure, streaming close, error body bounds, proxy bypass and
TLS verification. Use a deterministic assignment-call test to guard against
duplicate connection assignment without a wall-clock performance assertion.
Run affected provider and application suites. Compare fixed-image constant
arrival runs on the same six-CPU disposable kind fixture. A short 1,000 RPS
diagnostic is not a ten-minute qualification certificate.
