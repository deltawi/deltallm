from __future__ import annotations

import importlib
import io

import pytest


def _migration_state_module():
    return importlib.import_module("src.k8s_migration_state")


class _FakeKubernetesApiClient:
    def __init__(
        self,
        *,
        configmaps: list[dict[str, object] | Exception] | None = None,
        leases: list[dict[str, object] | Exception] | None = None,
        jobs: list[dict[str, object] | Exception] | None = None,
        create_side_effects: list[object] | None = None,
        replace_side_effects: list[object] | None = None,
    ) -> None:
        self._configmaps = list(configmaps or [])
        self._leases = list(leases or [])
        self._jobs = list(jobs or [])
        self._create_side_effects = list(create_side_effects or [])
        self._replace_side_effects = list(replace_side_effects or [])
        self.create_requests: list[dict[str, object]] = []
        self.replace_requests: list[dict[str, object]] = []
        self.get_configmap_calls = 0
        self.get_job_calls = 0

    def get_configmap(self, name: str) -> dict[str, object]:
        assert name
        self.get_configmap_calls += 1
        if not self._configmaps:
            raise AssertionError("unexpected configmap lookup")
        if len(self._configmaps) == 1:
            value = self._configmaps[0]
        else:
            value = self._configmaps.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def get_lease(self, name: str) -> dict[str, object]:
        assert name
        if not self._leases:
            raise AssertionError("unexpected lease lookup")
        value = self._leases.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def create_lease(
        self,
        name: str,
        *,
        holder_identity: str,
        renew_time: str,
        owner_reference: dict[str, str],
    ) -> dict[str, object]:
        assert name
        self.create_requests.append(
            {
                "name": name,
                "holder_identity": holder_identity,
                "renew_time": renew_time,
                "owner_reference": dict(owner_reference),
            }
        )
        if self._create_side_effects:
            side_effect = self._create_side_effects.pop(0)
            if isinstance(side_effect, Exception):
                raise side_effect
            return side_effect
        return _lease(revision=holder_identity, owner_uid=owner_reference["uid"], renew_time=renew_time, resource_version="1")

    def replace_lease(self, lease: dict[str, object]) -> dict[str, object]:
        self.replace_requests.append(lease)
        if self._replace_side_effects:
            side_effect = self._replace_side_effects.pop(0)
            if isinstance(side_effect, Exception):
                raise side_effect
            return side_effect
        return lease

    def get_job(self, name: str) -> dict[str, object]:
        assert name
        self.get_job_calls += 1
        if not self._jobs:
            raise AssertionError("unexpected job lookup")
        value = self._jobs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _configmap(
    *,
    uid: str = "uid-current",
    name: str = "release-migration-identity",
) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": "default",
            "uid": uid,
        },
    }


def _lease(
    *,
    revision: str | int,
    owner_uid: str | None = "uid-current",
    owner_name: str = "release-migration-identity",
    renew_time: str = "2026-04-18T00:00:00Z",
    resource_version: str = "12",
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "name": "release-migration-state",
        "namespace": "default",
        "resourceVersion": resource_version,
    }
    if owner_uid is not None:
        metadata["ownerReferences"] = [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": owner_name,
                "uid": owner_uid,
            }
        ]
    return {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": metadata,
        "spec": {
            "holderIdentity": str(revision),
            "renewTime": renew_time,
        },
    }


def _job(*, active: int = 0, succeeded: int = 0, failed: int = 0, failed_condition: bool = False) -> dict[str, object]:
    conditions: list[dict[str, str]] = []
    if failed_condition:
        conditions.append({"type": "Failed", "status": "True"})
    return {
        "status": {
            "active": active,
            "succeeded": succeeded,
            "failed": failed,
            "conditions": conditions,
        }
    }


def test_wait_for_migration_completion_returns_when_matching_lease_revision_is_already_ahead() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision=9, owner_uid="uid-current")],
    )

    module.wait_for_migration_completion(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="8",
        job_name="release-migrate-r8",
        stdout=stdout,
    )

    assert "confirms revision 9 for expected revision 8" in stdout.getvalue()
    assert client.get_configmap_calls == 1
    assert client.get_job_calls == 0


def test_wait_for_migration_completion_ignores_stale_owner_until_current_identity_records_state() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    sleeps: list[float] = []
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[
            _lease(revision=20, owner_uid="uid-old"),
            _lease(revision=9, owner_uid="uid-current"),
        ],
        jobs=[_job(active=1)],
    )

    module.wait_for_migration_completion(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="9",
        job_name="release-migrate-r9",
        timeout_seconds=30,
        period_seconds=1.5,
        sleeper=sleeps.append,
        monotonic=lambda: 0.0,
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "belongs to stale release identity uid uid-old" in output
    assert "status active=1 succeeded=0 failed=0" in output
    assert "confirms revision 9 for expected revision 9" in output
    assert sleeps == [1.5]
    assert client.get_job_calls == 1


def test_wait_for_migration_completion_fails_fast_when_the_job_has_failed() -> None:
    module = _migration_state_module()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision=9, owner_uid="uid-current")],
        jobs=[_job(failed=1, failed_condition=True)],
    )

    with pytest.raises(module.MigrationStateError, match="failed before revision 10 was recorded"):
        module.wait_for_migration_completion(
            client=client,
            identity_configmap_name="release-migration-identity",
            lease_name="release-migration-state",
            expected_revision="10",
            job_name="release-migrate-r10",
            monotonic=lambda: 0.0,
        )


def test_wait_for_migration_completion_fails_on_malformed_lease_state() -> None:
    module = _migration_state_module()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision="bad-revision", owner_uid="uid-current")],
    )

    with pytest.raises(module.MigrationStateError, match="holderIdentity must be a positive integer"):
        module.wait_for_migration_completion(
            client=client,
            identity_configmap_name="release-migration-identity",
            lease_name="release-migration-state",
            expected_revision="10",
            job_name="release-migrate-r10",
        )


def test_wait_for_migration_completion_fails_fast_on_identity_access_denied() -> None:
    module = _migration_state_module()
    client = _FakeKubernetesApiClient(
        configmaps=[module.KubernetesApiAuthorizationError("forbidden")],
    )

    with pytest.raises(module.MigrationStateError, match="Check the migration Role/RoleBinding"):
        module.wait_for_migration_completion(
            client=client,
            identity_configmap_name="release-migration-identity",
            lease_name="release-migration-state",
            expected_revision="10",
            job_name="release-migrate-r10",
        )


def test_wait_for_migration_completion_retries_when_identity_lookup_is_transient() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    sleeps: list[float] = []
    client = _FakeKubernetesApiClient(
        configmaps=[
            module.KubernetesApiTransientError("temporarily unavailable"),
            _configmap(uid="uid-current"),
        ],
        leases=[_lease(revision=10, owner_uid="uid-current")],
        jobs=[_job(active=1)],
    )

    module.wait_for_migration_completion(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="10",
        job_name="release-migrate-r10",
        timeout_seconds=30,
        period_seconds=1.25,
        sleeper=sleeps.append,
        monotonic=lambda: 0.0,
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "migration identity ConfigMap release-migration-identity is temporarily unavailable" in output
    assert "status active=1 succeeded=0 failed=0" in output
    assert "confirms revision 10 for expected revision 10" in output
    assert sleeps == [1.25]


def test_mark_migration_complete_creates_a_missing_lease_with_current_identity_owner() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[module.KubernetesApiNotFoundError("lease missing")],
    )

    module.mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="11",
        completed_at="2026-04-18T00:00:00Z",
        stdout=stdout,
    )

    assert "Recorded migration revision 11 in lease release-migration-state" in stdout.getvalue()
    assert client.create_requests == [
        {
            "name": "release-migration-state",
            "holder_identity": "11",
            "renew_time": "2026-04-18T00:00:00Z",
            "owner_reference": {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": "release-migration-identity",
                "uid": "uid-current",
            },
        }
    ]
    assert client.replace_requests == []


def test_mark_migration_complete_advances_lower_matching_lease_revision_with_replace() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision=11, owner_uid="uid-current", resource_version="42")],
    )

    module.mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="12",
        completed_at="2026-04-18T12:00:00Z",
        stdout=stdout,
    )

    assert "Recorded migration revision 12 in lease release-migration-state" in stdout.getvalue()
    assert client.create_requests == []
    assert client.replace_requests == [
        {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": {
                "name": "release-migration-state",
                "namespace": "default",
                "ownerReferences": [
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "name": "release-migration-identity",
                        "uid": "uid-current",
                    }
                ],
                "resourceVersion": "42",
            },
            "spec": {
                "holderIdentity": "12",
                "renewTime": "2026-04-18T12:00:00Z",
            },
        }
    ]


def test_mark_migration_complete_repairs_stale_owner_even_when_old_revision_is_newer() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision=25, owner_uid="uid-old", resource_version="42")],
    )

    module.mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="1",
        completed_at="2026-04-18T12:00:00Z",
        stdout=stdout,
    )

    assert "Recorded migration revision 1 in lease release-migration-state" in stdout.getvalue()
    assert client.create_requests == []
    assert client.replace_requests == [
        {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": {
                "name": "release-migration-state",
                "namespace": "default",
                "ownerReferences": [
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "name": "release-migration-identity",
                        "uid": "uid-current",
                    }
                ],
                "resourceVersion": "42",
            },
            "spec": {
                "holderIdentity": "1",
                "renewTime": "2026-04-18T12:00:00Z",
            },
        }
    ]


def test_mark_migration_complete_does_not_downgrade_matching_newer_lease_revision() -> None:
    module = _migration_state_module()
    stdout = io.StringIO()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[_lease(revision=13, owner_uid="uid-current")],
    )

    module.mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="12",
        stdout=stdout,
    )

    assert "already records newer revision 13; skipping stale revision 12" in stdout.getvalue()
    assert client.create_requests == []
    assert client.replace_requests == []


def test_mark_migration_complete_retries_conflict_then_succeeds() -> None:
    module = _migration_state_module()
    stderr = io.StringIO()
    stdout = io.StringIO()
    sleeps: list[float] = []
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[
            _lease(revision=11, owner_uid="uid-current", resource_version="42"),
            _lease(revision=11, owner_uid="uid-current", resource_version="43"),
        ],
        replace_side_effects=[
            module.KubernetesApiTransientError("lease update conflict"),
            _lease(revision=12, owner_uid="uid-current", resource_version="44"),
        ],
    )

    module.mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="12",
        completed_at="2026-04-18T12:00:00Z",
        max_attempts=2,
        sleep_seconds=0.25,
        sleeper=sleeps.append,
        stdout=stdout,
        stderr=stderr,
    )

    assert sleeps == [0.25]
    assert "Waiting to update migration state lease release-migration-state (1/2)" in stderr.getvalue()
    assert "Recorded migration revision 12 in lease release-migration-state" in stdout.getvalue()
    assert len(client.replace_requests) == 2
    assert client.replace_requests[0]["metadata"]["resourceVersion"] == "42"
    assert client.replace_requests[1]["metadata"]["resourceVersion"] == "43"


def test_mark_migration_complete_fails_fast_on_identity_access_denied() -> None:
    module = _migration_state_module()
    client = _FakeKubernetesApiClient(
        configmaps=[module.KubernetesApiAuthorizationError("forbidden")],
    )

    with pytest.raises(module.MigrationStateError, match="Check the migration Role/RoleBinding"):
        module.mark_migration_complete(
            client=client,
            identity_configmap_name="release-migration-identity",
            lease_name="release-migration-state",
            expected_revision="12",
        )


def test_deploy_and_mark_migration_complete_runs_prisma_then_records_state() -> None:
    module = _migration_state_module()
    client = _FakeKubernetesApiClient(
        configmaps=[_configmap(uid="uid-current")],
        leases=[module.KubernetesApiNotFoundError("lease missing")],
    )
    bootstrap_calls: list[dict[str, object]] = []

    def fake_bootstrap_runner(**kwargs: object) -> None:
        bootstrap_calls.append(kwargs)

    module.deploy_and_mark_migration_complete(
        client=client,
        identity_configmap_name="release-migration-identity",
        lease_name="release-migration-state",
        expected_revision="12",
        schema_path="./prisma/schema.prisma",
        max_attempts=7,
        sleep_seconds=0.75,
        state_max_attempts=4,
        state_sleep_seconds=0.5,
        bootstrap_runner=fake_bootstrap_runner,
        completed_at_factory=lambda: "2026-04-18T12:00:00Z",
    )

    assert bootstrap_calls == [
        {
            "mode": "deploy",
            "schema_path": "./prisma/schema.prisma",
            "max_attempts": 7,
            "sleep_seconds": 0.75,
            "stdout": None,
            "stderr": None,
        }
    ]
    assert client.create_requests == [
        {
            "name": "release-migration-state",
            "holder_identity": "12",
            "renew_time": "2026-04-18T12:00:00Z",
            "owner_reference": {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": "release-migration-identity",
                "uid": "uid-current",
            },
        }
    ]
