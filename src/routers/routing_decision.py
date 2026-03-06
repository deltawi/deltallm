from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import Request


def _refresh_request_resolution(request: Request) -> dict[str, Any] | None:
    resolution: dict[str, Any] = {}
    decision = getattr(request.state, "route_decision", None)
    if isinstance(decision, dict):
        resolution["routing"] = deepcopy(decision)
    prompt = getattr(request.state, "prompt_provenance", None)
    if isinstance(prompt, dict):
        resolution["prompt"] = deepcopy(prompt)
    request.state.request_resolution = deepcopy(resolution) if resolution else None
    return deepcopy(resolution) if resolution else None


def capture_initial_route_decision(request: Request, request_context: dict[str, Any]) -> dict[str, Any] | None:
    decision = request_context.get("route_decision")
    if not isinstance(decision, dict):
        return None
    stored = deepcopy(decision)
    request.state.route_decision = stored
    _refresh_request_resolution(request)
    return stored


def update_served_route_decision(
    request: Request,
    *,
    primary_deployment_id: str,
    served_deployment_id: str,
) -> dict[str, Any]:
    current = getattr(request.state, "route_decision", None)
    decision = dict(current) if isinstance(current, dict) else {}
    decision["primary_deployment_id"] = primary_deployment_id
    decision["served_deployment_id"] = served_deployment_id
    decision["fallback_used"] = primary_deployment_id != served_deployment_id
    request.state.route_decision = decision
    _refresh_request_resolution(request)
    return decision


def route_decision_metadata(request: Request) -> dict[str, Any] | None:
    decision = getattr(request.state, "route_decision", None)
    if not isinstance(decision, dict):
        return None
    return deepcopy(decision)


def set_prompt_provenance(request: Request, provenance: dict[str, Any] | None) -> dict[str, Any] | None:
    request.state.prompt_provenance = deepcopy(provenance) if isinstance(provenance, dict) else None
    return _refresh_request_resolution(request)


def prompt_provenance_metadata(request: Request) -> dict[str, Any] | None:
    prompt = getattr(request.state, "prompt_provenance", None)
    if not isinstance(prompt, dict):
        return None
    return deepcopy(prompt)


def request_resolution_metadata(request: Request) -> dict[str, Any] | None:
    resolution = getattr(request.state, "request_resolution", None)
    if isinstance(resolution, dict):
        return deepcopy(resolution)
    return _refresh_request_resolution(request)


def attach_route_decision(metadata: dict[str, Any], request: Request) -> dict[str, Any]:
    payload = dict(metadata)
    decision = route_decision_metadata(request)
    if decision is not None:
        payload["routing_decision"] = decision
    prompt = prompt_provenance_metadata(request)
    if prompt is not None:
        payload["prompt_provenance"] = prompt
    resolution = request_resolution_metadata(request)
    if resolution is not None:
        payload["request_resolution"] = resolution
    return payload


def route_decision_headers(request: Request) -> dict[str, str]:
    decision = route_decision_metadata(request)
    if decision is None:
        return {}

    headers: dict[str, str] = {}
    group = decision.get("model_group")
    if isinstance(group, str) and group:
        headers["x-deltallm-route-group"] = group
    strategy = decision.get("strategy")
    if isinstance(strategy, str) and strategy:
        headers["x-deltallm-route-strategy"] = strategy
    served = decision.get("served_deployment_id") or decision.get("selected_deployment_id")
    if isinstance(served, str) and served:
        headers["x-deltallm-route-deployment"] = served
    fallback_used = decision.get("fallback_used")
    if isinstance(fallback_used, bool):
        headers["x-deltallm-route-fallback-used"] = "true" if fallback_used else "false"
    policy_version = decision.get("policy_version")
    if isinstance(policy_version, int):
        headers["x-deltallm-route-policy-version"] = str(policy_version)
    return headers


def route_failover_kwargs(request_context: dict[str, Any]) -> dict[str, Any]:
    policy = request_context.get("route_policy")
    if not isinstance(policy, dict):
        return {}

    kwargs: dict[str, Any] = {}
    timeout_seconds = policy.get("timeout_seconds")
    if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0:
        kwargs["timeout_seconds"] = float(timeout_seconds)

    retry_max_attempts = policy.get("retry_max_attempts")
    if isinstance(retry_max_attempts, int) and retry_max_attempts >= 0:
        kwargs["retry_max_attempts"] = retry_max_attempts

    retryable_classes = policy.get("retryable_error_classes")
    if isinstance(retryable_classes, list):
        normalized = [str(item).strip() for item in retryable_classes if str(item).strip()]
        if normalized:
            kwargs["retryable_error_classes"] = normalized

    return kwargs
