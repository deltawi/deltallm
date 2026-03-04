from __future__ import annotations

from typing import Any

from fastapi import Request

from src.callbacks import CallbackManager
from src.models.errors import PermissionDeniedError
from src.models.requests import ChatCompletionRequest


async def run_text_preflight(
    *,
    request: Request,
    payload: ChatCompletionRequest,
    request_data: dict[str, Any] | None,
) -> tuple[Any, ChatCompletionRequest, dict[str, Any], CallbackManager, Any]:
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")

    from src.routers.utils import enforce_budget_if_configured

    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    guardrail_middleware = request.app.state.guardrail_middleware
    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    data = request_data or payload.model_dump(exclude_none=True)
    data = await callback_manager.execute_pre_call_hooks(
        user_api_key_dict=auth.model_dump(mode="json"),
        cache=getattr(request.state, "cache_context", None),
        data=data,
        call_type="completion",
    )
    data = await guardrail_middleware.run_pre_call(
        request_data=data,
        user_api_key_dict=auth.model_dump(mode="python"),
        call_type="completion",
    )
    return auth, ChatCompletionRequest.model_validate(data), data, callback_manager, guardrail_middleware
