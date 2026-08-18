from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from src.services.limit_counter import ParallelLimitCheck, ParallelLimitLease

logger = logging.getLogger(__name__)


async def acquire_preflight_capacity(request: Request, *, auth: Any) -> None:
    """Reserve cheap global/org capacity before entering expensive preflight work."""
    if bool(getattr(request.state, "_preflight_capacity_checked", False)):
        return
    request.state._preflight_capacity_checked = True
    if not _setting(request, "gateway_preflight_capacity_enabled", False):
        return

    checks = [
        ParallelLimitCheck(
            scope="gateway_preflight_global",
            entity_id="global",
            limit=int(_setting(request, "gateway_preflight_global_max_parallel", 300)),
        )
    ]
    organization_id = str(getattr(auth, "organization_id", None) or "").strip()
    organization_limit = int(_setting(request, "gateway_preflight_org_max_parallel", 100))
    if organization_id and organization_limit > 0:
        checks.append(
            ParallelLimitCheck(
                scope="gateway_preflight_org",
                entity_id=organization_id,
                limit=organization_limit,
            )
        )

    leases = await request.app.state.limit_counter.acquire_parallel_leases(
        checks,
        ttl_seconds=int(_setting(request, "gateway_preflight_lease_ttl_seconds", 30)),
    )
    request.state._preflight_capacity_leases = leases


async def release_preflight_capacity(request: Request) -> None:
    leases: tuple[ParallelLimitLease, ...] = getattr(
        request.state,
        "_preflight_capacity_leases",
        (),
    )
    if not leases:
        return
    try:
        await request.app.state.limit_counter.release_parallel_leases(list(leases))
    except Exception:
        # The lease has a short TTL, so a backend failure cannot leak capacity
        # indefinitely. Preserve the request outcome and make the failure visible.
        logger.exception("failed to release gateway preflight capacity lease")
        return
    request.state._preflight_capacity_leases = ()


def _setting(request: Request, name: str, default: Any) -> Any:
    config = getattr(request.app.state, "app_config", None)
    general = getattr(config, "general_settings", None)
    fields_set = getattr(general, "model_fields_set", None)
    if fields_set is None or name in fields_set:
        configured = getattr(general, name, None)
        if configured is not None:
            return configured
    return getattr(getattr(request.app.state, "settings", None), name, default)
