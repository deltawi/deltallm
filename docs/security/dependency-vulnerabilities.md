# How dependency vulnerabilities are found and handled

DeltaLLM ships third-party code: Python packages, the admin UI's npm packages, and the Debian,
Node.js and Python layers of the container image. This page explains how maintainers learn about a
new advisory in any of them, and how quickly it is handled.

To report a vulnerability in DeltaLLM's own code, use the
[private reporting process](vulnerability-reporting.md) instead.

## How advisories reach the maintainers

| What can be affected | How it is detected | How the fix arrives |
| --- | --- | --- |
| Python packages (`pyproject.toml`, `uv.lock`) | Dependabot alerts; `pip-audit` in the **Security scan** workflow | Dependabot security update pull request |
| Admin UI and root npm packages (`package-lock.json` files) | Dependabot alerts; `npm audit` in the **Security scan** workflow, on `main` and on the UI lock file of the latest release | Dependabot security update pull request |
| Container base images (Debian packages) | Trivy image scan of the current build and of the published `latest` and `latest-presidio` images | Dependabot docker pull request that moves the pinned base image digest |
| Node.js and CPython runtimes in the image | `scripts/ci/check_image_runtimes.py` compares the image's versions with the Node.js and CPython security release feeds (Trivy does not see these binaries) | Dependabot docker pull request |
| GitHub Actions | Dependabot alerts | Dependabot pull request that moves the pinned commit SHA |
| Isolated SDK test pins (`tests/openai_sdk`, `tests/realtime`) | Dependabot alerts; `pip-audit` | Dependabot pull request |

The **Security scan** workflow (`.github/workflows/security-scan.yml`) runs:

- on every pull request that changes a dependency file. It fails when the pull request adds a
  dependency with a high or critical advisory (dependency review), or when the Python or production
  npm dependencies have a known vulnerability. The image scan only reports on pull requests;
- on every push to `main` that changes a dependency file, and daily. Image results are published
  to the repository's **Security** tab. The run fails, which notifies the maintainers, when the
  current build or a published release image has a critical vulnerability with a fix available,
  when its Node.js or CPython runtime is more than 14 days behind a security release, or when the
  latest release's UI packages have a high or critical advisory.

Routine version updates wait 7 days after a release is published before Dependabot proposes them,
so a compromised release is usually withdrawn before it reaches this repository. Security updates
are not delayed. `uv.lock` and the npm lock files pin exact versions with hashes, and images install the frozen lock,
so a new upstream release can only enter DeltaLLM through a reviewed pull request.

## Response targets

Maintainers aim for these times from the moment an advisory is known to affect DeltaLLM. They are
targets, not a contractual SLA.

| Case | Target |
| --- | --- |
| Critical, in the shipped image or runtime dependencies, fix available | Patched release within 2 days |
| High, in the shipped image or runtime dependencies | Patched release within 7 days |
| Medium or low | Next weekly dependency update |
| Build, test or CI tooling only | Next scheduled tooling update, unless actively exploited |
| Compromised (malicious) package release | Same day, see below |

When an advisory does not affect DeltaLLM, for example because the vulnerable function is never
called, the maintainers dismiss the alert with the reason, so the open list stays accurate.

### Compromised package releases

When a package DeltaLLM uses publishes a malicious release:

1. Check whether the bad version appears in any lock file, in a published image's SBOM, or in a
   CI log since it was published.
2. If it never entered a lock file, no action is needed beyond blocking the version.
3. If it was installed anywhere, treat every secret that environment could read as exposed:
   rotate the master key, the salt key, provider API keys and the database password for the
   affected deployments, then publish a release pinned to a safe version and say so in the release
   notes.

## What operators should do

- Run a pinned release and update to new patch releases promptly; release notes list security fixes.
- Scan the image you run. Each release image carries an SBOM, see
  [Verify release images](release-verification.md).
- If you build your own image, rebuild it after a base image update even when DeltaLLM itself has
  not changed.

## Notes for contributors

- Dependabot opens grouped weekly pull requests for minor and patch updates. Major versions are
  updated by hand in their own pull request.
- A Dependabot `uv` pull request does not regenerate `requirements.txt`. Run
  `uv run python scripts/check_container_contract.py --write` on the branch and push the result,
  or the container contract check fails.
- To accept a `pip-audit` finding that does not affect DeltaLLM, add `--ignore-vuln <ID>` in the
  workflow with a comment that says why and when to revisit it.
- Tools pinned outside a package manager (kind, Helm, uv, Trivy, the PgBouncer test image, the
  monitoring images in `deploy/kubernetes/monitoring/dependencies.json` and the spaCy model wheel)
  are not tracked by Dependabot. Bump them together in a periodic tooling update.
