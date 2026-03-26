from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import Request

from src.models.errors import BudgetExceededError

logger = logging.getLogger(__name__)


_UI_ONLY_DEFAULT_KEYS = frozenset({"available_voices"})


def apply_default_params(
    upstream_payload: dict[str, Any],
    model_info: dict[str, Any],
) -> dict[str, Any]:
    defaults = model_info.get("default_params")
    if not isinstance(defaults, dict) or not defaults:
        return upstream_payload
    for key, value in defaults.items():
        if key in _UI_ONLY_DEFAULT_KEYS:
            continue
        if key not in upstream_payload:
            upstream_payload[key] = value
    return upstream_payload


async def enforce_budget_if_configured(
    request: Request,
    *,
    model: str,
    auth: Any | None = None,
) -> None:
    if bool(getattr(request.state, "budget_checked", False)):
        return
    budget_service = getattr(request.app.state, "budget_service", None)
    auth_ctx = auth or getattr(request.state, "user_api_key", None)
    if budget_service is None or auth_ctx is None:
        request.state.budget_checked = True
        return
    try:
        from src.billing.budget import BudgetExceeded
        await budget_service.check_budgets(
            api_key=getattr(auth_ctx, "api_key", None),
            user_id=getattr(auth_ctx, "user_id", None),
            team_id=getattr(auth_ctx, "team_id", None),
            organization_id=getattr(auth_ctx, "organization_id", None),
            model=model,
        )
    except BudgetExceeded as exc:
        raise BudgetExceededError(
            message=str(exc),
            param=exc.entity_type,
            code="budget_exceeded",
        ) from exc
    request.state.budget_checked = True


def fire_and_forget(coro: Any) -> None:
    task = asyncio.create_task(coro)

    def _on_done(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:  # pragma: no cover
            logger.warning("background side effect failed: %s", exc)

    task.add_done_callback(_on_done)
