# RPS results

Latest complete run: **11 October 2026**. All four rates passed.

| Rate | Duration | Successful / scheduled | p95 / p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 600 s | 30,000 / 30,000 | 22.86 / 26.15 ms | Passed |
| 100 RPS | 600 s | 60,000 / 60,000 | 23.53 / 28.09 ms | Passed |
| 200 RPS | 600 s | 120,000 / 120,000 | 28.16 / 41.67 ms | Passed |
| 500 RPS | 600 s | 300,000 / 300,000 | 95.15 / 179.39 ms | Passed |

Pass limits: p95 at most 150 ms, p99 at most 300 ms, zero errors, exact charges,
and complete accounting drain.

Setup: one gateway image with four API processes on a twelve-CPU, 12 GiB
Linux arm64 VM, with a synthetic one-token provider. These numbers describe that
test profile, not real-provider latency or a hardware minimum.

A newer image's 500 RPS check on 11 October 2026 did not pass, so the current
release image is not yet qualified. 1,000 RPS has not passed.

To run the tests yourself, see [Reproduce RPS tests](issue-320-rps-reproduction.md).
