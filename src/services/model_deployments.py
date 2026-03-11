from __future__ import annotations

from typing import Any

from src.config import AppConfig
from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository


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


def _resolved_params(params: dict[str, Any], settings: Any) -> dict[str, Any]:
    resolved = dict(params)
    if not resolved.get("api_key") and getattr(settings, "openai_api_key", None):
        resolved["api_key"] = settings.openai_api_key
    if not resolved.get("api_base") and getattr(settings, "openai_base_url", None):
        resolved["api_base"] = settings.openai_base_url
    return resolved


def model_records_from_config(cfg: AppConfig) -> list[ModelDeploymentRecord]:
    ensure_unique_model_names([entry.model_name for entry in cfg.model_list])
    records: list[ModelDeploymentRecord] = []
    for index, entry in enumerate(cfg.model_list):
        records.append(
            ModelDeploymentRecord(
                deployment_id=_deployment_id(entry.model_name, index, getattr(entry, "deployment_id", None)),
                model_name=entry.model_name,
                deltallm_params=entry.deltallm_params.model_dump(exclude_none=True),
                model_info=entry.model_info.model_dump(exclude_none=True) if entry.model_info else {},
            )
        )
    return records


def build_model_registry_from_config(cfg: AppConfig, settings: Any) -> dict[str, list[dict[str, Any]]]:
    ensure_unique_model_names([entry.model_name for entry in cfg.model_list])
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(cfg.model_list):
        model_registry.setdefault(entry.model_name, []).append(
            {
                "deployment_id": _deployment_id(entry.model_name, index, getattr(entry, "deployment_id", None)),
                "deltallm_params": _resolved_params(entry.deltallm_params.model_dump(exclude_none=True), settings),
                "model_info": entry.model_info.model_dump(exclude_none=True) if entry.model_info else {},
            }
        )
    return model_registry


def build_model_registry_from_records(
    records: list[ModelDeploymentRecord],
    settings: Any,
) -> dict[str, list[dict[str, Any]]]:
    ensure_unique_model_names([record.model_name for record in records])
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        model_registry.setdefault(record.model_name, []).append(
            {
                "deployment_id": record.deployment_id,
                "deltallm_params": _resolved_params(record.deltallm_params, settings),
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
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    if source_mode == "config_only":
        return build_model_registry_from_config(cfg, settings), "config"

    if repository is not None and source_mode in {"hybrid", "db_only"}:
        try:
            records = await repository.list_all()
        except Exception:
            if source_mode == "db_only":
                raise RuntimeError("model deployment source is db_only, but loading deployments from DB failed")
            records = []
        if records:
            return build_model_registry_from_records(records, settings), "db"
        if source_mode == "db_only":
            raise RuntimeError("model deployment source is db_only, but no deployments were found in DB")

    if source_mode == "db_only":
        raise RuntimeError("model deployment source is db_only, but model repository is unavailable")

    return build_model_registry_from_config(cfg, settings), "config"
