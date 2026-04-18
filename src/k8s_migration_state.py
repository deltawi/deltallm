from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TextIO
from urllib.parse import quote

from src.prisma_bootstrap import (
    DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS,
    DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS,
    DEFAULT_PRISMA_SCHEMA_PATH,
    run_prisma_bootstrap,
)

DEFAULT_KUBERNETES_TOKEN_PATH = "/var/run/secrets/deltallm-migration/token"
DEFAULT_KUBERNETES_CA_PATH = "/var/run/secrets/deltallm-migration/ca.crt"
DEFAULT_KUBERNETES_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_MIGRATION_WAIT_TIMEOUT_SECONDS = 900.0
DEFAULT_MIGRATION_WAIT_PERIOD_SECONDS = 2.0
DEFAULT_MIGRATION_STATE_UPDATE_ATTEMPTS = 30
DEFAULT_MIGRATION_STATE_UPDATE_SLEEP_SECONDS = 2.0

_DEFAULT_NAMESPACE_ENV = "POD_NAMESPACE"
_DEFAULT_RELEASE_REVISION_ENV = "DELTALLM_RELEASE_REVISION"
_DEFAULT_MIGRATION_IDENTITY_ENV = "DELTALLM_MIGRATION_IDENTITY_CONFIGMAP_NAME"
_DEFAULT_MIGRATION_LEASE_ENV = "DELTALLM_MIGRATION_STATE_LEASE_NAME"
_DEFAULT_MIGRATION_JOB_ENV = "DELTALLM_MIGRATION_JOB_NAME"
_DEFAULT_WAIT_TIMEOUT_ENV = "DELTALLM_MIGRATION_WAIT_TIMEOUT_SECONDS"
_DEFAULT_WAIT_PERIOD_ENV = "DELTALLM_MIGRATION_WAIT_PERIOD_SECONDS"

_AUTHORIZATION_STATUS_CODES = {401, 403}
_TRANSIENT_STATUS_CODES = {409, 429, 500, 502, 503, 504}


class MigrationStateError(RuntimeError):
    pass


class KubernetesApiError(RuntimeError):
    pass


class KubernetesApiNotFoundError(KubernetesApiError):
    pass


class KubernetesApiAuthorizationError(KubernetesApiError):
    pass


class KubernetesApiTransientError(KubernetesApiError):
    pass


@dataclass(frozen=True)
class ReleaseIdentity:
    name: str
    uid: str


@dataclass(frozen=True)
class MigrationLeaseState:
    holder_identity: str
    revision: int
    renew_time: str


@dataclass(frozen=True)
class KubernetesApiClient:
    base_url: str
    namespace: str
    token: str
    ssl_context: ssl.SSLContext
    request_timeout_seconds: float = DEFAULT_KUBERNETES_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def from_cluster(
        cls,
        *,
        namespace: str,
        token_path: str = DEFAULT_KUBERNETES_TOKEN_PATH,
        ca_path: str = DEFAULT_KUBERNETES_CA_PATH,
        api_host: str | None = None,
        api_port: str | None = None,
    ) -> KubernetesApiClient:
        normalized_namespace = _required_argument(namespace, "namespace")
        host = str(api_host or os.getenv("KUBERNETES_SERVICE_HOST", "")).strip()
        if not host:
            raise MigrationStateError("KUBERNETES_SERVICE_HOST is not set")
        port = str(api_port or os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")).strip() or "443"

        try:
            with open(token_path, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as exc:
            raise MigrationStateError(f"Failed to read Kubernetes API token: {exc}") from exc
        if not token:
            raise MigrationStateError("Kubernetes API token is empty")

        try:
            ssl_context = ssl.create_default_context(cafile=ca_path)
        except OSError as exc:
            raise MigrationStateError(f"Failed to read Kubernetes API CA bundle: {exc}") from exc

        return cls(
            base_url=f"https://{host}:{port}",
            namespace=normalized_namespace,
            token=token,
            ssl_context=ssl_context,
        )

    def get_configmap(self, name: str) -> dict[str, Any]:
        normalized_name = _required_argument(name, "configmap name")
        return self._request_json(
            method="GET",
            path=f"/api/v1/namespaces/{quote(self.namespace, safe='')}/configmaps/{quote(normalized_name, safe='')}",
        )

    def get_lease(self, name: str) -> dict[str, Any]:
        normalized_name = _required_argument(name, "lease name")
        return self._request_json(
            method="GET",
            path=(
                f"/apis/coordination.k8s.io/v1/namespaces/{quote(self.namespace, safe='')}/leases/"
                f"{quote(normalized_name, safe='')}"
            ),
        )

    def create_lease(
        self,
        name: str,
        *,
        holder_identity: str,
        renew_time: str,
        owner_reference: dict[str, str],
    ) -> dict[str, Any]:
        normalized_name = _required_argument(name, "lease name")
        body = json.dumps(
            _build_lease_document(
                name=normalized_name,
                namespace=self.namespace,
                holder_identity=holder_identity,
                renew_time=renew_time,
                owner_reference=owner_reference,
            )
        ).encode("utf-8")
        return self._request_json(
            method="POST",
            path=f"/apis/coordination.k8s.io/v1/namespaces/{quote(self.namespace, safe='')}/leases",
            body=body,
            headers={"Content-Type": "application/json"},
        )

    def replace_lease(self, lease: dict[str, Any]) -> dict[str, Any]:
        metadata = lease.get("metadata") or {}
        normalized_name = _required_argument(metadata.get("name"), "lease name")
        body = json.dumps(lease).encode("utf-8")
        return self._request_json(
            method="PUT",
            path=(
                f"/apis/coordination.k8s.io/v1/namespaces/{quote(self.namespace, safe='')}/leases/"
                f"{quote(normalized_name, safe='')}"
            ),
            body=body,
            headers={"Content-Type": "application/json"},
        )

    def get_job(self, name: str) -> dict[str, Any]:
        normalized_name = _required_argument(name, "job name")
        return self._request_json(
            method="GET",
            path=f"/apis/batch/v1/namespaces/{quote(self.namespace, safe='')}/jobs/{quote(normalized_name, safe='')}",
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                context=self.ssl_context,
                timeout=self.request_timeout_seconds,
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace").strip()
            message = f"Kubernetes API {method} {path} failed with HTTP {exc.code}"
            if body_text:
                message = f"{message}: {body_text}"
            if exc.code == 404:
                raise KubernetesApiNotFoundError(message) from exc
            if exc.code in _AUTHORIZATION_STATUS_CODES:
                raise KubernetesApiAuthorizationError(message) from exc
            if exc.code in _TRANSIENT_STATUS_CODES:
                raise KubernetesApiTransientError(message) from exc
            raise KubernetesApiError(message) from exc
        except OSError as exc:
            raise KubernetesApiTransientError(f"Failed to contact the Kubernetes API: {exc}") from exc


def wait_for_migration_completion(
    *,
    client: KubernetesApiClient,
    identity_configmap_name: str,
    lease_name: str,
    expected_revision: str,
    job_name: str,
    timeout_seconds: float = DEFAULT_MIGRATION_WAIT_TIMEOUT_SECONDS,
    period_seconds: float = DEFAULT_MIGRATION_WAIT_PERIOD_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    stdout: TextIO | None = None,
) -> None:
    normalized_identity_configmap_name = _required_argument(
        identity_configmap_name,
        "migration identity configmap name",
    )
    normalized_lease_name = _required_argument(lease_name, "lease name")
    normalized_expected_revision, expected_revision_number = _parse_revision(expected_revision, label="release revision")
    normalized_job_name = _required_argument(job_name, "job name")
    out_stream = stdout if stdout is not None else sys.stdout
    delay = max(0.0, float(period_seconds))
    deadline = monotonic() + max(1.0, float(timeout_seconds))

    while True:
        state_message = ""
        try:
            release_identity = _load_release_identity(
                client=client,
                identity_configmap_name=normalized_identity_configmap_name,
            )
        except KubernetesApiAuthorizationError as exc:
            raise _authorization_error(
                action="reading",
                resource_kind="migration identity ConfigMap",
                resource_name=normalized_identity_configmap_name,
                exc=exc,
            ) from exc
        except KubernetesApiNotFoundError as exc:
            raise MigrationStateError(
                f"Migration identity ConfigMap {normalized_identity_configmap_name} was not found: {exc}"
            ) from exc
        except KubernetesApiTransientError as exc:
            state_message = (
                f"migration identity ConfigMap {normalized_identity_configmap_name} is temporarily unavailable ({exc})"
            )
        except KubernetesApiError as exc:
            raise MigrationStateError(
                f"Failed to read migration identity ConfigMap {normalized_identity_configmap_name}: {exc}"
            ) from exc
        else:
            try:
                lease = client.get_lease(normalized_lease_name)
            except KubernetesApiNotFoundError as exc:
                state_message = f"migration state lease {normalized_lease_name} is not available yet ({exc})"
            except KubernetesApiAuthorizationError as exc:
                raise _authorization_error(
                    action="reading",
                    resource_kind="migration state Lease",
                    resource_name=normalized_lease_name,
                    exc=exc,
                ) from exc
            except KubernetesApiTransientError as exc:
                state_message = f"migration state lease {normalized_lease_name} is temporarily unavailable ({exc})"
            except KubernetesApiError as exc:
                raise MigrationStateError(str(exc)) from exc
            else:
                owner_uid = _lease_owner_uid(lease, identity_name=release_identity.name)
                if owner_uid == release_identity.uid:
                    lease_state = _parse_migration_lease_state(lease, lease_name=normalized_lease_name)
                    if lease_state.revision >= expected_revision_number:
                        print(
                            (
                                f"Migration state lease {normalized_lease_name} confirms revision {lease_state.revision} "
                                f"for expected revision {normalized_expected_revision}"
                            ),
                            file=out_stream,
                            flush=True,
                        )
                        return
                    state_message = (
                        f"migration state lease revision={lease_state.revision} "
                        f"renewTime={_display_value(lease_state.renew_time)}"
                    )
                elif owner_uid:
                    state_message = (
                        f"migration state lease {normalized_lease_name} belongs to stale release identity uid {owner_uid}"
                    )
                else:
                    state_message = (
                        f"migration state lease {normalized_lease_name} is missing the current release identity owner reference"
                    )

        try:
            job = client.get_job(normalized_job_name)
        except KubernetesApiNotFoundError as exc:
            job_message = f"migration job {normalized_job_name} is not available yet ({exc})"
        except KubernetesApiAuthorizationError as exc:
            raise _authorization_error(
                action="reading",
                resource_kind="migration Job",
                resource_name=normalized_job_name,
                exc=exc,
            ) from exc
        except KubernetesApiTransientError as exc:
            job_message = f"migration job {normalized_job_name} is temporarily unavailable ({exc})"
        except KubernetesApiError as exc:
            raise MigrationStateError(str(exc)) from exc
        else:
            conditions = _job_conditions(job)
            if conditions.get("Failed") == "True":
                raise MigrationStateError(
                    f"Migration job {normalized_job_name} failed before revision {normalized_expected_revision} was recorded"
                )
            if conditions.get("Complete") == "True":
                job_message = (
                    f"migration job {normalized_job_name} completed and is waiting to record "
                    f"revision {normalized_expected_revision} in lease {normalized_lease_name}"
                )
            else:
                job_message = f"migration job {normalized_job_name} status {_job_status_summary(job)}"

        if monotonic() >= deadline:
            raise MigrationStateError(
                (
                    f"Timed out waiting for migration revision {normalized_expected_revision}: "
                    f"{state_message}; {job_message}"
                )
            )

        print(
            f"Waiting for migration revision {normalized_expected_revision}: {state_message}; {job_message}",
            file=out_stream,
            flush=True,
        )
        sleeper(delay)


def mark_migration_complete(
    *,
    client: KubernetesApiClient,
    identity_configmap_name: str,
    lease_name: str,
    expected_revision: str,
    completed_at: str | None = None,
    max_attempts: int = DEFAULT_MIGRATION_STATE_UPDATE_ATTEMPTS,
    sleep_seconds: float = DEFAULT_MIGRATION_STATE_UPDATE_SLEEP_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    normalized_identity_configmap_name = _required_argument(
        identity_configmap_name,
        "migration identity configmap name",
    )
    normalized_lease_name = _required_argument(lease_name, "lease name")
    normalized_expected_revision, expected_revision_number = _parse_revision(expected_revision, label="release revision")
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(sleep_seconds))
    completed_timestamp = completed_at or current_timestamp()

    for attempt in range(1, attempts + 1):
        try:
            release_identity = _load_release_identity(
                client=client,
                identity_configmap_name=normalized_identity_configmap_name,
            )
        except KubernetesApiAuthorizationError as exc:
            raise _authorization_error(
                action="reading",
                resource_kind="migration identity ConfigMap",
                resource_name=normalized_identity_configmap_name,
                exc=exc,
            ) from exc
        except KubernetesApiNotFoundError as exc:
            raise MigrationStateError(
                f"Migration identity ConfigMap {normalized_identity_configmap_name} was not found: {exc}"
            ) from exc
        except KubernetesApiTransientError as exc:
            if attempt >= attempts:
                raise MigrationStateError(
                    (
                        f"Failed to read migration identity ConfigMap {normalized_identity_configmap_name} "
                        f"after {attempts} attempts: {exc}"
                    )
                ) from exc
            print(
                (
                    f"Waiting to read migration identity ConfigMap {normalized_identity_configmap_name} "
                    f"({attempt}/{attempts}): {exc}"
                ),
                file=err_stream,
                flush=True,
            )
            sleeper(delay)
            continue
        except KubernetesApiError as exc:
            raise MigrationStateError(
                f"Failed to read migration identity ConfigMap {normalized_identity_configmap_name}: {exc}"
            ) from exc

        owner_reference = _build_identity_owner_reference(release_identity)
        try:
            lease = client.get_lease(normalized_lease_name)
        except KubernetesApiNotFoundError:
            try:
                client.create_lease(
                    normalized_lease_name,
                    holder_identity=normalized_expected_revision,
                    renew_time=completed_timestamp,
                    owner_reference=owner_reference,
                )
            except KubernetesApiAuthorizationError as exc:
                raise _authorization_error(
                    action="creating",
                    resource_kind="migration state Lease",
                    resource_name=normalized_lease_name,
                    exc=exc,
                ) from exc
            except KubernetesApiTransientError as exc:
                if attempt >= attempts:
                    raise MigrationStateError(
                        (
                            f"Failed to record migration revision {normalized_expected_revision} in lease "
                            f"{normalized_lease_name} after {attempts} attempts: {exc}"
                        )
                    ) from exc
                print(
                    f"Waiting to create migration state lease {normalized_lease_name} ({attempt}/{attempts}): {exc}",
                    file=err_stream,
                    flush=True,
                )
                sleeper(delay)
                continue
            except KubernetesApiError as exc:
                raise MigrationStateError(
                    f"Failed to create migration state lease {normalized_lease_name}: {exc}"
                ) from exc
            print(
                f"Recorded migration revision {normalized_expected_revision} in lease {normalized_lease_name}",
                file=out_stream,
                flush=True,
            )
            return
        except KubernetesApiAuthorizationError as exc:
            raise _authorization_error(
                action="reading",
                resource_kind="migration state Lease",
                resource_name=normalized_lease_name,
                exc=exc,
            ) from exc
        except KubernetesApiTransientError as exc:
            if attempt >= attempts:
                raise MigrationStateError(
                    (
                        f"Failed to record migration revision {normalized_expected_revision} in lease "
                        f"{normalized_lease_name} after {attempts} attempts: {exc}"
                    )
                ) from exc
            print(
                f"Waiting to read migration state lease {normalized_lease_name} ({attempt}/{attempts}): {exc}",
                file=err_stream,
                flush=True,
            )
            sleeper(delay)
            continue
        except KubernetesApiError as exc:
            raise MigrationStateError(
                f"Failed to record migration revision {normalized_expected_revision} in lease {normalized_lease_name}: {exc}"
            ) from exc

        owner_uid = _lease_owner_uid(lease, identity_name=release_identity.name)
        if owner_uid == release_identity.uid:
            lease_state = _parse_migration_lease_state(lease, lease_name=normalized_lease_name)
            if lease_state.revision > expected_revision_number:
                print(
                    (
                        f"Migration state lease {normalized_lease_name} already records newer revision "
                        f"{lease_state.revision}; skipping stale revision {normalized_expected_revision}"
                    ),
                    file=out_stream,
                    flush=True,
                )
                return
            if lease_state.revision == expected_revision_number:
                print(
                    f"Migration state lease {normalized_lease_name} already records revision {normalized_expected_revision}",
                    file=out_stream,
                    flush=True,
                )
                return

        updated_lease = _updated_lease_document(
            lease,
            lease_name=normalized_lease_name,
            holder_identity=normalized_expected_revision,
            renew_time=completed_timestamp,
            owner_reference=owner_reference,
        )
        try:
            client.replace_lease(updated_lease)
        except KubernetesApiAuthorizationError as exc:
            raise _authorization_error(
                action="updating",
                resource_kind="migration state Lease",
                resource_name=normalized_lease_name,
                exc=exc,
            ) from exc
        except KubernetesApiTransientError as exc:
            if attempt >= attempts:
                raise MigrationStateError(
                    (
                        f"Failed to advance migration revision {normalized_expected_revision} in lease "
                        f"{normalized_lease_name} after {attempts} attempts: {exc}"
                    )
                ) from exc
            print(
                f"Waiting to update migration state lease {normalized_lease_name} ({attempt}/{attempts}): {exc}",
                file=err_stream,
                flush=True,
            )
            sleeper(delay)
            continue
        except KubernetesApiError as exc:
            raise MigrationStateError(
                f"Failed to advance migration revision {normalized_expected_revision} in lease {normalized_lease_name}: {exc}"
            ) from exc

        print(
            f"Recorded migration revision {normalized_expected_revision} in lease {normalized_lease_name}",
            file=out_stream,
            flush=True,
        )
        return


def deploy_and_mark_migration_complete(
    *,
    client: KubernetesApiClient,
    identity_configmap_name: str,
    lease_name: str,
    expected_revision: str,
    schema_path: str = DEFAULT_PRISMA_SCHEMA_PATH,
    max_attempts: int = DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS,
    sleep_seconds: float = DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS,
    state_max_attempts: int = DEFAULT_MIGRATION_STATE_UPDATE_ATTEMPTS,
    state_sleep_seconds: float = DEFAULT_MIGRATION_STATE_UPDATE_SLEEP_SECONDS,
    bootstrap_runner: Callable[..., None] = run_prisma_bootstrap,
    completed_at_factory: Callable[[], str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    bootstrap_runner(
        mode="deploy",
        schema_path=schema_path,
        max_attempts=max_attempts,
        sleep_seconds=sleep_seconds,
        stdout=stdout,
        stderr=stderr,
    )
    mark_migration_complete(
        client=client,
        identity_configmap_name=identity_configmap_name,
        lease_name=lease_name,
        expected_revision=expected_revision,
        completed_at=(completed_at_factory or current_timestamp)(),
        max_attempts=state_max_attempts,
        sleep_seconds=state_sleep_seconds,
        stdout=stdout,
        stderr=stderr,
    )


def current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_release_identity(
    *,
    client: KubernetesApiClient,
    identity_configmap_name: str,
) -> ReleaseIdentity:
    normalized_name = _required_argument(identity_configmap_name, "migration identity configmap name")
    configmap = client.get_configmap(normalized_name)
    return _parse_release_identity(configmap, identity_configmap_name=normalized_name)


def _authorization_error(
    *,
    action: str,
    resource_kind: str,
    resource_name: str,
    exc: KubernetesApiAuthorizationError,
) -> MigrationStateError:
    return MigrationStateError(
        (
            f"Access denied while {action} {resource_kind} {resource_name}: {exc}. "
            "Check the migration Role/RoleBinding and projected service-account token configuration."
        )
    )


def _build_lease_document(
    *,
    name: str,
    namespace: str,
    holder_identity: str,
    renew_time: str,
    owner_reference: dict[str, str],
    resource_version: str | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": _required_argument(name, "lease name"),
        "namespace": _required_argument(namespace, "namespace"),
        "ownerReferences": [dict(owner_reference)],
    }
    if resource_version:
        metadata["resourceVersion"] = resource_version
    lease_spec = dict(spec or {})
    lease_spec["holderIdentity"] = _required_argument(holder_identity, "lease holderIdentity")
    lease_spec["renewTime"] = _required_argument(renew_time, "lease renewTime")
    return {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": metadata,
        "spec": lease_spec,
    }


def _build_identity_owner_reference(release_identity: ReleaseIdentity) -> dict[str, str]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "name": release_identity.name,
        "uid": release_identity.uid,
    }


def _parse_release_identity(
    configmap: dict[str, Any],
    *,
    identity_configmap_name: str,
) -> ReleaseIdentity:
    metadata = configmap.get("metadata") or {}
    uid = _required_argument(
        metadata.get("uid"),
        f"migration identity ConfigMap {identity_configmap_name} metadata.uid",
    )
    return ReleaseIdentity(name=identity_configmap_name, uid=uid)


def _parse_migration_lease_state(lease: dict[str, Any], *, lease_name: str) -> MigrationLeaseState:
    spec = lease.get("spec") or {}
    holder_identity, revision = _parse_revision(
        spec.get("holderIdentity"),
        label=f"migration state lease {lease_name} holderIdentity",
    )
    renew_time = str(spec.get("renewTime", "")).strip()
    return MigrationLeaseState(
        holder_identity=holder_identity,
        revision=revision,
        renew_time=renew_time,
    )


def _lease_owner_uid(lease: dict[str, Any], *, identity_name: str) -> str:
    metadata = lease.get("metadata") or {}
    owner_references = metadata.get("ownerReferences") or []
    for owner_reference in owner_references:
        if (
            str(owner_reference.get("apiVersion", "")).strip() == "v1"
            and str(owner_reference.get("kind", "")).strip() == "ConfigMap"
            and str(owner_reference.get("name", "")).strip() == identity_name
        ):
            return str(owner_reference.get("uid", "")).strip()
    return ""


def _updated_lease_document(
    lease: dict[str, Any],
    *,
    lease_name: str,
    holder_identity: str,
    renew_time: str,
    owner_reference: dict[str, str],
) -> dict[str, Any]:
    metadata = lease.get("metadata") or {}
    resource_version = str(metadata.get("resourceVersion", "")).strip()
    if not resource_version:
        raise MigrationStateError(
            f"Migration state lease {lease_name} is missing metadata.resourceVersion"
        )
    namespace = _required_argument(
        metadata.get("namespace"),
        f"migration state lease {lease_name} metadata.namespace",
    )
    return _build_lease_document(
        name=lease_name,
        namespace=namespace,
        holder_identity=holder_identity,
        renew_time=renew_time,
        owner_reference=owner_reference,
        resource_version=resource_version,
        spec=lease.get("spec") or {},
    )


def _job_conditions(job: dict[str, Any]) -> dict[str, str]:
    conditions = {}
    for condition in (job.get("status") or {}).get("conditions") or []:
        condition_type = str(condition.get("type", "")).strip()
        if condition_type:
            conditions[condition_type] = str(condition.get("status", "")).strip()
    return conditions


def _job_status_summary(job: dict[str, Any]) -> str:
    status = job.get("status") or {}
    active = int(status.get("active", 0) or 0)
    succeeded = int(status.get("succeeded", 0) or 0)
    failed = int(status.get("failed", 0) or 0)
    return f"active={active} succeeded={succeeded} failed={failed}"


def _display_value(value: str) -> str:
    return value if value else "<empty>"


def _required_argument(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MigrationStateError(f"{label} is required")
    return normalized


def _parse_revision(value: object, *, label: str) -> tuple[str, int]:
    normalized = _required_argument(value, label)
    try:
        revision = int(normalized)
    except ValueError as exc:
        raise MigrationStateError(f"{label} must be a positive integer") from exc
    if revision < 1:
        raise MigrationStateError(f"{label} must be a positive integer")
    return normalized, revision


def _env_float(name: str, default: float) -> float:
    raw_value = str(os.getenv(name, default)).strip()
    try:
        return float(raw_value)
    except ValueError as exc:
        raise MigrationStateError(f"{name} must be a number") from exc


def _add_cluster_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", default=os.getenv(_DEFAULT_NAMESPACE_ENV, ""))
    parser.add_argument("--token-path", default=DEFAULT_KUBERNETES_TOKEN_PATH)
    parser.add_argument("--ca-path", default=DEFAULT_KUBERNETES_CA_PATH)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coordinate durable Kubernetes migration state for DeltaLLM releases.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wait_parser = subparsers.add_parser("wait", help="Wait for the current release revision to be marked as migrated.")
    _add_cluster_arguments(wait_parser)
    wait_parser.add_argument("--identity-configmap-name", default=os.getenv(_DEFAULT_MIGRATION_IDENTITY_ENV, ""))
    wait_parser.add_argument("--lease-name", default=os.getenv(_DEFAULT_MIGRATION_LEASE_ENV, ""))
    wait_parser.add_argument("--job-name", default=os.getenv(_DEFAULT_MIGRATION_JOB_ENV, ""))
    wait_parser.add_argument("--revision", default=os.getenv(_DEFAULT_RELEASE_REVISION_ENV, ""))
    wait_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=_env_float(_DEFAULT_WAIT_TIMEOUT_ENV, DEFAULT_MIGRATION_WAIT_TIMEOUT_SECONDS),
    )
    wait_parser.add_argument(
        "--period-seconds",
        type=float,
        default=_env_float(_DEFAULT_WAIT_PERIOD_ENV, DEFAULT_MIGRATION_WAIT_PERIOD_SECONDS),
    )

    deploy_parser = subparsers.add_parser(
        "deploy-and-mark",
        help="Run prisma migrate deploy and mark the current release revision as complete.",
    )
    _add_cluster_arguments(deploy_parser)
    deploy_parser.add_argument("--identity-configmap-name", default=os.getenv(_DEFAULT_MIGRATION_IDENTITY_ENV, ""))
    deploy_parser.add_argument("--lease-name", default=os.getenv(_DEFAULT_MIGRATION_LEASE_ENV, ""))
    deploy_parser.add_argument("--revision", default=os.getenv(_DEFAULT_RELEASE_REVISION_ENV, ""))
    deploy_parser.add_argument("--schema", default=DEFAULT_PRISMA_SCHEMA_PATH)
    deploy_parser.add_argument("--max-attempts", type=int, default=DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS)
    deploy_parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS)
    deploy_parser.add_argument("--state-max-attempts", type=int, default=DEFAULT_MIGRATION_STATE_UPDATE_ATTEMPTS)
    deploy_parser.add_argument(
        "--state-sleep-seconds",
        type=float,
        default=DEFAULT_MIGRATION_STATE_UPDATE_SLEEP_SECONDS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        client = KubernetesApiClient.from_cluster(
            namespace=args.namespace,
            token_path=args.token_path,
            ca_path=args.ca_path,
        )
        if args.command == "wait":
            wait_for_migration_completion(
                client=client,
                identity_configmap_name=args.identity_configmap_name,
                lease_name=args.lease_name,
                expected_revision=args.revision,
                job_name=args.job_name,
                timeout_seconds=args.timeout_seconds,
                period_seconds=args.period_seconds,
            )
            return 0
        if args.command == "deploy-and-mark":
            deploy_and_mark_migration_complete(
                client=client,
                identity_configmap_name=args.identity_configmap_name,
                lease_name=args.lease_name,
                expected_revision=args.revision,
                schema_path=args.schema,
                max_attempts=args.max_attempts,
                sleep_seconds=args.sleep_seconds,
                state_max_attempts=args.state_max_attempts,
                state_sleep_seconds=args.state_sleep_seconds,
            )
            return 0
        raise AssertionError(f"Unhandled command: {args.command}")
    except (MigrationStateError, KeyboardInterrupt) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
