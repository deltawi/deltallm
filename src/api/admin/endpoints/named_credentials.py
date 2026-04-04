from __future__ import annotations

from time import perf_counter
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, to_json_value
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.named_credentials import NamedCredentialRecord, NamedCredentialRepository
from src.db.repositories import ModelDeploymentRepository
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context
from src.services.named_credentials import (
    canonicalize_named_credential_provider,
    clear_connection_fields,
    connection_fingerprint,
    redact_connection_config,
    extract_connection_config_from_params,
    named_credential_provider_for_params,
    normalize_named_credential_payload,
    serialize_named_credential,
)

router = APIRouter(tags=["Named Credentials"])


def _repository_or_503(request: Request) -> NamedCredentialRepository:
    repository = getattr(request.app.state, "named_credential_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Named credential repository unavailable")
    return repository


def _model_repository_or_503(request: Request) -> ModelDeploymentRepository:
    repository = getattr(request.app.state, "model_deployment_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model deployment repository unavailable")
    return repository


async def _serialize_with_usage(
    repository: NamedCredentialRepository,
    record: NamedCredentialRecord,
) -> dict[str, Any]:
    payload = to_json_value(serialize_named_credential(record))
    payload["usage_count"] = await repository.count_linked_deployments(record.credential_id)
    return payload


async def _reload_runtime_if_in_use(request: Request, credential_id: str, repository: NamedCredentialRepository) -> None:
    usage_count = await repository.count_linked_deployments(credential_id)
    if usage_count <= 0:
        return
    hot_reload = getattr(request.app.state, "model_hot_reload_manager", None)
    if hot_reload is not None:
        await hot_reload.reload_runtime()


def _inline_report_item(
    *,
    provider: str,
    fingerprint: str,
    connection_config: dict[str, Any],
    deployments: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "provider": provider,
        "connection_config": to_json_value(redact_connection_config(connection_config)),
        "credentials_present": True,
        "deployment_count": len(deployments),
        "deployments": deployments,
    }


@router.get("/ui/api/named-credentials", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_named_credentials(request: Request, provider: str | None = None) -> dict[str, Any]:
    repository = _repository_or_503(request)
    records = await repository.list_all(
        provider=canonicalize_named_credential_provider(provider) or None,
    )
    usage_counts = await repository.list_usage_counts()
    return {
        "data": [
            {
                **to_json_value(serialize_named_credential(record)),
                "usage_count": usage_counts.get(record.credential_id, 0),
            }
            for record in records
        ]
    }


@router.get("/ui/api/named-credentials/inline-report", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def inline_named_credential_report(request: Request) -> dict[str, Any]:
    repository = _model_repository_or_503(request)
    records = await repository.list_all()

    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.named_credential_id:
            continue
        provider = named_credential_provider_for_params(record.deltallm_params)
        if not provider:
            continue
        connection_config = extract_connection_config_from_params(record.deltallm_params)
        if not connection_config:
            continue
        fingerprint = connection_fingerprint(provider, connection_config)
        item = grouped.setdefault(
            fingerprint,
            {
                "provider": provider,
                "connection_config": connection_config,
                "deployments": [],
            },
        )
        item["deployments"].append(
            {
                "deployment_id": record.deployment_id,
                "model_name": record.model_name,
            }
        )

    data = [
        _inline_report_item(
            provider=str(item["provider"]),
            fingerprint=fingerprint,
            connection_config=item["connection_config"],
            deployments=sorted(item["deployments"], key=lambda deployment: deployment["model_name"]),
        )
        for fingerprint, item in grouped.items()
    ]
    data.sort(key=lambda item: (-int(item["deployment_count"]), str(item["provider"])))
    return {"data": data}


@router.get("/ui/api/named-credentials/{credential_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_named_credential(request: Request, credential_id: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    record = await repository.get_by_id(credential_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Named credential not found")

    payload = await _serialize_with_usage(repository, record)
    payload["linked_deployments"] = to_json_value(await repository.list_linked_deployments(credential_id))
    return payload


@router.post("/ui/api/named-credentials", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def create_named_credential(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    name, provider, connection_config, metadata = normalize_named_credential_payload(payload)

    existing = await repository.get_by_name(name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A named credential with this name already exists")

    context = get_platform_auth_context(request)
    record = await repository.create(
        NamedCredentialRecord(
            credential_id=str(payload.get("credential_id") or uuid4()),
            name=name,
            provider=provider,
            connection_config=connection_config,
            metadata=metadata,
            created_by_account_id=getattr(context, "account_id", None),
        )
    )
    response = await _serialize_with_usage(repository, record)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_NAMED_CREDENTIAL_CREATE,
        resource_type="named_credential",
        resource_id=record.credential_id,
        request_payload=payload,
        response_payload=response,
        before=None,
        after=response,
    )
    return response


@router.put("/ui/api/named-credentials/{credential_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_named_credential(request: Request, credential_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    existing = await repository.get_by_id(credential_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Named credential not found")

    name, provider, connection_config, metadata = normalize_named_credential_payload(payload, existing=existing)
    by_name = await repository.get_by_name(name)
    if by_name is not None and by_name.credential_id != credential_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A named credential with this name already exists")

    updated = await repository.update(
        credential_id,
        name=name,
        provider=provider,
        connection_config=connection_config,
        metadata=metadata,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Named credential not found")

    await _reload_runtime_if_in_use(request, credential_id, repository)
    response = await _serialize_with_usage(repository, updated)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_NAMED_CREDENTIAL_UPDATE,
        resource_type="named_credential",
        resource_id=credential_id,
        request_payload=payload,
        response_payload=response,
        before=serialize_named_credential(existing),
        after=response,
    )
    return response


@router.delete("/ui/api/named-credentials/{credential_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_named_credential(request: Request, credential_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    existing = await repository.get_by_id(credential_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Named credential not found")

    usage_count = await repository.count_linked_deployments(credential_id)
    if usage_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Named credential is still linked to model deployments",
        )

    deleted = await repository.delete(credential_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Named credential not found")

    response = {"deleted": True, "credential_id": credential_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_NAMED_CREDENTIAL_DELETE,
        resource_type="named_credential",
        resource_id=credential_id,
        response_payload=response,
        before=serialize_named_credential(existing),
        after=response,
    )
    return response


@router.post("/ui/api/named-credentials/convert-inline-group", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def convert_inline_group_to_named_credential(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    named_repo = _repository_or_503(request)
    model_repo = _model_repository_or_503(request)
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)

    name = str(payload.get("name") or "").strip()
    provider = canonicalize_named_credential_provider(payload.get("provider"))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if not provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider is required")
    deployment_ids = payload.get("deployment_ids")
    fingerprint = str(payload.get("fingerprint") or "").strip()
    if not isinstance(deployment_ids, list) or not deployment_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deployment_ids must be a non-empty array")
    if not fingerprint:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fingerprint is required")

    if await named_repo.get_by_name(name) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A named credential with this name already exists")

    records = await model_repo.list_by_deployment_ids([str(item) for item in deployment_ids])
    if len(records) != len({str(item).strip() for item in deployment_ids if str(item).strip()}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more deployment_ids are invalid")

    for record in records:
        if record.named_credential_id is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="One or more deployments already use a named credential")

    expected_fingerprint = fingerprint
    baseline_config: dict[str, Any] | None = None
    for record in records:
        record_provider = named_credential_provider_for_params(record.deltallm_params)
        if record_provider != provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All deployments must use the same provider")
        record_connection_config = extract_connection_config_from_params(record.deltallm_params)
        if connection_fingerprint(provider, record_connection_config) != expected_fingerprint:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Deployments do not share the same inline connection config")
        if baseline_config is None:
            baseline_config = record_connection_config

    if baseline_config is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No inline connection config found for the selected deployments")

    name, provider, baseline_config, metadata = normalize_named_credential_payload(
        {
            "name": name,
            "provider": provider,
            "connection_config": baseline_config,
            "metadata": metadata,
        }
    )

    context = get_platform_auth_context(request)
    created_credential_id = str(payload.get("credential_id") or uuid4())

    async def _apply_conversion(
        named_repository: Any,
        deployment_repository: Any,
        *,
        rollback_on_error: bool,
    ) -> NamedCredentialRecord:
        updated_records: list[Any] = []
        created = await named_repository.create(
            NamedCredentialRecord(
                credential_id=created_credential_id,
                name=name,
                provider=provider,
                connection_config=baseline_config,
                metadata=metadata,
                created_by_account_id=getattr(context, "account_id", None),
            )
        )

        try:
            for record in records:
                updated = await deployment_repository.update(
                    record.deployment_id,
                    model_name=record.model_name,
                    named_credential_id=created.credential_id,
                    deltallm_params=clear_connection_fields(record.deltallm_params),
                    model_info=record.model_info,
                )
                if updated is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Failed to update one or more deployments during conversion",
                    )
                updated_records.append(record)
        except Exception:
            if rollback_on_error:
                update = getattr(deployment_repository, "update", None)
                if callable(update):
                    for record in reversed(updated_records):
                        try:
                            await update(
                                record.deployment_id,
                                model_name=record.model_name,
                                named_credential_id=record.named_credential_id,
                                deltallm_params=record.deltallm_params,
                                model_info=record.model_info,
                            )
                        except Exception:
                            pass
                delete = getattr(named_repository, "delete", None)
                if callable(delete):
                    await delete(created.credential_id)
            raise
        return created

    created: NamedCredentialRecord
    if hasattr(db, "tx"):
        async with db.tx() as tx:
            created = await _apply_conversion(
                NamedCredentialRepository(tx),
                ModelDeploymentRepository(tx),
                rollback_on_error=False,
            )
    else:
        created = await _apply_conversion(
            named_repo,
            model_repo,
            rollback_on_error=True,
        )

    hot_reload = getattr(request.app.state, "model_hot_reload_manager", None)
    if hot_reload is not None:
        await hot_reload.reload_runtime()

    response = {
        "credential": await _serialize_with_usage(named_repo, created),
        "converted_deployments": [
            {"deployment_id": record.deployment_id, "model_name": record.model_name}
            for record in records
        ],
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_NAMED_CREDENTIAL_CONVERT_INLINE,
        resource_type="named_credential_conversion",
        resource_id=created.credential_id,
        request_payload=payload,
        response_payload=response,
    )
    return response
