#!/usr/bin/env bash
# Install the pinned Trivy scanner at /usr/local/bin/trivy.
#
# The binary is downloaded from the GitHub release and checked against the
# sha256 below (from trivy_<version>_checksums.txt), instead of using a
# third-party action. Bump the version and checksum together.
set -euo pipefail

TRIVY_VERSION="0.76.2"
TRIVY_SHA256="bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf"
ARCHIVE="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
URL="https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/${ARCHIVE}"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 --max-time 120 "$URL" -o "$workdir/$ARCHIVE"
echo "${TRIVY_SHA256}  $workdir/$ARCHIVE" | sha256sum -c -
tar -xzf "$workdir/$ARCHIVE" -C "$workdir" trivy
sudo install -m 0755 "$workdir/trivy" /usr/local/bin/trivy
trivy --version | grep -q "Version: ${TRIVY_VERSION}"
