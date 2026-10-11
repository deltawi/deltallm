# Reproduce the Issue 320 RPS tests

Use this guide with the [results report](issue-320-rps-report.md).
The stored evidence is local. Do not assume that raw bundles are included in a PR.

The original upgrade is merged. Its source branch was
`codex/issue-320-main-integration`. The release follow-up in
[PR #352](https://github.com/deltawi/deltallm/pull/352) is also merged.
The internal accounting and Playground corrections are in
[PR #353](https://github.com/deltawi/deltallm/pull/353). PR #351 is merged.
Use the fixed candidate
commit and evidence recorded there for current qualification. The following
historical bundles do not qualify the later merged release image.

Current app evidence uses clean `1045d5a9` and image `03f95d3b3068`. All required
CI and installed main-app checks passed. The selected 60-second 500 RPS
diagnostic passed. Both approved ten-minute 500 RPS preflights then failed;
neither reached the final four stages or recovery. The first uses harness
`7b564124` with twelve CPUs and 12 GiB RAM. The second uses harness `4b38075a`
with twelve CPUs and 8 GiB RAM. Both use the same app image and limits. Keep
these failed results separate from the earlier complete qualification.

Current qualification uses the normal runner without `--diagnostic-rates`.
It checks ordinary clients before load, then runs a fixed 60-second same-rate
warm-up before each measured stage. The initial 50, 100, and 200 RPS stages use
`--short-seconds`; the initial 500 RPS observation uses 600 seconds. All four
final stages use 600 seconds. Warm-up samples and exact charges are checked
separately and are not included in measured latency or success counts. No
cooling pause occurs inside a measured stage. The runner saves samples and
requires accounting to drain between stages. It ends with native process-loss
and same-image Helm rollout checks. Keep all gates active and retain failed
attempts. A selected-tier diagnostic is not a release qualification.

The approved `--high-rate-preflight` option replaces only the initial low-rate
short screens. It first measures 500 RPS for 600 seconds, then measures 50, 100,
200, and 500 RPS for 600 seconds each. Every stage keeps the 60-second same-rate
warm-up, original acceptance limits, exact charges, and complete drain. Native
process-loss and same-image Helm rollout checks still run after the full series.
Do not combine this option with `--diagnostic-rates`. The manifest records each
stage and duration. The default sequence remains unchanged.

The current native runner declares and records PostgreSQL fixture resources
and live settings in `database-fixture.json`. Its limits are four CPUs and four
GiB, with 512-MiB shared buffers and a four-GiB WAL limit. It verifies that
`fsync`, synchronous commit, full-page writes, and autovacuum remain enabled.
It also declares and verifies `wal_compression=lz4` on the pinned PostgreSQL
build. Older harness revisions used the default compression setting. Use the
recorded source and database profile for each result; do not change old results.
The connection bound stays 1,000. These are test settings, not production
minimums. Earlier source bundles retain their earlier database allocations.
The current fixture also verifies a 256-MiB memory-backed `/dev/shm` volume.
Earlier fixed image `d62715d0` uses runtime `609bad89` and forward migration
`20261010190000_accounting_append_cold_plans`. Current diagnostics use twelve
VM CPUs and 12 GiB RAM. A selected-tier pass does not qualify all four rates.
The current reporting image adds migrations `20261010213000` and `20261010214000`.
Use its own recorded source and image identity; do not use the earlier image
to test those changes. Keep the 60-second warm-ups, ten-minute stages, retained
history, exact financial checks, and native recovery gates unchanged.

The current native Helm overlay checks startup and readiness each second.
It keeps two positive readiness observations, 300-second startup grace,
15-second readiness failure tolerance, and the existing liveness checks.
The API's five-second admission-monitor startup deadline does not change.
Retain initial process status and previous logs before cold client checks.
An initial retry is a functional readiness result, not a load warm-up result.

The native load client now uses an explicit four-second idle connection expiry.
This is shorter than the installed server's five-second idle timeout. The
standalone load command keeps its five-second default and accepts the bounded
`--keepalive-expiry` option. Request deadlines, connection counts, arrival rates,
and stage gates do not change. The client does not retry POST requests.

The native qualification gateway also sets
`upstream_http_keepalive_expiry_seconds=4`. Its fixed provider explicitly uses
`--timeout-keep-alive 5`. Production defaults remain unchanged. Both network
directions need a client idle limit below the receiving server or proxy limit.
No provider request retry is added. Use the saved configuration for older runs;
do not change an older result to this new profile.

The earlier `613fc1ca` campaign failed final 100 RPS after initial 500 RPS passed.
Its original evidence is retained in `qualification-final-upstream-idle-12cpu`.
That source still used thirty-second internal accounting connection expiry.
The accounting follow-up uses four seconds below the managed server's five
seconds, with no POST retry or longer deadline. It also records fixed failure
categories and HTTP status codes without exception text, signatures, or URLs.
Use a new clean source-matched image to test that correction. Do not change the
older image identity or treat its failed campaign as passing qualification.

Transport failures retain fixed failure-phase and reason codes, connection-reuse
status, and available phase times. Unknown reasons remain unknown. No exception
message, key, or response body enters these diagnostic fields. A failed warm-up
or measured stage saves bounded server logs before process-loss or rollout
checks replace the original pods. Retain those logs with the raw samples.

## Current preflight evidence bundle

The local bundle `artifacts/release-readiness-20261011-preflight/` retains both
new failed preflights, their raw samples, host traces, financial state, installed
function checks, and current CI. It also retains the preceding app and selected
load evidence. Its index preserves all earlier stage decisions. The bundle
includes the exact image, clean harness history, fixed tools, and a reproduction
wrapper. No complete current-image qualification is present.

Use a fresh checkout of `4b38075a1763f67e7cad249518ba98b00cad508f`. This harness
contains only test and documentation changes after the app image commit.
The tested Python runtime hash remains `91f73195c2a360408f7d4ceb8df9d5f0d41f3fdcfba38e6b8a3629f6b6fa391b`.
The image ID remains
`sha256:03f95d3b30685c2ce3088ee262423847a0c350eebe3440afeb65a78dbfce3f26`.
Do not rebuild the image and claim the same artifact identity.

```sh
task_preflight_bundle=/absolute/path/to/artifacts/release-readiness-20261011-preflight
task_preflight_checkout=/absolute/path/to/new/preflight-reproduction
python3 "$task_preflight_bundle/tools/export_qualification_evidence.py" \
  --output "$task_preflight_bundle" --verify
git bundle verify "$task_preflight_bundle/current-source.bundle"
git clone --no-checkout "$task_preflight_bundle/current-source.bundle" "$task_preflight_checkout"
git -C "$task_preflight_checkout" checkout --detach 4b38075a1763f67e7cad249518ba98b00cad508f
cd "$task_preflight_checkout"
uv sync --frozen --extra dev --python 3.11.13
uv run --frozen prisma generate --schema=prisma/schema.prisma
export DOCKER_CONTEXT=your-idle-dedicated-docker-context
export PREFLIGHT_VM_MEMORY_GIB=8
export PREFLIGHT_EVIDENCE=/absolute/path/to/new/preflight-evidence
bash "$task_preflight_bundle/reproduce-preflight.sh"
```

Declare twelve VM CPUs and either 8 or 12 GiB RAM. Set
`PREFLIGHT_VM_MEMORY_GIB` to that value. Do not stop unrelated workloads to
make this setting fit. Check physical-host headroom before starting. The
wrapper rejects a dirty checkout, running containers, or a mismatched image.
It runs the approved preflight schedule with all unchanged gates. The saved
tools are for macOS arm64; use the pinned versions for another host. The
two original environments and their outcomes remain separate records.

## Earlier complete release qualification bundle

The local bundle `artifacts/release-readiness-20261010/` retains the passing
`qualification-final-client-idle-12cpu` sequence and all earlier attempts.
Its `all-runs.json` keeps original decisions and raw checksums. Warm-ups are
listed separately. `checksums.json` also covers source, exact image, tools,
functional checks, financial records, maintenance, recovery, and CI evidence.

Use a fresh clean checkout of harness `8a98ce73`. The tested runtime is
`c70d63cd`, with image ID
`sha256:8c9e5e246361f05904420cbddf600ebb0cd263761dd20e83b4ebb10dcdd882d9`.
The bundle includes that image. A rebuild has a new identity and needs its own
image and load checks. Later documentation-only commits do not change the tested
runtime or harness; the bundle retains those commits separately.

```sh
task_release_bundle=/absolute/path/to/artifacts/release-readiness-20261010
task_release_checkout=/absolute/path/to/new/release-reproduction
python3 "$task_release_bundle/tools/export_qualification_evidence.py" \
  --output "$task_release_bundle" --verify
git bundle verify "$task_release_bundle/source-history.bundle"
git clone --no-checkout "$task_release_bundle/source-history.bundle" "$task_release_checkout"
git -C "$task_release_checkout" checkout --detach 8a98ce734f2f2338613bf629f16f14017a6d818e
cd "$task_release_checkout"
export DOCKER_CONTEXT=your-idle-dedicated-docker-context
export PATH="$task_release_bundle/tools:$PATH"
export RELEASE_KIND="$task_release_bundle/tools/kind"
export RELEASE_STARTUP_RECORDER="$task_release_bundle/tools/record_native_startup.py"
export RELEASE_IMAGE_ARCHIVE="$task_release_bundle/image/deltallm-reporting-corrected.tar"
export RELEASE_EVIDENCE=/absolute/path/to/new/release-evidence
bash "$task_release_bundle/reproduce-client-idle.sh"
```

The saved CLI binaries are for macOS arm64. On another host, install kind
v0.31.0, Helm v3.18.4, and kubectl v1.34.3 for that host, then set the tool paths.
The Docker VM must be idle, Linux arm64, with twelve CPUs and 12 GiB RAM. The
wrapper rejects a dirty checkout, running containers, or a mismatched exact image.
It checks non-root image startup, records initial process retries, then runs
the unchanged normal qualification and recovery. It does not retry POST requests
or stop unrelated services. Keep every new failed result.

## Latest provider-pool source

The 8 October revised-adapter bundle is `artifacts/http-pool-20261008/`.
Its `README.md` and `reproduction.json` retain all baseline and candidate results,
including failures. Use `current_source` for the revised adapter; the top-level
source fields describe the first adapter.

The bundle is stored under the primary repository's local artifact directory,
not inside every worktree. Choose its absolute path and a new checkout:

```sh
task_pool_bundle=/absolute/path/to/artifacts/http-pool-20261008
task_pool_checkout=/absolute/path/to/new/issue320-pool-reproduction
shasum -a 256 -c "$task_pool_bundle/source-checksums.sha256"
git bundle verify "$task_pool_bundle/source-thin.bundle"
git clone "$task_pool_bundle/source-thin.bundle" "$task_pool_checkout"
git -C "$task_pool_checkout" rev-parse HEAD
cd "$task_pool_checkout"
uv sync --frozen --extra dev --python 3.11
uv run --frozen prisma generate --schema=prisma/schema.prisma
```

Expected HEAD is `90e72985a4d8115f0b602c72373716e17a0b44ed`.
The checksum file records the original absolute artifact paths; if the bundle
moves to another computer, verify the four named files against the same recorded
SHA-256 values at their new paths. Do not change the expected checksums.

Build from the repository Dockerfile with a source revision label and run
`scripts/check_lifecycle_image.py` before load tests. Rebuilt image digests can
differ; retain the new build identity. The recorded runtime source hash is
`2d6832d719666d22f4c49b09458f6f6a84bb5ba0848c9300bc35c72c5a491031`.
It covers Python runtime, dependency declarations, lock and Prisma schema, not
migration SQL or the Dockerfile. The source bundle retains those inputs too.

Use an idle, dedicated Linux arm64 Docker VM with six CPUs and 12 GiB RAM,
kind v0.31.0, and unchanged fixture resources and acceptance limits:

```sh
uv run --frozen python -m tests.performance.run_native_qualification \
  --image your-source-matched-image \
  --output /absolute/path/to/new-500rps-evidence \
  --kind /absolute/path/to/kind-v0.31.0 \
  --short-seconds 30 --diagnostic-rates 500
```

For the sampled 1,000 RPS diagnosis, use the saved read-only probe with profiling
disabled and a new output path:

```sh
RPS_DIAGNOSTIC_CPU_PROFILING=0 PYTHONPATH=. uv run --frozen python \
  "$task_pool_bundle/probe.py" \
  --image your-source-matched-image \
  --output /absolute/path/to/new-1000rps-evidence \
  --kind /absolute/path/to/kind-v0.31.0 \
  --short-seconds 30 --diagnostic-rates 1000
```

The first revised 500 RPS run failed its growth check; its confirmation passed.
The revised 1,000 RPS diagnostic failed. Keep both passing and failed attempts,
their raw arrivals, metrics, financial snapshots and nonzero exit results.
Neither command is ten-minute release qualification. The normal qualification
command below still applies, with the newly built source-matched image.

## Earlier evidence

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

For historical source `7d4fbe71`, the normal schedule runs short 50/100/200/500
stages, then 600-second stages at each rate only if the short gates permit them.
The current release runner uses the longer initial 500 RPS observation and
fixed warm-up described above. Any initial failure stops the final series.
Preserve nonzero exits, manifests, stage JSON, raw samples, metrics,
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

No 50–500 RPS load test was repeated for the 7 October report. Reproduction
results must get their own source, image, environment, and evidence identities.

## New reviewed-image 1,000 RPS diagnostic

The 8 October diagnostic has a separate local bundle:
`artifacts/native-reviewed-1000rps-6cpu-12g-60s-20261008/`.
Its `reproduce.md` gives the complete restore, build, image-check, and test
commands. Do not use the older `source-history.bundle` for this newer source.

Restore `source-run2.bundle` into a fresh checkout and select
`b6f3cab423fa13bcd7994790aa8db5225756dee1`. This includes the reviewed runtime
and all additive migrations, plus the explicit short-only test harness option.
Use kind v0.31.0 and a dedicated Linux arm64 Docker VM with 6 CPUs and 12 GiB
RAM to match this run. The older report used 8 CPUs and 6 GiB RAM.

After building and checking an image from that clean source, run:

```sh
uv run --frozen python -m tests.performance.run_native_qualification \
  --image your-reviewed-image \
  --output /absolute/path/to/fresh-1000rps-evidence \
  --kind /absolute/path/to/kind-v0.31.0 \
  --short-seconds 60 --diagnostic-rates 1000
```

This runs only a 60-second 1,000 RPS diagnostic. It retains all existing limits
and gates and never becomes release-eligible. The recorded run failed; preserve
its nonzero exit and all failed checks. `run/` retains a setup-only attempt and
`run2/` retains the actual gateway stage. The application image was unchanged
between attempts. The source bundles, build metadata, image checks, raw samples,
resource counters, financial evidence, and environmental setup notes are saved
with both attempts. All generated evidence is outside the clean source checkout.

## Proof-copy comparison

Use `artifacts/accounting-proof-20261008/` for the newer comparison. Check
`source-archives.sha256`, then clone `source-before.bundle` and
`source-after.bundle` into separate fresh directories. Their HEADs must match
`c1a759db17cf217f4d10ef9aeb21614af9d47704` and
`9cbd2a32dc36fe516c57da4621e01392a55e8f1f`. Keep generated evidence outside
those clean source directories. Both bundles include all existing upgrade
migrations; the proof-copy change itself adds no migration.

Install the locked development dependencies and generate the Prisma client in
each checkout. Run the same proof benchmark in each:

```sh
uv sync --frozen --extra dev
uv run --frozen prisma generate --schema prisma/schema.prisma
uv run --frozen python -m tests.performance.benchmark_accounting_proofs \
  --iterations 100 --repeats 5
```

Build a distinct image from each clean source. Use a dedicated Docker VM with
6 CPUs and 12 GiB RAM, kind v0.31.0, and no running unrelated containers. Run
the same 30-second 500 RPS stage once per image. Then use the after image for
the 1,000 RPS stage:

```sh
uv run --frozen python -m tests.performance.run_native_qualification \
  --image your-source-matched-image \
  --output /absolute/path/to/fresh-evidence \
  --kind /absolute/path/to/kind-v0.31.0 \
  --short-seconds 30 --diagnostic-rates 500
```

For the after-image 1,000 RPS diagnostic, use a new evidence path and replace
`--diagnostic-rates 500` with `--diagnostic-rates 1000`. Do not change the resource
profile, connection limits, timeouts, or gates. These selected short stages never
become release-eligible. The saved before-500 and after-1000 runs exited nonzero;
the after-500 run exited zero. Preserve all failed checks and financial snapshots.

The test logs also record the full application and real-dependency checks.
Use `tests/realtime/sdk-requirements.txt` in an isolated Python 3.11 environment
and set `DELTALLM_REALTIME_SDK_PYTHON` to that interpreter for the four official
SDK cases. The Redis eviction check needs two separate empty Redis servers,
not two database numbers on one server. No user workloads were used as fixtures.

## Restore historical records

Historical measurements and stage-specific design notes are no longer loose files
in the current checkout. All passing and failed results remain in Git revision
`abd09e5ff33d102bcb65f78fd288617f260331cd`. Its full snapshot also retains the old
commands and their matching imports. Restore into a fresh directory, not over the
working checkout:

```sh
git fetch origin codex/issue-320-main-integration
task_history=$(mktemp -d /private/tmp/deltallm-history.XXXXXX)
git archive --format=tar --output "$task_history/source.tar" \\
  abd09e5ff33d102bcb65f78fd288617f260331cd
tar -xf "$task_history/source.tar" -C "$task_history"
```

The restored snapshot contains:

- `docs/project/benchmarks/`: the September baseline and overload comparisons.
- `docs/project/evidence/issue-320-20261007/`: all 87 stage decisions and checksums.
- The original long measure list, six stage-specific design notes and three
  diagnostic commands under `scripts/benchmarks/`.

A local archive of those 46 files is also retained at
`artifacts/pr-cleanup-20261009/historical-materials.tar.gz`. Its SHA-256 is
`a6a5ffd5f7691243d70c614ac451a679012318bee976dbd861f2be7ab8e1f6ed`.
The adjacent manifest records the original file hashes. Local ignored artifacts
are not published with the PR; the Git restore command does not depend on them.

The nine original closed-loop regression files remain in
`tests/fixtures/concurrency-history-20260911.tar.gz`. The test verifies the archive
hash, each original sample hash, allowed fields and the unchanged summary. This
fixture is not a current release certificate. New qualification still uses the
normal constant-arrival kind runner and retains its complete evidence.
