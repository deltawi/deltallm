#!/usr/bin/env bash
# A `pgbouncer` command that runs a pinned PgBouncer container image.
#
# CI installs PgBouncer with apt. When Ubuntu's package servers cannot be
# reached (on 2026-11-11 runners could not resolve them for hours), the CI job
# installs this script as `pgbouncer` instead. It accepts the two forms the job
# uses: `pgbouncer --version` and `pgbouncer -d <config>`. The image comes from
# ghcr.io, which the runners reach through GitHub.
set -euo pipefail

IMAGE="ghcr.io/cloudnative-pg/pgbouncer:1.25.2-202609221033-trixie@sha256:b15c428057d1ffbc19fb70496911a5f1a47066dcc1315bf794f4e85ade29e2e5"
NAME="pgbouncer-ci"
# The job keeps its config, log and pid files here. The image runs as its own
# pgbouncer user, so that user must be able to write the directory.
WORKDIR="/tmp/pgbouncer-ci"
PORT="6432"

mkdir -p "$WORKDIR"
chmod 0777 "$WORKDIR"
run=(docker run --network host -v "${WORKDIR}:${WORKDIR}")

if [ "${1:-}" != "-d" ]; then
  exec "${run[@]}" --rm "$IMAGE" "$@"
fi
shift
# pgbouncer -d forks into the background, which would end the container, so
# run it in the foreground inside a detached container instead.
docker rm -f "$NAME" >/dev/null 2>&1 || true
# A pid file left by an earlier container names PID 1, which is always alive in
# the new container, so PgBouncer would refuse to start. Remove it.
if [ "$#" -gt 0 ] && [ -f "${!#}" ]; then
  pidfile="$(sed -n 's/^[[:space:]]*pidfile[[:space:]]*=[[:space:]]*//p' "${!#}" | tail -n 1)"
  if [ -n "$pidfile" ]; then
    rm -f "$pidfile"
  fi
fi
"${run[@]}" -d --name "$NAME" "$IMAGE" "$@" >/dev/null
for _ in $(seq 1 60); do
  if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    break
  fi
  sleep 0.5
done
echo "PgBouncer did not start listening on port ${PORT}" >&2
docker logs "$NAME" >&2 || true
exit 1
