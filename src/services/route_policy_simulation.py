from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from src.api.admin.route_group_contracts import (
    RoutePolicySimulationAttempt,
    RoutePolicySimulationPrompt,
    RoutePolicySimulationRequest,
    RoutePolicySimulationResponse,
    RoutePolicySimulationSelection,
    RoutePolicySimulationSummary,
)
from src.db.prompt_registry import PromptRegistryRepository
from src.db.route_groups import RouteGroupRepository
from src.models.errors import RateLimitError, ServiceUnavailableError, TimeoutError
from src.router import (
    ROUTING_MODE_CONTEXT_KEY,
    CooldownManager,
    Deployment,
    FailoverManager,
    InitialDeploymentSource,
    Router,
    RouterConfig,
    build_deployment_registry,
    build_route_group_policies,
    select_initial_deployment,
)
from src.router.policy_validation import (
    PolicyMemberInventoryItem,
    merge_context_policy_block,
    merge_policy_members,
    validate_route_policy,
)
from src.router.context_policy import (
    CONTEXT_ROUTING_METRICS_ENABLED_KEY,
    RequestTokenDemand,
    set_request_token_demand,
)
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.router.simulation_state import RoutingSimulationState, RoutingStateSnapshotMiss
from src.services.prompt_registry import apply_route_preferences_to_metadata, parse_prompt_reference

_MAX_SAMPLE_ATTEMPTS = 50


class RoutePolicySimulationError(RuntimeError):
    pass


class RoutePolicySimulationNotFoundError(RoutePolicySimulationError):
    pass


class RoutePolicySimulationInvalidError(RoutePolicySimulationError):
    pass


class RoutePolicySimulationUnavailableError(RoutePolicySimulationError):
    pass


class _SimulationFailoverManager(FailoverManager):
    """Use production failover decisions without sleeps or operational events."""

    def _compute_backoff(self, attempt: int, error: Exception | None = None) -> float:
        del attempt, error
        return 0.0

    def _record_fallback_event(
        self,
        model_group: str,
        from_id: str | None,
        to_id: str | None,
        reason: str,
        classification: str,
        error_msg: str,
        attempt: int,
        success: bool,
    ) -> None:
        del (
            model_group,
            from_id,
            to_id,
            reason,
            classification,
            error_msg,
            attempt,
            success,
        )


@dataclass(slots=True)
class _SimulationCounters:
    initial: Counter[str]
    served: Counter[str]
    reasons: Counter[str]
    terminal: Counter[str]
    selected_requests: int = 0
    no_selection_requests: int = 0
    served_requests: int = 0
    failed_requests: int = 0
    fallback_requests: int = 0
    timed_out_requests: int = 0
    total_attempts: int = 0


@dataclass(frozen=True, slots=True)
class _PolicyMembership:
    inventory: dict[str, PolicyMemberInventoryItem]
    runtime_members: list[dict[str, Any]]


class RoutePolicySimulationService:
    def __init__(
        self,
        *,
        route_groups: RouteGroupRepository,
        runtime: RoutingRuntimeGeneration,
        prompts: PromptRegistryRepository | None = None,
    ) -> None:
        self._route_groups = route_groups
        self._runtime = runtime
        self._prompts = prompts

    async def simulate(
        self,
        group_key: str,
        request: RoutePolicySimulationRequest,
    ) -> RoutePolicySimulationResponse:
        group = await self._route_groups.get_group(group_key)
        if group is None:
            raise RoutePolicySimulationNotFoundError("Route group not found")

        runtime_groups = await self._route_groups.list_runtime_groups()
        membership = await self._policy_membership(group_key)
        warnings: list[str] = []
        if request.policy is not None:
            try:
                normalized, policy_warnings = validate_route_policy(
                    request.policy,
                    available_members=membership.inventory,
                    workload_mode=group.mode,
                )
            except ValueError as exc:
                raise RoutePolicySimulationInvalidError(str(exc)) from exc
            warnings.extend(policy_warnings)
            runtime_groups = _apply_policy_override(
                runtime_groups,
                group_key=group_key,
                policy=normalized,
                base_members=membership.runtime_members,
                base_strategy=group.routing_strategy,
            )

        metadata, prompt, prompt_warnings = await self._resolve_prompt(
            group_key,
            dict(request.metadata),
            request.prompt_ref,
        )
        warnings.extend(prompt_warnings)
        try:
            (
                router,
                failover,
                simulation_state,
                available_outcome_ids,
            ) = await self._build_simulation_runtime(
                group_key=group_key,
                group_mode=str(group.mode or "chat"),
                runtime_groups=runtime_groups,
                metadata=metadata,
                user_id=request.user_id,
                token_demand=RequestTokenDemand(
                    input_tokens=request.input_tokens,
                    requested_output_tokens=request.requested_output_tokens,
                ),
            )
        except ValueError as exc:
            raise RoutePolicySimulationInvalidError(str(exc)) from exc
        except RoutingStateSnapshotMiss as exc:
            raise RoutePolicySimulationUnavailableError(
                "Routing state could not be captured for simulation"
            ) from exc
        outcomes = _outcomes_by_deployment(
            request,
            available_deployment_ids=available_outcome_ids,
        )

        counters = _SimulationCounters(
            initial=Counter(),
            served=Counter(),
            reasons=Counter(),
            terminal=Counter(),
        )
        sample_attempts: list[RoutePolicySimulationAttempt] = []
        sample_decision: dict[str, Any] | None = None

        for iteration in range(1, request.iterations + 1):
            simulation_state.reset_attempt_effects()
            context: dict[str, Any] = {
                "metadata": dict(metadata),
                "user_id": request.user_id,
                ROUTING_MODE_CONTEXT_KEY: str(group.mode or "chat"),
                CONTEXT_ROUTING_METRICS_ENABLED_KEY: False,
            }
            set_request_token_demand(
                context,
                RequestTokenDemand(
                    input_tokens=request.input_tokens,
                    requested_output_tokens=request.requested_output_tokens,
                ),
            )
            initial_selection = await select_initial_deployment(
                router=router,
                failover_manager=failover,
                model_group=group_key,
                request_context=context,
            )
            selected = initial_selection.deployment
            decision = context.get("route_decision")
            if isinstance(decision, dict):
                counters.reasons[str(decision.get("reason") or "unknown")] += 1
                if sample_decision is None:
                    sample_decision = dict(decision)
            if selected is None:
                counters.no_selection_requests += 1
                counters.failed_requests += 1
                counters.terminal["no_selection"] += 1
                continue

            counters.selected_requests += 1
            counters.initial[selected.deployment_id] += 1
            attempt_ids: list[str] = []

            async def execute(deployment: Deployment) -> str:
                outcome = outcomes.get(deployment.deployment_id, "success")
                attempt_ids.append(deployment.deployment_id)
                counters.total_attempts += 1
                if len(sample_attempts) < _MAX_SAMPLE_ATTEMPTS:
                    previous_id = attempt_ids[-2] if len(attempt_ids) > 1 else None
                    if previous_id is None:
                        transition = (
                            "fallback"
                            if initial_selection.source is InitialDeploymentSource.CONTEXT_FALLBACK
                            else "primary"
                        )
                    elif previous_id == deployment.deployment_id:
                        transition = "retry"
                    else:
                        transition = "fallback"
                    sample_attempts.append(
                        RoutePolicySimulationAttempt(
                            iteration=iteration,
                            attempt=len(attempt_ids),
                            deployment_id=deployment.deployment_id,
                            outcome=outcome,
                            transition=transition,
                        )
                    )
                if outcome == "success":
                    return "simulated-success"
                if outcome == "timeout":
                    raise TimeoutError(
                        message="Simulated provider timeout",
                        affects_deployment_health=True,
                    )
                if outcome == "rate_limit":
                    raise RateLimitError(
                        message="Simulated provider rate limit",
                        affects_deployment_health=True,
                    )
                raise ServiceUnavailableError(
                    message="Simulated provider unavailability",
                    affects_deployment_health=True,
                )

            route_policy = context.get("route_policy")
            failover_kwargs = route_policy if isinstance(route_policy, dict) else {}
            try:
                _, served = await failover.execute_with_failover(
                    primary_deployment=selected,
                    model_group=group_key,
                    execute=execute,
                    return_deployment=True,
                    routing_context=context,
                    timeout_seconds=_positive_float(failover_kwargs.get("timeout_seconds")),
                    retry_max_attempts=_nonnegative_int(failover_kwargs.get("retry_max_attempts")),
                    retryable_error_classes=_string_list(
                        failover_kwargs.get("retryable_error_classes")
                    ),
                )
            except TimeoutError:
                counters.failed_requests += 1
                counters.timed_out_requests += 1
                counters.terminal["timeout"] += 1
                continue
            except (RateLimitError, ServiceUnavailableError) as exc:
                counters.failed_requests += 1
                terminal = "rate_limit" if isinstance(exc, RateLimitError) else "unavailable"
                counters.terminal[terminal] += 1
                continue

            counters.served_requests += 1
            counters.served[served.deployment_id] += 1
            counters.terminal["success"] += 1
            if (
                initial_selection.source is InitialDeploymentSource.CONTEXT_FALLBACK
                or served.deployment_id != selected.deployment_id
            ):
                counters.fallback_requests += 1

        return RoutePolicySimulationResponse(
            group_key=group_key,
            iterations=request.iterations,
            warnings=warnings,
            prompt=prompt,
            effective_metadata=metadata,
            summary=RoutePolicySimulationSummary(
                selected_requests=counters.selected_requests,
                no_selection_requests=counters.no_selection_requests,
                served_requests=counters.served_requests,
                failed_requests=counters.failed_requests,
                fallback_requests=counters.fallback_requests,
                timed_out_requests=counters.timed_out_requests,
                total_attempts=counters.total_attempts,
            ),
            reason_counts=dict(counters.reasons),
            selections=_selection_summaries(counters.initial, request.iterations),
            served_deployments=_selection_summaries(counters.served, request.iterations),
            terminal_outcomes=dict(counters.terminal),
            sample_decision=sample_decision,
            sample_attempts=sample_attempts,
        )

    async def _policy_membership(self, group_key: str) -> _PolicyMembership:
        members = await self._route_groups.list_members(group_key)
        inventory = {
            member.deployment_id.strip(): PolicyMemberInventoryItem(
                deployment_id=member.deployment_id.strip(),
                enabled=member.enabled,
            )
            for member in members
            if isinstance(member.deployment_id, str) and member.deployment_id.strip()
        }
        runtime_members = [
            {
                "deployment_id": deployment_id,
                "enabled": member.enabled,
                "weight": member.weight,
                "priority": member.priority,
            }
            for member in members
            if isinstance(member.deployment_id, str)
            and (deployment_id := member.deployment_id.strip())
        ]
        return _PolicyMembership(
            inventory=inventory,
            runtime_members=runtime_members,
        )

    async def _resolve_prompt(
        self,
        group_key: str,
        metadata: dict[str, Any],
        prompt_ref_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], RoutePolicySimulationPrompt | None, list[str]]:
        if prompt_ref_payload is None:
            return metadata, None, []
        prompt_ref = parse_prompt_reference(prompt_ref_payload)
        if prompt_ref is None:
            raise RoutePolicySimulationInvalidError("prompt_ref could not be parsed")
        if self._prompts is None:
            raise RoutePolicySimulationUnavailableError("Prompt registry repository unavailable")
        resolved = await self._prompts.resolve_prompt(
            template_key=prompt_ref.template_key,
            label=prompt_ref.label,
            version=prompt_ref.version,
        )
        if resolved is None:
            raise RoutePolicySimulationInvalidError("prompt_ref could not be resolved")
        try:
            effective, preferences = apply_route_preferences_to_metadata(
                metadata,
                resolved.route_preferences,
            )
        except ValueError as exc:
            raise RoutePolicySimulationInvalidError(str(exc)) from exc
        warnings: list[str] = []
        preferred_group = preferences.get("route_group")
        if isinstance(preferred_group, str) and preferred_group and preferred_group != group_key:
            warnings.append(
                f"prompt route_preferences.route_group={preferred_group!r} is advisory "
                f"and does not override simulation group {group_key!r}"
            )
        return (
            effective,
            RoutePolicySimulationPrompt(
                template_key=resolved.template_key,
                version=resolved.version,
                label=resolved.label or prompt_ref.label,
                route_preferences=preferences,
            ),
            warnings,
        )

    async def _build_simulation_runtime(
        self,
        *,
        group_key: str,
        group_mode: str,
        runtime_groups: list[dict[str, Any]],
        metadata: dict[str, Any],
        user_id: str,
        token_demand: RequestTokenDemand,
    ) -> tuple[Router, FailoverManager, RoutingSimulationState, set[str]]:
        model_registry = {
            key: [dict(entry) for entry in entries]
            for key, entries in self._runtime.model_registry.items()
        }
        deployment_registry = build_deployment_registry(
            model_registry,
            route_groups=runtime_groups,
        )
        snapshot_state = RoutingSimulationState(self._runtime.router.state)
        router = Router(
            strategy=self._runtime.strategy,
            state_backend=snapshot_state,
            config=RouterConfig(
                enable_pre_call_checks=self._runtime.router_config.enable_pre_call_checks,
                model_group_alias=dict(self._runtime.router_config.model_group_alias),
                route_group_policies=build_route_group_policies(runtime_groups),
            ),
            deployment_registry=deployment_registry,
        )
        fallback_groups = list(self._runtime.failover_config.fallbacks.get(group_key, []))
        context_fallback_groups = list(
            self._runtime.failover_config.context_window_fallbacks.get(group_key, [])
        )
        planned_groups = list(
            dict.fromkeys([group_key, *fallback_groups, *context_fallback_groups])
        )
        warm_context: dict[str, Any] = {
            "metadata": dict(metadata),
            "user_id": user_id,
            ROUTING_MODE_CONTEXT_KEY: group_mode,
        }
        set_request_token_demand(warm_context, token_demand)
        await router.plan_deployments(planned_groups, warm_context)
        snapshot_state.freeze()
        cooldown = CooldownManager(
            snapshot_state,
            cooldown_time=self._runtime.cooldown_manager.cooldown_time,
            allowed_fails=self._runtime.cooldown_manager.allowed_fails,
        )
        failover = _SimulationFailoverManager(
            config=replace(self._runtime.failover_config),
            candidate_planner=router,
            state_backend=snapshot_state,
            cooldown_manager=cooldown,
        )
        reachable_ids = {
            deployment.deployment_id
            for model_group in planned_groups
            for deployment in router.deployment_registry.get(model_group, ())
        }
        return router, failover, snapshot_state, reachable_ids


def _apply_policy_override(
    runtime_groups: list[dict[str, Any]],
    *,
    group_key: str,
    policy: dict[str, Any],
    base_members: list[dict[str, Any]],
    base_strategy: str | None,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    found = False
    for group in runtime_groups:
        if str(group.get("key") or "") != group_key:
            updated.append(group)
            continue
        found = True
        patched = dict(group)
        policy_strategy = policy.get("strategy")
        patched["strategy"] = (
            policy_strategy
            if isinstance(policy_strategy, str) and policy_strategy
            else base_strategy
        )
        patched["timeouts"] = policy.get("timeouts")
        patched["retry"] = policy.get("retry")
        context = merge_context_policy_block(group.get("context"), policy)
        if context is None:
            patched.pop("context", None)
        else:
            patched["context"] = context
        patched["members"] = merge_policy_members(
            base_members,
            policy.get("members") if "members" in policy else None,
        )
        updated.append(patched)
    if not found:
        raise RoutePolicySimulationNotFoundError("Route group not found")
    return updated


def _outcomes_by_deployment(
    request: RoutePolicySimulationRequest,
    *,
    available_deployment_ids: set[str],
) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for item in request.outcomes:
        deployment_id = item.deployment_id.strip()
        if deployment_id in outcomes:
            raise RoutePolicySimulationInvalidError(
                f"outcomes contains duplicate deployment_id: {deployment_id}"
            )
        if deployment_id not in available_deployment_ids:
            raise RoutePolicySimulationInvalidError(
                f"outcomes references unknown deployment_id: {deployment_id}"
            )
        outcomes[deployment_id] = item.outcome
    return outcomes


def _selection_summaries(
    counts: Counter[str], iterations: int
) -> list[RoutePolicySimulationSelection]:
    return [
        RoutePolicySimulationSelection(
            deployment_id=deployment_id,
            count=count,
            ratio=count / iterations,
        )
        for deployment_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _positive_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and float(value) > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized or None
