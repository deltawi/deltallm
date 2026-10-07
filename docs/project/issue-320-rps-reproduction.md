# Reproduce the Issue 320 RPS tests

Use this guide with the [results report](issue-320-rps-report.md).
The stored evidence is local. Do not assume that raw bundles are included in a PR.

## Saved files

The review bundle is `artifacts/issue320-evidence-20261007/`. It contains:

- `all-runs.json` and `all-runs.md`: all 87 saved stage decisions.
- `evidence/`: the four latest load campaigns, the accepted image records,
  and the final database verification attempts.
- `history-reports/`: the original reports and campaign manifests for all
  87 saved stages. Raw streams are retained only for the latest load campaigns.
- `source-history.bundle`: complete Git history through the tested checkpoint,
  plus the locally known `feature/issue-320-concurrency` reference.
- `tools/export_qualification_evidence.py`: standalone export and checksum tool.
- `checksums.json`: SHA-256 identities for the review bundle files.

The compact Git-tracked indexes contain results and checksums, not raw payloads
or host command transcripts. Review local diagnostics before external publication.
The old scratch archive and host-specific shell wrappers were removed from this
package. They can be recovered from local Trash. They are not needed to reproduce
the latest tests with the commands below.

Verify the review bundle without starting Docker or Kubernetes:

```sh
python3 artifacts/issue320-evidence-20261007/tools/export_qualification_evidence.py \
  --output artifacts/issue320-evidence-20261007 --verify
git bundle verify artifacts/issue320-evidence-20261007/source-history.bundle
```

The exporter needs Python 3.11 or newer.

## Prepare a clean test checkout

Choose a fresh checkout path and the absolute path of the saved review bundle.
Do not use the dirty report worktree for qualification.

```sh
task_bundle=/absolute/path/to/artifacts/issue320-evidence-20261007
task_checkout=/absolute/path/to/a/new/issue320-reproduction
git clone --no-checkout "$task_bundle/source-history.bundle" "$task_checkout"
git -C "$task_checkout" checkout --detach 7d4fbe713e4ae1fa295ce7af2ccd9e87a2ada62f
cd "$task_checkout"
uv sync --frozen --extra dev --python 3.11
uv run --frozen prisma generate --schema=./prisma/schema.prisma
```

The normal ladder used checkpoint `914693f1`. Its runner, chart, migrations,
lock, and runtime are identical to `7d4fbe71`; only two documents differ.
The bundle preserves both checkpoints.

Use Docker, Buildx, Helm, kubectl, uv, and **kind v0.31.0**. Select a dedicated
Docker environment with no running containers. For the recorded hardware
comparison, use a Linux arm64 VM with 8 CPUs and 6 GiB memory. Confirm the
effective values with `docker info`. Keep unrelated VM workloads off the
test host, with their owners' approval. Do not stop unrelated services by script.

Pinned fixture images are:

| Fixture | Pin |
| --- | --- |
| kind node | `kindest/node:v1.34.3@sha256:08497ee19eace7b4b5348db5c6a1591d7752b164530a36f855cb0f2bdcbadd48` |
| PostgreSQL | `postgres@sha256:724292da1f2e50bdccfc3302ce75bbba7f4a6076701b588cc795fcac65683550` |
| Redis | `redis@sha256:c6eabf748fc7a61dbb5a705c78bcf3d6377b1127a97d0ce965c11c44ba46896f` |

The source also pins monitoring dependencies. The stored `server-manifest.json`
and `fixture-values.yaml` files record the actual settings and resource limits.
The PostgreSQL and Redis fixtures use local test credentials only.

## Build and check the application image

If the original image is available, compare its identity with the report before
use. The local bundle records its identity but does not contain container layers.
Otherwise, build from the pinned clean source:

```sh
docker buildx build --platform linux/arm64 --load \
  --label org.opencontainers.image.revision=7d4fbe713e4ae1fa295ce7af2ccd9e87a2ada62f \
  --tag deltallm-native:issue320-reproduction-7d4fbe71 .
docker image inspect deltallm-native:issue320-reproduction-7d4fbe71
uv run --frozen python scripts/check_lifecycle_image.py \
  --image deltallm-native:issue320-reproduction-7d4fbe71 \
  --output artifacts/reproduction-image-checks
```

Use a fresh output path for each attempt. A source rebuild can have a different
image digest because external build inputs can change. Record the new identity;
do not claim that a rebuild is the original tested image. Use the commands in
this guide. The removed host shell wrappers relied on old machine paths and
temporary relay settings.

## Run the normal qualification

```sh
uv run --frozen python -m tests.performance.run_native_qualification \
  --image deltallm-native:issue320-reproduction-7d4fbe71 \
  --output artifacts/reproduction-normal \
  --kind kind --short-seconds 60
```

The runner creates and removes only its own two-node kind cluster and uses a
private kubeconfig. It checks source/image identity, primes native accounting,
and proves generator capacity before gateway arrivals. Gateway load runs inside
the cluster against four per-API services; it does not use a port-forward.

The normal schedule runs short 50/100/200/500 stages, then 600-second stages at
each rate only if the short gates permit them. Any short failure stops the long
series. Preserve nonzero exits, manifests, stage JSON, raw samples, metrics,
resource counters, dependency snapshots, money reconciliation, and drain results.
Do not edit a result or choose only passing attempts.

For a short diagnostic at 500 RPS, add `--diagnostic-rates 500` and a new output
path. It is not release qualification, even if the stage passes.

## Reproduce the long 500 RPS diagnostics

The private host helpers are preserved exactly in the review bundle. Each keeps
the same application image, request workload, gates, deadlines, and Pod resource
limits. Each selects one 600-second 500 RPS stage and adds a bounded read-only
78-field SQL cost sampler. Each declares that it is not qualification.

Standard-storage diagnostic:

```sh
PYTHONPATH="$task_checkout" uv run --frozen python \
  "$task_bundle/evidence/native-7d4fbe71-500-8cpu-6g-600s-sustained-queue-20261007/host-isolation/probe.py" \
  --image deltallm-native:issue320-reproduction-7d4fbe71 \
  --output artifacts/reproduction-long-500 \
  --kind kind --short-seconds 60 --diagnostic-rates 500
```

For the storage comparison, use the same command with helper path
`evidence/native-7d4fbe71-500-8cpu-6g-600s-data-volume-20261007/host-isolation/probe.py`
and a new output path. That helper adds an owned worker-node PostgreSQL data
directory mounted at `/var/lib/postgresql/data`, pins the database to that
worker, and uses a setup-only Recreate strategy. It verifies ext4 storage,
`fsync=on`, and `synchronous_commit=on`. Do not use it against an existing
cluster or database.

The SQL-cost short-repeat helper is also retained in its campaign directory.
It selects three 60-second 500 RPS stages on the same instance. These repeats
test retained history; they are not the canonical schedule.

## Export a new review package

Use the exporter from the report branch or the saved bundle. Point it at a
directory containing complete campaign folders. Give it a fresh output path.
The exporter preserves all stage decisions and rejects missing raw samples.

```sh
python3 "$task_bundle/tools/export_qualification_evidence.py" \
  --source artifacts/reproduction-history \
  --output artifacts/reproduction-review \
  --retain reproduction-normal reproduction-long-500
python3 "$task_bundle/tools/export_qualification_evidence.py" \
  --output artifacts/reproduction-review --verify
```

No load test was repeated for this report. Reproduction results must get their
own source, image, environment, and evidence identities.
