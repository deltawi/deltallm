# How DeltaLLM releases are built and verified

Every DeltaLLM release image is built by GitHub Actions from a tagged commit in
`deltawi/deltallm`, tested, and then signed. You can check that an image you are about to run
came from that pipeline and has not been changed since.

## What each release publishes

| Artifact | Where | Signed with |
| --- | --- | --- |
| `deltallm/deltallm:<tag>` and `deltallm/deltallm:<tag>-presidio` | Docker Hub (multi-platform, `linux/amd64` and `linux/arm64`) | Keyless cosign signature and a GitHub build provenance attestation |
| SPDX SBOM per platform | Inside the image index on Docker Hub | Covered by the image signature |
| BuildKit SLSA provenance per platform (`mode=max`) | Inside the image index on Docker Hub | Covered by the image signature |
| Helm chart `deltallm-<version>.tgz` | `https://deltawi.github.io/deltallm` | GitHub build provenance attestation |

Signing is keyless: there is no long-lived signing key to leak. The release workflow requests a
short-lived certificate from [Sigstore](https://www.sigstore.dev/) using its GitHub OIDC identity,
and the signature is recorded in the public Rekor transparency log. The certificate names the
exact workflow file and release tag that produced the image.

Releases published before this pipeline was added (v0.3.12 and earlier) are not signed and have no
SBOM attached.

## Verify an image signature

Install [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) v3 or later, then
run, replacing the tag with the release you use:

```bash
TAG=v0.3.13
cosign verify "docker.io/deltallm/deltallm:${TAG}" \
  --certificate-identity "https://github.com/deltawi/deltallm/.github/workflows/release-images.yml@refs/tags/${TAG}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The command exits non-zero unless the image digest was signed by the release workflow for that
exact tag. Verify the `-presidio` image the same way, with `"docker.io/deltallm/deltallm:${TAG}-presidio"`.

Pin what you verified. Resolve the tag to its digest once and deploy the digest:

```bash
docker buildx imagetools inspect "deltallm/deltallm:${TAG}" --format '{{ .Manifest.Digest }}'
```

In Helm, set `image.digest` to that value so a later tag move cannot change what runs.

## Verify build provenance with the GitHub CLI

The release also records a signed [SLSA provenance](https://slsa.dev/spec/v1.0/provenance)
attestation in GitHub. It states which repository, commit, and workflow built the image:

```bash
gh attestation verify "oci://docker.io/deltallm/deltallm:${TAG}" \
  --repo deltawi/deltallm \
  --signer-workflow deltawi/deltallm/.github/workflows/release-images.yml
```

The Helm chart package is attested the same way. Download it and verify the file:

```bash
VERSION=${TAG#v}
curl -fsSLO "https://deltawi.github.io/deltallm/deltallm-${VERSION}.tgz"
gh attestation verify "deltallm-${VERSION}.tgz" \
  --repo deltawi/deltallm \
  --signer-workflow deltawi/deltallm/.github/workflows/release-images.yml
```

## Read the SBOM and BuildKit provenance

Each platform image carries an SPDX SBOM listing the OS and Python packages inside it, and the
BuildKit provenance with the build inputs. Both sit in the signed image index, so the signature
check above also covers them.

```bash
# SBOM for one platform (SPDX JSON)
docker buildx imagetools inspect "deltallm/deltallm:${TAG}" \
  --format '{{ json (index .SBOM "linux/amd64").SPDX }}' > deltallm-sbom.spdx.json

# BuildKit SLSA provenance for one platform
docker buildx imagetools inspect "deltallm/deltallm:${TAG}" \
  --format '{{ json (index .Provenance "linux/amd64").SLSA }}'
```

Feed the SBOM to your scanner, for example `grype sbom:deltallm-sbom.spdx.json` or
`trivy sbom deltallm-sbom.spdx.json`.

## How the pipeline is built

1. A maintainer publishes a GitHub release for a `vX.Y.Z` tag. That starts
   `.github/workflows/release-images.yml`.
2. Both image variants are built for `linux/amd64` and `linux/arm64` and pushed by digest only,
   with no tag, together with the SBOM and provenance.
3. The pushed digest is pulled back and must pass the image contract checks and the Kubernetes
   migration, rollout, and pod-loss acceptance tests.
4. Only those tested digests get the release tags (`<tag>`, and `latest` for non-prereleases).
5. The `sign-images` job signs the same digests with cosign and records the GitHub provenance
   attestation. The Helm chart is published only after signing succeeds, and its package is
   attested before it is pushed to the chart repository.

Images are tagged a few minutes before the signing job finishes. If `cosign verify` reports no
signature right after a release is announced, wait for the release workflow run to complete.

The build itself is pinned too:

- Every third-party GitHub Action is pinned to a full commit SHA with its version in a comment.
  A test in the CI suite (`tests/test_release_supply_chain.py`) fails if a workflow uses a tag or
  branch instead.
- Dependabot opens grouped weekly pull requests to move those pins forward, so updates are
  reviewed like any other change.
- The Dockerfile pins its Node and Python base images and the Dockerfile syntax frontend by
  digest, installs Python dependencies from the locked `uv.lock` with `--frozen`, and runs as the
  non-root user `10001`.
- The release workflow token is read-only by default. Only the signing and chart jobs can request
  an OIDC token, and only the chart job can push to `gh-pages`.

To report a problem with a release or its signatures, follow the
[vulnerability reporting process](vulnerability-reporting.md).
