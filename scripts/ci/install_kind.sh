#!/usr/bin/env bash
# Install the pinned kind binary at /tmp/kind-linux-amd64.
#
# GitHub release downloads sometimes return 504 for many minutes. Retry them,
# then build the same version from the Go module proxy, which go verifies
# against the public checksum database.
set -euo pipefail

KIND_VERSION="v0.31.0"
KIND_SHA256="eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa"
TARGET="/tmp/kind-linux-amd64"
URL="https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64"

if curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 --max-time 90 "$URL" -o "$TARGET" \
  && echo "${KIND_SHA256}  ${TARGET}" | sha256sum -c -; then
  chmod +x "$TARGET"
  exit 0
fi

echo "::warning::kind release download failed; building ${KIND_VERSION} from the Go module proxy"
rm -f "$TARGET"
gobin="$(mktemp -d)"
GOBIN="$gobin" GOFLAGS=-mod=mod go install "sigs.k8s.io/kind@${KIND_VERSION}"
install -m 0755 "$gobin/kind" "$TARGET"
"$TARGET" version | grep -q "${KIND_VERSION}"
