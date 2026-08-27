from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import (
    emit_admin_mutation_audit,
    model_entries,
    to_json_value,
    get_auth_scope,
)
from src.config import (
    AppConfig,
    DEFAULT_UI_INSTANCE_NAME,
    UIBrandingPayload,
    UIBrandingResetPayload,
    UIBrandingSettings,
    UIBrandingUpdatePayload,
)
from src.config_runtime.dynamic import (
    DynamicConfigPostCommitApplyError,
    DynamicConfigRestartRequiredError,
)
from src.db.ui_branding_assets import BrandingAssetDatabase, UIBrandingAssetRepository
from src.middleware.admin import require_admin_permission
from src.providers.resolution import resolve_provider
from src.services.audit_service import require_audit_service
from src.services.ui_branding_assets import (
    BRANDING_ASSET_MAX_BYTES,
    UIBrandingAssetService,
    branding_asset_config_field,
    branding_asset_url,
    normalize_asset_kind,
    validate_branding_asset,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin Config"])


def _ui_branding_from_app_config(app_config: AppConfig | None) -> UIBrandingPayload:
    general_settings = app_config.general_settings if app_config is not None else None
    instance_name = str(
        getattr(general_settings, "instance_name", DEFAULT_UI_INSTANCE_NAME)
        or DEFAULT_UI_INSTANCE_NAME
    )
    ui_branding = getattr(general_settings, "ui_branding", None)
    if not isinstance(ui_branding, UIBrandingSettings):
        ui_branding = UIBrandingSettings()
    return UIBrandingPayload(instance_name=instance_name, **ui_branding.model_dump())


def _effective_ui_branding(request: Request) -> UIBrandingPayload:
    app_config = getattr(request.app.state, "app_config", None)
    return _ui_branding_from_app_config(app_config if isinstance(app_config, AppConfig) else None)


def _reset_audit_event_id(operation_id: str, phase: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"deltallm:ui-branding-reset:{operation_id}:{phase}"))


def _audit_enabled(request: Request) -> bool:
    app_config = getattr(request.app.state, "app_config", None)
    if not isinstance(app_config, AppConfig):
        return True
    return app_config.general_settings.audit_enabled


async def _emit_branding_reset_outcome_audit(
    *,
    request: Request,
    request_start: float,
    operation_id: str,
    before: UIBrandingPayload,
    after: UIBrandingPayload | None,
    reconciliation_pending: bool,
    error: Exception | None = None,
) -> None:
    try:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_UI_BRANDING_RESET,
            resource_type="ui_branding",
            request_payload={"target": "factory_defaults"},
            response_payload=after.model_dump() if after is not None else None,
            before=before.model_dump(),
            after=after.model_dump() if after is not None else None,
            metadata={
                "operation_id": operation_id,
                "phase": "outcome",
                "reconciliation_pending": reconciliation_pending,
            },
            status="error" if error is not None else "success",
            error=error,
            critical=False,
            event_id=_reset_audit_event_id(operation_id, "outcome"),
        )
    except asyncio.CancelledError:
        raise
    except Exception as audit_error:
        logger.warning(
            "branding reset outcome audit failed operation_id=%s error_type=%s",
            operation_id,
            type(audit_error).__name__,
        )


@router.get("/ui/api/branding", response_model=UIBrandingPayload)
async def get_ui_branding(request: Request, response: Response) -> UIBrandingPayload:
    response.headers["Cache-Control"] = "no-store"
    return _effective_ui_branding(request)


def _branding_asset_service(request: Request) -> UIBrandingAssetService:
    service = getattr(request.app.state, "ui_branding_asset_service", None)
    if not isinstance(service, UIBrandingAssetService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Branding asset service unavailable",
        )
    return service


def _asset_response_headers(
    *, content_sha256: str, size_bytes: int, versioned: bool
) -> dict[str, str]:
    return {
        "Cache-Control": "public, max-age=31536000, immutable"
        if versioned
        else "public, max-age=60",
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox",
        "Content-Length": str(size_bytes),
        "ETag": f'"{content_sha256}"',
        "X-Content-Type-Options": "nosniff",
    }


@router.api_route("/ui/api/branding/assets/{asset_key}", methods=["GET", "HEAD"])
async def get_ui_branding_asset(
    request: Request,
    asset_key: str,
    version: str | None = Query(
        default=None, alias="v", min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    try:
        normalized_key = normalize_asset_kind(asset_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Branding asset not found"
        ) from exc

    asset = await _branding_asset_service(request).get_asset(
        normalized_key, expected_sha256=version
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Branding asset not found"
        )

    headers = _asset_response_headers(
        content_sha256=asset.content_sha256,
        size_bytes=asset.size_bytes,
        versioned=version is not None,
    )
    if if_none_match and headers["ETag"] in {part.strip() for part in if_none_match.split(",")}:
        not_modified_headers = dict(headers)
        not_modified_headers.pop("Content-Length", None)
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=not_modified_headers)
    return Response(
        content=b"" if request.method == "HEAD" else asset.content,
        media_type=asset.content_type,
        headers=headers,
    )


@router.put(
    "/ui/api/branding",
    response_model=UIBrandingPayload,
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def update_ui_branding(
    request: Request, payload: UIBrandingUpdatePayload
) -> UIBrandingPayload:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )

    before = _effective_ui_branding(request)
    await dynamic_config.update_config(
        {
            "general_settings": {
                "instance_name": payload.instance_name,
                "ui_branding": payload.model_dump(
                    include={"primary_color", "secondary_color", "menu_hover_color"}
                ),
            }
        },
        updated_by="admin_api",
    )
    after = _effective_ui_branding(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_UI_BRANDING_UPDATE,
        resource_type="ui_branding",
        request_payload=payload.model_dump(),
        response_payload=after.model_dump(),
        before=before.model_dump(),
        after=after.model_dump(),
    )
    return after


@router.post(
    "/ui/api/branding/reset",
    response_model=UIBrandingResetPayload,
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def reset_ui_branding(request: Request) -> UIBrandingResetPayload:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    _branding_asset_service(request)
    before = _effective_ui_branding(request)
    factory = UIBrandingPayload()
    operation_id = str(uuid4())
    audit_enabled = _audit_enabled(request)

    if audit_enabled:
        require_audit_service(getattr(request.app.state, "audit_service", None))
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_UI_BRANDING_RESET,
            resource_type="ui_branding",
            request_payload={"target": "factory_defaults"},
            before=before.model_dump(),
            metadata={"operation_id": operation_id, "phase": "attempt"},
            status="attempted",
            critical=True,
            force_sync=True,
            event_id=_reset_audit_event_id(operation_id, "attempt"),
        )

    async def delete_assets(db_client: BrandingAssetDatabase) -> None:
        await UIBrandingAssetRepository(db_client).delete_all_known()

    try:
        await dynamic_config.update_config(
            {
                "general_settings": {
                    "instance_name": factory.instance_name,
                    "ui_branding": factory.model_dump(exclude={"instance_name"}),
                }
            },
            updated_by="admin_api",
            transaction_mutation=delete_assets,
        )
        after = _effective_ui_branding(request)
        reconciliation_pending = False
    except DynamicConfigPostCommitApplyError as exc:
        after = _ui_branding_from_app_config(exc.committed_app_config)
        reconciliation_pending = True
    except Exception as exc:
        if audit_enabled:
            await _emit_branding_reset_outcome_audit(
                request=request,
                request_start=request_start,
                operation_id=operation_id,
                before=before,
                after=None,
                reconciliation_pending=False,
                error=exc,
            )
        raise

    if audit_enabled:
        await _emit_branding_reset_outcome_audit(
            request=request,
            request_start=request_start,
            operation_id=operation_id,
            before=before,
            after=after,
            reconciliation_pending=reconciliation_pending,
        )
    return UIBrandingResetPayload(
        **after.model_dump(),
        reconciliation_pending=reconciliation_pending,
    )


@router.put(
    "/ui/api/branding/assets/{asset_key}",
    response_model=UIBrandingPayload,
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def upload_ui_branding_asset(
    request: Request,
    asset_key: str,
    file: UploadFile = File(...),
) -> UIBrandingPayload:
    request_start = perf_counter()
    try:
        normalized_key = normalize_asset_kind(asset_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Branding asset not found"
        ) from exc

    try:
        content = await file.read(BRANDING_ASSET_MAX_BYTES + 1)
    finally:
        await file.close()
    try:
        asset = validate_branding_asset(normalized_key, content, original_filename=file.filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    _branding_asset_service(request)
    before = _effective_ui_branding(request)
    asset_url = branding_asset_url(normalized_key, asset.content_sha256)

    async def persist_asset(db_client: BrandingAssetDatabase) -> None:
        await UIBrandingAssetRepository(db_client).upsert(
            asset_key=asset.asset_key,
            content_type=asset.content_type,
            content=asset.content,
            content_sha256=asset.content_sha256,
            size_bytes=asset.size_bytes,
            original_filename=asset.original_filename,
            updated_by="admin_api",
        )

    await dynamic_config.update_config(
        {
            "general_settings": {
                "ui_branding": {branding_asset_config_field(normalized_key): asset_url},
            }
        },
        updated_by="admin_api",
        transaction_mutation=persist_asset,
    )
    after = _effective_ui_branding(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_UI_BRANDING_ASSET_UPLOAD,
        resource_type="ui_branding_asset",
        resource_id=normalized_key,
        request_payload={
            "asset_key": normalized_key,
            "content_type": asset.content_type,
            "content_sha256": asset.content_sha256,
            "size_bytes": asset.size_bytes,
            "original_filename": asset.original_filename,
        },
        response_payload=after.model_dump(),
        before=before.model_dump(),
        after=after.model_dump(),
    )
    return after


@router.delete(
    "/ui/api/branding/assets/{asset_key}",
    response_model=UIBrandingPayload,
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def delete_ui_branding_asset(request: Request, asset_key: str) -> UIBrandingPayload:
    request_start = perf_counter()
    try:
        normalized_key = normalize_asset_kind(asset_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Branding asset not found"
        ) from exc

    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    _branding_asset_service(request)
    before = _effective_ui_branding(request)

    async def delete_asset(db_client: BrandingAssetDatabase) -> None:
        await UIBrandingAssetRepository(db_client).delete(normalized_key)

    await dynamic_config.update_config(
        {
            "general_settings": {
                "ui_branding": {branding_asset_config_field(normalized_key): None},
            }
        },
        updated_by="admin_api",
        transaction_mutation=delete_asset,
    )
    after = _effective_ui_branding(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_UI_BRANDING_ASSET_DELETE,
        resource_type="ui_branding_asset",
        resource_id=normalized_key,
        request_payload={"asset_key": normalized_key},
        response_payload=after.model_dump(),
        before=before.model_dump(),
        after=after.model_dump(),
    )
    return after


@router.get(
    "/ui/api/routing", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]
)
async def get_routing(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    router_settings = getattr(app_config, "router_settings", None)
    general_settings = getattr(app_config, "general_settings", None)

    health_handler = getattr(request.app.state, "router_health_handler", None)
    health_payload = None
    if health_handler is not None:
        health_payload = await health_handler.get_health_status()

    deployments: list[dict[str, Any]] = []
    health_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(health_payload, dict):
        health_by_id = {
            str(item.get("deployment_id")): item for item in health_payload.get("deployments", [])
        }

    for model_name, entries in getattr(request.app.state, "model_registry", {}).items():
        for index, entry in enumerate(entries):
            deployment_id = str(entry.get("deployment_id") or f"{model_name}-{index}")
            params = dict(entry.get("deltallm_params", {}))
            health = health_by_id.get(deployment_id, {})
            deployments.append(
                {
                    "deployment_id": deployment_id,
                    "model": model_name,
                    "provider": resolve_provider(params),
                    "status": "healthy" if bool(health.get("healthy", True)) else "degraded",
                    "latency_ms": health.get("avg_latency_ms"),
                    "last_check": health.get("last_success_at") or health.get("last_error_at"),
                }
            )

    fallback_map = {}
    failover_manager = getattr(request.app.state, "failover_manager", None)
    config = getattr(failover_manager, "config", None)
    if config is not None and isinstance(getattr(config, "fallbacks", None), dict):
        fallback_map = config.fallbacks

    failover_chains = [
        {"model_group": model, "chain": [model, *fallbacks]}
        for model, fallbacks in fallback_map.items()
    ]
    for model_name in getattr(request.app.state, "model_registry", {}):
        if model_name not in fallback_map:
            failover_chains.append({"model_group": model_name, "chain": [model_name]})

    return {
        "strategy": str(getattr(router_settings, "routing_strategy", "simple-shuffle")),
        "available_strategies": [
            "simple-shuffle",
            "least-busy",
            "latency-based-routing",
            "cost-based-routing",
            "usage-based-routing",
            "priority-based-routing",
            "weighted",
            "rate-limit-aware",
        ],
        "config": {
            "timeout": getattr(router_settings, "timeout", 600),
            "retries": getattr(router_settings, "num_retries", 0),
            "cooldown": getattr(router_settings, "cooldown_time", 60),
            "retry_after": getattr(router_settings, "retry_after", 0),
            "health_check_enabled": getattr(general_settings, "background_health_checks", False),
            "health_check_interval": getattr(general_settings, "health_check_interval", 300),
        },
        "deployments": deployments,
        "failover_chains": failover_chains,
    }


@router.put(
    "/ui/api/routing", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]
)
async def update_routing(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    before = await get_routing(request)

    config_update: dict[str, Any] = {}
    router_updates: dict[str, Any] = {}
    general_updates: dict[str, Any] = {}

    strategy = payload.get("strategy")
    if isinstance(strategy, str) and strategy:
        router_updates["routing_strategy"] = strategy

    config_fields = payload.get("config")
    if isinstance(config_fields, dict):
        if "timeout" in config_fields:
            router_updates["timeout"] = float(config_fields["timeout"])
        if "retries" in config_fields:
            router_updates["num_retries"] = int(config_fields["retries"])
        if "cooldown" in config_fields:
            router_updates["cooldown_time"] = int(config_fields["cooldown"])
        if "retry_after" in config_fields:
            router_updates["retry_after"] = float(config_fields["retry_after"])
        if "health_check_enabled" in config_fields:
            general_updates["background_health_checks"] = bool(
                config_fields["health_check_enabled"]
            )
        if "health_check_interval" in config_fields:
            general_updates["health_check_interval"] = int(config_fields["health_check_interval"])

    if router_updates:
        config_update["router_settings"] = router_updates
    if general_updates:
        config_update["general_settings"] = general_updates

    if config_update:
        try:
            await dynamic_config.update_config(config_update, updated_by="admin_api")
        except DynamicConfigRestartRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "restart_required", "message": str(exc)},
            ) from exc

    response = await get_routing(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="routing_config",
        request_payload=payload,
        response_payload=response,
        before=before,
        after=response,
    )
    return response


@router.get(
    "/ui/api/settings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))]
)
async def get_settings(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {}

    scope = get_auth_scope(request, authorization, x_master_key)
    general = to_json_value(app_config.general_settings.model_dump())
    if not scope.is_platform_admin:
        general.pop("master_key", None)

    return {
        "general_settings": general,
        "router_settings": to_json_value(app_config.router_settings.model_dump()),
        "deltallm_settings": to_json_value(app_config.deltallm_settings.model_dump()),
        "model_count": len(model_entries(request.app)),
    }


@router.put(
    "/ui/api/settings", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]
)
async def update_settings(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    before = await get_settings(request, authorization=authorization, x_master_key=x_master_key)

    config_update: dict[str, Any] = {}

    general_updates = (
        payload.get("general_settings") if isinstance(payload.get("general_settings"), dict) else {}
    )
    router_updates = (
        payload.get("router_settings") if isinstance(payload.get("router_settings"), dict) else {}
    )
    deltallm_updates = (
        payload.get("deltallm_settings")
        if isinstance(payload.get("deltallm_settings"), dict)
        else {}
    )

    if general_updates:
        config_update["general_settings"] = general_updates
    if router_updates:
        config_update["router_settings"] = router_updates
    if deltallm_updates:
        if "guardrails" in deltallm_updates and isinstance(deltallm_updates["guardrails"], list):
            deltallm_updates["guardrails"] = [
                {
                    "guardrail_name": str(item.get("guardrail_name")),
                    "deltallm_params": item.get("deltallm_params", {}),
                }
                for item in deltallm_updates["guardrails"]
                if isinstance(item, dict) and isinstance(item.get("deltallm_params"), dict)
            ]
        config_update["deltallm_settings"] = deltallm_updates

    if config_update:
        try:
            await dynamic_config.update_config(config_update, updated_by="admin_api")
        except DynamicConfigRestartRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "restart_required", "message": str(exc)},
            ) from exc

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and "master_key" in general_updates:
        setattr(settings, "master_key", general_updates["master_key"])

    if "log_level" in general_updates:
        level = str(general_updates["log_level"]).upper()
        if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            logging.getLogger().setLevel(getattr(logging, level))

    response = await get_settings(request, authorization=authorization, x_master_key=x_master_key)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_SETTINGS_UPDATE,
        resource_type="app_settings",
        request_payload=payload,
        response_payload=response,
        before=before,
        after=response,
    )
    return response
