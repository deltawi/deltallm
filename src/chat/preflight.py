from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from src.callbacks import CallbackManager
from src.chat.audit import emit_prompt_resolution_audit_event
from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest
from src.routers.routing_decision import set_prompt_provenance
from src.services.model_visibility import ensure_model_allowed, get_callable_target_policy_mode_from_app
from src.services.prompt_registry import apply_route_preferences_to_metadata, parse_prompt_reference


async def run_text_preflight(
    *,
    request: Request,
    payload: ChatCompletionRequest,
    request_data: dict[str, Any] | None,
) -> tuple[Any, ChatCompletionRequest, dict[str, Any], CallbackManager, Any]:
    auth = request.state.user_api_key
    ensure_model_allowed(
        auth,
        payload.model,
        callable_target_grant_service=getattr(request.app.state, "callable_target_grant_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        emit_shadow_log=True,
    )

    from src.routers.utils import enforce_budget_if_configured

    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    guardrail_middleware = request.app.state.guardrail_middleware
    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    data = request_data or payload.model_dump(exclude_none=True)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    explicit_prompt_ref = parse_prompt_reference(metadata.get("prompt_ref")) if isinstance(metadata, dict) else None
    prompt_variables: dict[str, Any] = {}
    if isinstance(metadata, dict):
        if isinstance(metadata.get("prompt_variables"), dict):
            prompt_variables.update(metadata["prompt_variables"])
        if explicit_prompt_ref is not None and isinstance(explicit_prompt_ref.variables, dict):
            prompt_variables.update(explicit_prompt_ref.variables)

    prompt_registry = getattr(request.app.state, "prompt_registry_service", None)
    if prompt_registry is not None and callable(getattr(prompt_registry, "resolve_and_render", None)):
        prompt_started = perf_counter()
        try:
            resolved = await prompt_registry.resolve_and_render(
                explicit_reference=explicit_prompt_ref,
                variables=prompt_variables,
                api_key=getattr(auth, "api_key", None),
                user_id=getattr(auth, "user_id", None),
                team_id=getattr(auth, "team_id", None),
                organization_id=getattr(auth, "organization_id", None),
                route_group_key=str(payload.model),
                model=str(payload.model),
                request_id=request.headers.get("x-request-id"),
                scope_context=getattr(request.state, "runtime_scope_context", None),
            )
        except ValueError as exc:
            emit_prompt_resolution_audit_event(
                request=request,
                auth=auth,
                status="error",
                request_start=prompt_started,
                prompt_key=explicit_prompt_ref.template_key if explicit_prompt_ref is not None else None,
                metadata={
                    "source": "explicit" if explicit_prompt_ref is not None else "binding",
                    "prompt_ref": metadata.get("prompt_ref") if isinstance(metadata, dict) else None,
                },
                error=exc,
            )
            raise InvalidRequestError(message=f"prompt resolution failed: {exc}") from exc
        if resolved is not None:
            existing_messages = data.get("messages") if isinstance(data.get("messages"), list) else []
            data["messages"] = [*resolved.messages, *existing_messages]
            merged_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            merged_metadata, _ = apply_route_preferences_to_metadata(
                merged_metadata,
                resolved.provenance.route_preferences,
            )
            merged_metadata["prompt_provenance"] = resolved.provenance.to_dict()
            data["metadata"] = merged_metadata
            set_prompt_provenance(request, resolved.provenance.to_dict())
            emit_prompt_resolution_audit_event(
                request=request,
                auth=auth,
                status="success",
                request_start=prompt_started,
                prompt_key=resolved.provenance.template_key,
                metadata={
                    "source": resolved.provenance.source,
                    "prompt_provenance": resolved.provenance.to_dict(),
                },
            )
        else:
            set_prompt_provenance(request, None)

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
