from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from src.router.group_execution import group_preparation_deadline, prepare_group

if TYPE_CHECKING:
    from src.router.failover import FailoverManager
    from src.router.router import Deployment

_INITIAL_DEPLOYMENT_SELECTION_CONTEXT_KEY = "_deltallm_initial_deployment_selection"


class InitialDeploymentRouter(Protocol):
    async def select_deployment(
        self,
        model_group: str,
        request_context: dict[str, object],
    ) -> Deployment | None: ...

    def require_deployment(
        self,
        model_group: str,
        deployment: Deployment | None,
        *,
        request_context: dict[str, object] | None = None,
    ) -> Deployment: ...


class InitialDeploymentSource(StrEnum):
    PRIMARY = "primary"
    CONTEXT_FALLBACK = "context_fallback"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class InitialDeploymentSelection:
    model_group: str
    deployment: Deployment | None
    source: InitialDeploymentSource
    primary_rejection_reason: str | None
    terminal_reason: str | None


def get_initial_deployment_selection(
    request_context: dict[str, object],
) -> InitialDeploymentSelection | None:
    value = request_context.get(_INITIAL_DEPLOYMENT_SELECTION_CONTEXT_KEY)
    return value if isinstance(value, InitialDeploymentSelection) else None


async def select_initial_deployment(
    *,
    router: InitialDeploymentRouter,
    failover_manager: FailoverManager,
    model_group: str,
    request_context: dict[str, object],
) -> InitialDeploymentSelection:
    """Select a primary or an eligible classified context fallback."""

    await prepare_group(request_context, model_group)
    deadline = group_preparation_deadline(request_context)
    selection = router.select_deployment(model_group, request_context)
    selected = await deadline.wait_for(selection) if deadline is not None else await selection
    decision = request_context.get("route_decision")
    primary_rejection_reason = (
        str(decision.get("reason"))
        if selected is None and isinstance(decision, dict) and decision.get("reason") is not None
        else None
    )
    source = (
        InitialDeploymentSource.PRIMARY if selected is not None else InitialDeploymentSource.NONE
    )
    if selected is None:
        selected = await failover_manager.select_context_fallback_for_local_rejection(
            model_group,
            request_context,
        )
        if selected is not None:
            source = InitialDeploymentSource.CONTEXT_FALLBACK
    decision = request_context.get("route_decision")
    selection = InitialDeploymentSelection(
        model_group=model_group,
        deployment=selected,
        source=source,
        primary_rejection_reason=primary_rejection_reason,
        terminal_reason=(
            str(decision.get("reason"))
            if isinstance(decision, dict) and decision.get("reason") is not None
            else None
        ),
    )
    if selection.source is InitialDeploymentSource.CONTEXT_FALLBACK:
        request_context[_INITIAL_DEPLOYMENT_SELECTION_CONTEXT_KEY] = selection
    else:
        request_context.pop(_INITIAL_DEPLOYMENT_SELECTION_CONTEXT_KEY, None)
    return selection


async def require_initial_deployment(
    *,
    router: InitialDeploymentRouter,
    failover_manager: FailoverManager,
    model_group: str,
    request_context: dict[str, object],
) -> Deployment:
    """Select an initial deployment, then apply the router's standard errors."""

    selection = await select_initial_deployment(
        router=router,
        failover_manager=failover_manager,
        model_group=model_group,
        request_context=request_context,
    )
    return router.require_deployment(
        model_group=model_group,
        deployment=selection.deployment,
        request_context=request_context,
    )
