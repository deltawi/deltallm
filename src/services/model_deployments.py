from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.config import AppConfig
from src.db.named_credentials import NamedCredentialRecord, NamedCredentialRepository
from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository
from src.services.named_credentials import merge_named_credential_params, resolve_named_credential_record

if TYPE_CHECKING:
    from src.config_runtime.secrets import SecretResolver


class DuplicateModelNameError(ValueError):
    """Raised when multiple deployments share the same public model name."""


def _raise_duplicate_model_name(model_name: str) -> None:
    raise DuplicateModelNameError(f"Duplicate model_name '{model_name}' is not allowed")


def ensure_unique_model_names(model_names: list[str]) -> None:
    seen: set[str] = set()
    for raw_name in model_names:
        model_name = str(raw_name).strip()
        if model_name in seen:
            _raise_duplicate_model_name(model_name)
        seen.add(model_name)


def ensure_model_name_available(
    model_registry: dict[str, list[dict[str, Any]]],
    *,
    model_name: str,
    exclude_deployment_id: str | None = None,
) -> None:
    deployments = model_registry.get(model_name, [])
    for index, deployment in enumerate(deployments):
        deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
        if exclude_deployment_id is not None and deployment_id == exclude_deployment_id:
            continue
        _raise_duplicate_model_name(model_name)


def _deployment_id(model_name: str, index: int, value: str | None) -> str:
    if value:
        return str(value)
    return f"{model_name}-{index}"


def resolve_runtime_deltallm_params(
    params: dict[str, Any],
    settings: Any,
    *,
    named_credential: NamedCredentialRecord | None = None,
) -> dict[str, Any]:
    resolved = merge_named_credential_params(params, named_credential)
    if not resolved.get("api_key") and getattr(settings, "openai_api_key", None):
        resolved["api_key"] = settings.openai_api_key
    if not resolved.get("api_base") and getattr(settings, "openai_base_url", None):
        resolved["api_base"] = settings.openai_base_url
    return resolved


async def _named_credentials_by_id(
    repository: NamedCredentialRepository | None,
    credential_ids: list[str],
) -> dict[str, NamedCredentialRecord]:
    if repository is None:
        return {}
    return await repository.list_by_ids(credential_ids)


def model_records_from_config(cfg: AppConfig) -> list[ModelDeploymentRecord]:
    ensure_unique_model_names([entry.model_name for entry in cfg.model_list])
    records: list[ModelDeploymentRecord] = []
    for index, entry in enumerate(cfg.model_list):
        records.append(
            ModelDeploymentRecord(
                deployment_id=_deployment_id(entry.model_name, index, getattr(entry, "deployment_id", None)),
                model_name=entry.model_name,
                named_credential_id=str(entry.named_credential_id).strip() or None if entry.named_credential_id is not None else None,
                deltallm_params=entry.deltallm_params.model_dump(exclude_none=True),
                model_info=entry.model_info.model_dump(exclude_none=True) if entry.model_info else {},
            )
        )
    return records


async def build_model_registry_from_config(
    cfg: AppConfig,
    settings: Any,
    *,
    named_credential_repository: NamedCredentialRepository | None = None,
    secret_resolver: "SecretResolver | None" = None,
) -> dict[str, list[dict[str, Any]]]:
    ensure_unique_model_names([entry.model_name for entry in cfg.model_list])
    named_credentials = await _named_credentials_by_id(
        named_credential_repository,
        [str(entry.named_credential_id).strip() for entry in cfg.model_list if entry.named_credential_id is not None],
    )
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(cfg.model_list):
        raw_named_credential = named_credentials.get(str(entry.named_credential_id).strip()) if entry.named_credential_id is not None else None
        named_credential = resolve_named_credential_record(raw_named_credential, secret_resolver=secret_resolver)
        model_registry.setdefault(entry.model_name, []).append(
            {
                "deployment_id": _deployment_id(entry.model_name, index, getattr(entry, "deployment_id", None)),
                "deltallm_params": resolve_runtime_deltallm_params(
                    entry.deltallm_params.model_dump(exclude_none=True),
                    settings,
                    named_credential=named_credential,
                ),
                "model_info": entry.model_info.model_dump(exclude_none=True) if entry.model_info else {},
                "named_credential_id": str(entry.named_credential_id).strip() or None if entry.named_credential_id is not None else None,
                "named_credential_name": named_credential.name if named_credential is not None else None,
            }
        )
    return model_registry


async def build_model_registry_from_records(
    records: list[ModelDeploymentRecord],
    settings: Any,
    named_credential_repository: NamedCredentialRepository | None = None,
    *,
    secret_resolver: "SecretResolver | None" = None,
) -> dict[str, list[dict[str, Any]]]:
    ensure_unique_model_names([record.model_name for record in records])
    named_credentials = await _named_credentials_by_id(
        named_credential_repository,
        [record.named_credential_id for record in records if record.named_credential_id],
    )
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        raw_named_credential = named_credentials.get(record.named_credential_id or "")
        named_credential = resolve_named_credential_record(raw_named_credential, secret_resolver=secret_resolver)
        model_registry.setdefault(record.model_name, []).append(
            {
                "deployment_id": record.deployment_id,
                "named_credential_id": record.named_credential_id,
                "named_credential_name": named_credential.name if named_credential is not None else None,
                "deltallm_params": resolve_runtime_deltallm_params(
                    record.deltallm_params,
                    settings,
                    named_credential=named_credential,
                ),
                "model_info": dict(record.model_info or {}),
            }
        )
    return model_registry


async def bootstrap_model_deployments_from_config(
    repository: ModelDeploymentRepository,
    cfg: AppConfig,
) -> bool:
    records = model_records_from_config(cfg)
    if not records:
        return False
    return await repository.bulk_insert_if_empty(records)


async def load_model_registry(
    repository: ModelDeploymentRepository | None,
    cfg: AppConfig,
    settings: Any,
    source_mode: str = "hybrid",
    named_credential_repository: NamedCredentialRepository | None = None,
    secret_resolver: "SecretResolver | None" = None,
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    if source_mode == "config_only":
        return await build_model_registry_from_config(
            cfg,
            settings,
            named_credential_repository=named_credential_repository,
            secret_resolver=secret_resolver,
        ), "config"

    if repository is not None and source_mode in {"hybrid", "db_only"}:
        try:
            records = await repository.list_all()
        except Exception:
            if source_mode == "db_only":
                raise RuntimeError("model deployment source is db_only, but loading deployments from DB failed")
            records = []
        if records:
            return await build_model_registry_from_records(
                records,
                settings,
                named_credential_repository=named_credential_repository,
                secret_resolver=secret_resolver,
            ), "db"
        if source_mode == "db_only":
            raise RuntimeError("model deployment source is db_only, but no deployments were found in DB")

    if source_mode == "db_only":
        raise RuntimeError("model deployment source is db_only, but model repository is unavailable")

    return await build_model_registry_from_config(
        cfg,
        settings,
        named_credential_repository=named_credential_repository,
        secret_resolver=secret_resolver,
    ), "config"
