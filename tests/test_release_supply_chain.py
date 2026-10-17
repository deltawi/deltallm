from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
# owner/repo[/path]@<40-hex commit> # vX.Y.Z  (the comment lets Dependabot and reviewers read it)
PINNED_USES = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>.+?)\s*$")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


def _steps_using(job: dict, action: str) -> list[dict]:
    return [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith(f"{action}@")]


def test_every_third_party_action_is_pinned_by_commit_sha() -> None:
    unpinned: list[str] = []
    for path in _workflow_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_LINE.match(line)
            if match is None:
                continue
            ref = match.group("ref")
            if ref.startswith("./"):
                continue
            if not PINNED_USES.match(ref):
                unpinned.append(f"{path.relative_to(REPO_ROOT)}:{number}: {ref}")

    assert unpinned == [], "Pin actions as owner/repo@<sha> # vX.Y.Z:\n" + "\n".join(unpinned)


def test_dependabot_keeps_action_pins_current() -> None:
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))

    ecosystems = {update["package-ecosystem"] for update in config["updates"]}
    assert "github-actions" in ecosystems


def test_release_build_attaches_sbom_and_provenance() -> None:
    job = _load("lifecycle-acceptance.yml")["jobs"]["image-and-rollout"]
    (build,) = _steps_using(job, "docker/build-push-action")

    assert build["with"]["sbom"] is True
    assert build["with"]["provenance"] == "mode=max"


def test_release_signs_and_attests_qualified_images_before_publishing_chart() -> None:
    workflow = _load("release-images.yml")
    jobs = workflow["jobs"]
    sign = jobs["sign-images"]

    assert workflow["permissions"] == {"contents": "read"}
    assert sign["needs"] == "docker"
    assert sign["permissions"]["id-token"] == "write"
    assert sign["permissions"]["attestations"] == "write"
    sign_script = next(step["run"] for step in sign["steps"] if step.get("name") == "Sign release images")
    assert 'cosign sign --yes "${IMAGE_NAME}@${STANDARD_DIGEST}"' in sign_script
    assert 'cosign sign --yes "${IMAGE_NAME}@${PRESIDIO_DIGEST}"' in sign_script
    attested = {step["with"]["subject-digest"] for step in _steps_using(sign, "actions/attest")}
    assert attested == {
        "${{ steps.digests.outputs.standard }}",
        "${{ steps.digests.outputs.presidio }}",
    }

    assert "sign-images" in jobs["helm-repo"]["needs"]
    assert _steps_using(jobs["helm-repo"], "actions/attest"), "the Helm chart package must be attested"
