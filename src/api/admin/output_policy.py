"""Validate and persist the caller output policy at the control-plane boundary."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, cast

from fastapi import HTTPException, Request

from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.output_policy import OutputPolicyChange, OutputPolicyDatabase, OutputPolicyScope
from src.db.output_policy import tier_version_has_output_policy
from src.models.errors import InvalidRequestError
from src.models.output_limits import validate_model_output_limits, validate_output_limit

logger = logging.getLogger(__name__)


class OutputPolicyKeyInvalidation(Protocol):
    def require_cache_invalidation_backend(self, *, scope_type: str) -> None: ...
    async def invalidate_keys_for_team(self, team_id: str) -> int: ...
    async def invalidate_keys_for_org(self, organization_id: str) -> int: ...
    async def invalidate_keys_for_user(self, user_id: str) -> int: ...
    async def invalidate_key_cache_by_hash(self, token_hash: str) -> None: ...


async def schedule_output_policy_invalidation(
    tx: OutputPolicyDatabase,
    *,
    request: Request,
    scope: OutputPolicyScope,
    identity: str,
    change: OutputPolicyChange,
) -> None:
    """Queue recovery in the policy transaction, including explicit clears."""
    if not change.changed:
        return
    service = getattr(request.app.state, "cache_invalidation_service", None)
    try:
        record = await CacheInvalidationOutboxRepository(tx).enqueue(
            scope_type="key_hash" if scope == "key" else scope,
            scope_id=identity,
            reason="output_tpm_policy_update",
            max_attempts=int(getattr(service, "max_attempts", 10)),
        )
        if record is None:
            raise RuntimeError("Invalidation was not queued")
    except Exception as exc:
        raise HTTPException(
            503, detail="Output policy invalidation could not be scheduled"
        ) from exc


async def invalidate_output_policy_now(
    request: Request, *, scope: OutputPolicyScope, identity: str
) -> None:
    """Try promptly after commit; the existing outbox worker owns recovery."""
    service = cast(
        OutputPolicyKeyInvalidation | None, getattr(request.app.state, "key_service", None)
    )
    try:
        if service is None:
            raise RuntimeError("Key service is unavailable")
        service.require_cache_invalidation_backend(
            scope_type="key_hash" if scope == "key" else scope
        )
        async with asyncio.timeout(0.5):
            if scope == "team":
                await service.invalidate_keys_for_team(identity)
            elif scope == "organization":
                await service.invalidate_keys_for_org(identity)
            elif scope == "user":
                await service.invalidate_keys_for_user(identity)
            else:
                await service.invalidate_key_cache_by_hash(identity)
    except Exception:
        logger.warning("output_tpm_policy_invalidation_queued", extra={"scope_type": scope})


def output_policy_change(
    request: Request, payload: dict[str, object], *, scope: OutputPolicyScope
) -> OutputPolicyChange:
    present = "output_tpm_limit" in payload
    model_present = "model_output_tpm_limit" in payload
    if model_present and scope not in {"key", "team"}:
        raise HTTPException(400, detail="Model output limits require a key or team scope")
    try:
        value = validate_output_limit(payload["output_tpm_limit"]) if present else None
        model_value = (
            validate_model_output_limits(payload["model_output_tpm_limit"])
            if model_present
            else None
        )
    except InvalidRequestError as exc:
        raise HTTPException(400, detail=exc.message) from exc
    if value is not None or model_value:
        limiter = getattr(request.app.state, "limit_counter", None)
        if limiter is None or limiter.redis is None or limiter.degraded_mode != "fail_closed":
            raise HTTPException(400, detail="Output TPM requires Redis and fail_closed mode")
        config = request.app.state.app_config.general_settings
        if scope != "key" and (config.enable_jwt_auth or config.custom_auth):
            raise HTTPException(
                400, detail="Shared output TPM requires stored API-key authentication"
            )
    return OutputPolicyChange(present, value, model_present, model_value)


def validate_tier_output_write(request: Request, payload: dict[str, object]) -> None:
    if payload.get("output_tpm_limit") is None:
        return
    output_policy_change(request, payload, scope="organization")
    settings = request.app.state.app_config.general_settings
    if (
        settings.tier_policy_mode == "enforce"
        and settings.tier_policy_missing_service_mode != "fail_closed"
    ):
        raise HTTPException(
            400, detail="Tier output TPM requires tier_policy_missing_service_mode=fail_closed"
        )


async def validate_tier_output_activation(request: Request, version_id: str) -> None:
    manager = getattr(request.app.state, "prisma_manager", None)
    db = getattr(manager, "client", None)
    if db is not None and await tier_version_has_output_policy(db, version_id):
        validate_tier_output_write(request, {"output_tpm_limit": 1})
