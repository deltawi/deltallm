# Stream-completion commit profile

Fixed local ASGI provider, fake billing/Redis, 1 ms per provider hop. Each case is
10 RPS for 20 seconds, 200 requests. This is not a real-provider or production
durability/throughput benchmark. JSONL files retain raw request timing; summaries
retain offered/received rates, status counts, scheduling lag, TTFT and tails.
`dependency-summary.jsonl` additionally retains provider time, command counts,
logical selector billing operations and in-flight samples/slope.

From the worktree root with frozen development dependencies:

```sh
.venv/bin/python -m tests.performance.stream_commit_profile --before --output-dir /tmp/stream-before
.venv/bin/python -m tests.performance.stream_commit_profile --output-dir /tmp/stream-after
```

Run the two commands concurrently in separate terminals for the paired profile.
`--before` loads only the five stream-fix modules from pinned revision
`d41b66afe43154181f1c21f02b1ac9b372c2714a`; every other module/dependency uses the
same worktree. It never calls a live model or reads live credentials.

`initial-*` were sequential, with unrelated host activity during the after run.
`paired-*` ran concurrently using the original local helper. `checked-*` repeated
the pair with the checked-in pinned helper. Python 3.11.13 and the frozen lock
were identical. Retain all runs, including tail/TTFT regressions; the main
[readiness record](../../model-router-main-readiness.md#streaming-dependency-and-latency-evidence)
explains the results and limits. These samples do not instrument durable SQL or
claim a statistically significant speedup.
