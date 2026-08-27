from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from fastapi import Request
from pydantic import ValidationError

from src.callbacks import CallbackManager
from src.chat.audit import request_client_ip
from src.models.errors import InvalidRequestError
from src.models.request_serialization import dump_request_for_preflight
from src.models.requests import ChatCompletionRequest
from src.middleware.rate_limit import (
    _release_rate_limits,
    acquire_parallel_limits_for_payload,
    check_and_acquire_rate_limits_for_payload,
)
from src.metrics import observe_request_phase
from src.routers.routing_decision import set_prompt_provenance
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)
from src.services.prompt_registry import apply_route_preferences_to_metadata, parse_prompt_reference
from src.services.preflight_capacity import acquire_preflight_capacity, release_preflight_capacity


@dataclass(frozen=True, slots=True)
class TextPreflightResult:
    auth: Any
    payload: ChatCompletionRequest
    request_data: dict[str, Any]
    callback_manager: CallbackManager
    guardrail_middleware: Any


async def run_text_preflight(
    *,
    request: Request,
    payload: ChatCompletionRequest,
    request_data: dict[str, Any] | None,
    routing_runtime: RoutingRuntimeGeneration,
) -> tuple[Any, ChatCompletionRequest, dict[str, Any], CallbackManager, Any]:
    prepared = getattr(request.state, "prepared_text_request", None)
    if isinstance(prepared, TextPreflightResult):
        return (
            prepared.auth,
            prepared.payload,
            dict(prepared.request_data),
            prepared.callback_manager,
            prepared.guardrail_middleware,
        )

    auth = request.state.user_api_key
    capacity_started = perf_counter()
    try:
        await acquire_preflight_capacity(request, auth=auth)
    except Exception:
        _observe_preflight_phase(
            phase="capacity_admission",
            started=capacity_started,
            outcome="error",
            response_kind=_response_kind(payload),
        )
        raise
    _observe_preflight_phase(
        phase="capacity_admission",
        started=capacity_started,
        outcome="success",
        response_kind=_response_kind(payload),
    )
    guardrail_middleware = request.app.state.guardrail_middleware
    callback_manager: CallbackManager = getattr(
        request.app.state, "callback_manager", CallbackManager()
    )
    data = dict(request_data) if request_data is not None else dump_request_for_preflight(payload)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    explicit_prompt_ref = (
        parse_prompt_reference(metadata.get("prompt_ref")) if isinstance(metadata, dict) else None
    )
    prompt_variables: dict[str, Any] = {}
    if isinstance(metadata, dict):
        if isinstance(metadata.get("prompt_variables"), dict):
            prompt_variables.update(metadata["prompt_variables"])
        if explicit_prompt_ref is not None and isinstance(explicit_prompt_ref.variables, dict):
            prompt_variables.update(explicit_prompt_ref.variables)

    prompt_registry = getattr(request.app.state, "prompt_registry_service", None)
    if prompt_registry is not None and callable(
        getattr(prompt_registry, "resolve_and_render", None)
    ):
        prompt_started = perf_counter()
        route_group_key = _route_group_key_for_model(request, str(payload.model))
        try:
            resolved = await prompt_registry.resolve_and_render(
                explicit_reference=explicit_prompt_ref,
                variables=prompt_variables,
                api_key=getattr(auth, "api_key", None),
                user_id=getattr(auth, "user_id", None),
                team_id=getattr(auth, "team_id", None),
                organization_id=getattr(auth, "organization_id", None),
                route_group_key=route_group_key,
                model=str(payload.model),
                request_id=request.headers.get("x-request-id"),
                client_ip=request_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                scope_context=getattr(request.state, "runtime_scope_context", None),
            )
        except ValueError as exc:
            _observe_preflight_phase(
                phase="prompt",
                started=prompt_started,
                outcome="error",
                response_kind=_response_kind(payload),
            )
            raise InvalidRequestError(message=f"prompt resolution failed: {exc}") from exc
        except Exception:
            _observe_preflight_phase(
                phase="prompt",
                started=prompt_started,
                outcome="error",
                response_kind=_response_kind(payload),
            )
            raise
        if resolved is not None:
            existing_messages = (
                data.get("messages") if isinstance(data.get("messages"), list) else []
            )
            data["messages"] = [*resolved.messages, *existing_messages]
            merged_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            merged_metadata, _ = apply_route_preferences_to_metadata(
                merged_metadata,
                resolved.provenance.route_preferences,
            )
            merged_metadata["prompt_provenance"] = resolved.provenance.to_dict()
            data["metadata"] = merged_metadata
            set_prompt_provenance(request, resolved.provenance.to_dict())
        else:
            set_prompt_provenance(request, None)
        _observe_preflight_phase(
            phase="prompt",
            started=prompt_started,
            outcome="success",
            response_kind=_response_kind(payload),
        )

    hooks_started = perf_counter()
    try:
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
    except Exception:
        _observe_preflight_phase(
            phase="hooks_guardrails",
            started=hooks_started,
            outcome="error",
            response_kind=_response_kind(payload),
        )
        raise
    _observe_preflight_phase(
        phase="hooks_guardrails",
        started=hooks_started,
        outcome="success",
        response_kind=_response_kind(payload),
    )
    try:
        transformed_payload = ChatCompletionRequest.model_validate(data)
    except ValidationError as exc:
        raise InvalidRequestError(
            message="Request data was invalid after pre-call policy processing"
        ) from exc
    transformed_data = dump_request_for_preflight(transformed_payload)

    authorization_started = perf_counter()
    try:
        ensure_model_allowed(
            auth,
            transformed_payload.model,
            callable_target_grant_service=getattr(
                request.app.state, "callable_target_grant_service", None
            ),
            callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            policy_mode=get_callable_target_policy_mode_from_app(request.app),
            tier_policy_mode=get_tier_policy_mode_from_app(request.app),
            tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(
                request.app
            ),
            emit_shadow_log=True,
        )
    except Exception:
        _observe_preflight_phase(
            phase="authorization",
            started=authorization_started,
            outcome="error",
            response_kind=_response_kind(transformed_payload),
        )
        raise
    _observe_preflight_phase(
        phase="authorization",
        started=authorization_started,
        outcome="success",
        response_kind=_response_kind(transformed_payload),
    )

    from src.routers.utils import enforce_budget_if_configured

    parallel_started = perf_counter()
    try:
        await acquire_parallel_limits_for_payload(
            request,
            model=transformed_payload.model,
            payload=transformed_data,
        )
    except Exception:
        _observe_preflight_phase(
            phase="final_parallel_admission",
            started=parallel_started,
            outcome="error",
            response_kind=_response_kind(transformed_payload),
        )
        raise
    _observe_preflight_phase(
        phase="final_parallel_admission",
        started=parallel_started,
        outcome="success",
        response_kind=_response_kind(transformed_payload),
    )

    budget_started = perf_counter()
    try:
        await enforce_budget_if_configured(request, model=transformed_payload.model, auth=auth)
    except Exception:
        await _release_rate_limits(request)
        await release_preflight_capacity(request)
        _observe_preflight_phase(
            phase="budget",
            started=budget_started,
            outcome="error",
            response_kind=_response_kind(transformed_payload),
        )
        raise
    _observe_preflight_phase(
        phase="budget",
        started=budget_started,
        outcome="success",
        response_kind=_response_kind(transformed_payload),
    )

    admission_started = perf_counter()
    try:
        await check_and_acquire_rate_limits_for_payload(
            request,
            model=transformed_payload.model,
            payload=transformed_data,
        )
    except Exception:
        _observe_preflight_phase(
            phase="rate_admission",
            started=admission_started,
            outcome="error",
            response_kind=_response_kind(transformed_payload),
        )
        raise
    finally:
        await release_preflight_capacity(request)
    _observe_preflight_phase(
        phase="rate_admission",
        started=admission_started,
        outcome="success",
        response_kind=_response_kind(transformed_payload),
    )

    result = TextPreflightResult(
        auth=auth,
        payload=transformed_payload,
        request_data=dict(transformed_data),
        callback_manager=callback_manager,
        guardrail_middleware=guardrail_middleware,
    )
    request.state.prepared_text_request = result
    return auth, transformed_payload, dict(transformed_data), callback_manager, guardrail_middleware


def _route_group_key_for_model(request: Request, model: str) -> str | None:
    catalog = getattr(request.app.state, "callable_target_catalog", None)
    if not isinstance(catalog, dict):
        return None
    target = catalog.get(model)
    target_type = getattr(target, "target_type", None)
    if target_type is None and isinstance(target, dict):
        target_type = target.get("target_type")
    return model if target_type == "route_group" else None


def _response_kind(payload: Any) -> str:
    return "stream" if bool(getattr(payload, "stream", False)) else "nonstream"


def _observe_preflight_phase(
    *,
    phase: str,
    started: float,
    outcome: str,
    response_kind: str,
) -> None:
    observe_request_phase(
        route="chat_completions",
        phase=phase,
        outcome=outcome,
        response_kind=response_kind,
        latency_seconds=perf_counter() - started,
    )
