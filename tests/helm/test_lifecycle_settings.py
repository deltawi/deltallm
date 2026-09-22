import json

import pytest
import yaml

from src.lifecycle_settings import LifecycleSettings
from tests.helm.test_ingress_settings import CHART, render

pytestmark = pytest.mark.helm


def documents(*args):
    result = render(*args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.mark.parametrize("overlay", ["values.yaml", "values-eval.yaml", "values-production.yaml"])
def test_every_profile_declares_valid_lifecycle_settings(overlay):
    docs = documents("-f", str(CHART / overlay))
    for item in docs:
        if item["kind"] == "ConfigMap" and "config.yaml" in item.get("data", {}):
            general = yaml.safe_load(item["data"]["config.yaml"])["general_settings"]
            assert set(LifecycleSettings.model_fields) <= set(general)
            LifecycleSettings.model_validate(general)
        if item["kind"] == "Deployment":
            pod = item["spec"]["template"]["spec"]
            assert pod["terminationGracePeriodSeconds"] == 90
            assert pod["containers"][0]["readinessProbe"]["successThreshold"] == 2
            assert pod["containers"][0]["readinessProbe"]["failureThreshold"] == 3
            assert "lifecycle" not in pod["containers"][0]


@pytest.mark.parametrize(
    "setting,reason",
    [
        ("config.general_settings.lifecycle_shutdown_seconds=79", "reserve every shutdown phase"),
        ("terminationGracePeriodSeconds=89", "exitMarginSeconds"),
        (
            "config.general_settings.readiness_probe_timeout_seconds=2,probes.readiness.timeoutSeconds=2",
            "probe timeout",
        ),
        ("dependencyCapacity.apiProcessesPerPod=2", "one process per pod"),
        ("command[0]=uvicorn", "python -m src.server"),
        ("args[0]=--reload", "custom process arguments"),
    ],
)
def test_invalid_managed_budgets_and_process_overrides_fail_render(setting, reason):
    result = render("--set", setting)
    assert result.returncode != 0
    assert reason in result.stderr


@pytest.mark.parametrize(
    "setting,reason",
    [
        ("managedLifecycle.enabled=false", "cannot disable"),
        ("config.general_settings.migration_mode=startup", "migration_mode=external"),
        ("image.tag=latest", "immutable release"),
        ("image.tag=", "immutable release"),
        ("migrationJob.enabled=false", "migration hook"),
        ("postgresql.enabled=true", "externally ready database"),
        ("migrationJob.activeDeadlineSeconds=300", "subprocess timeoutSeconds"),
        ("dependencyCapacity.retiringGenerations=1", "two retiring generations"),
        ("autoscaling.scaleDownStabilizationSeconds=89", "scaleDownStabilizationSeconds"),
        (
            "batchWorker.autoscaling.enabled=true,batchWorker.autoscaling.scaleDownStabilizationSeconds=89",
            "scaleDownStabilizationSeconds",
        ),
    ],
)
def test_incompatible_production_release_choices_fail_render(setting, reason):
    result = render("-f", str(CHART / "values-production.yaml"), "--set", setting)
    assert result.returncode != 0
    assert reason in result.stderr


def test_external_orchestration_still_requires_read_only_startup_verification():
    docs = documents(
        "-f",
        str(CHART / "values-production.yaml"),
        "--set",
        "migrationJob.enabled=false,migrationJob.external=true",
    )
    assert not any(doc["kind"] == "Job" for doc in docs)
    general = next(
        yaml.safe_load(doc["data"]["config.yaml"])["general_settings"]
        for doc in docs
        if doc["kind"] == "ConfigMap" and "config.yaml" in doc.get("data", {})
    )
    assert general["migration_mode"] == "external"


def test_preinstall_job_has_no_application_resource_or_service_dependency():
    digest = "sha256:" + "a" * 64
    docs = documents(
        "-f",
        str(CHART / "values-production.yaml"),
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "image.digest=" + digest,
    )
    job = next(doc for doc in docs if doc["kind"] == "Job")
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert "configMap" not in json.dumps(pod)
    container = pod["containers"][0]
    assert {env["name"] for env in container["env"]} == {"DATABASE_URL"}
    images = {
        doc["spec"]["template"]["spec"]["containers"][0]["image"]
        for doc in docs
        if doc["kind"] in {"Job", "Deployment"}
    }
    assert images == {"deltallm/deltallm@" + digest}
    labels = job["spec"]["template"]["metadata"]["labels"]
    for service in (doc for doc in docs if doc["kind"] == "Service"):
        assert not all(
            labels.get(key) == value for key, value in service["spec"]["selector"].items()
        )


def test_job_identity_changes_with_release_image_and_command_arguments():
    def name(*args):
        return next(
            doc["metadata"]["name"]
            for doc in documents("--set", "migrationJob.enabled=true", *args)
            if doc["kind"] == "Job"
        )

    assert name() != name("--set", "image.tag=another-release")
    assert name() != name("--set", "migrationJob.timeoutSeconds=100")
    assert len(name()) <= 63
