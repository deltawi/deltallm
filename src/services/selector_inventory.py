"""Safe control-plane projections of the immutable physical deployment inventory."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from src.providers.resolution import resolve_provider
from src.router.policy_validation import PolicyMemberInventoryItem
from src.router.router import Deployment
from src.router.selection.qualification import validate_classifier_metadata


class SelectorDeploymentOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deployment_id: str
    model_name: str
    provider: str
    mode: str
    eligible: bool
    unavailable_reason: str | None = None


class SelectorOptionsPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: tuple[SelectorDeploymentOption, ...]
    selected: SelectorDeploymentOption | None
    limit: int
    offset: int
    has_more: bool


def policy_deployment_inventory(
    deployments: Mapping[str, Deployment],
) -> dict[str, PolicyMemberInventoryItem]:
    return {
        key: PolicyMemberInventoryItem(
            deployment_id=key,
            workload_mode=str(target.model_info.get("mode", "chat")),
            model_info=target.model_info,
            provider_model=target.deltallm_params.get("model"),
            provider_name=target.deltallm_params.get("provider"),
        )
        for key, target in deployments.items()
    }


def _option(target: Deployment) -> SelectorDeploymentOption:
    reason = None
    try:
        validate_classifier_metadata(target.deltallm_params, target.model_info)
    except ValueError:
        # Never return Pydantic input values or arbitrary model/provider metadata.
        reason = (
            "Check this deployment's chat capabilities, context limit, provider, "
            "input/output prices and RPM/TPM limits before publishing."
        )
    return SelectorDeploymentOption(
        deployment_id=target.deployment_id,
        model_name=target.model_name,
        provider=resolve_provider(target.deltallm_params),
        mode=str(target.model_info.get("mode", "chat")),
        eligible=reason is None,
        unavailable_reason=reason,
    )


def selector_options(
    deployments: Mapping[str, Deployment],
    *,
    search: str = "",
    selected_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> SelectorOptionsPage:
    needle = search.casefold()
    matches = sorted(
        (
            item
            for item in deployments.values()
            if item.model_info.get("mode", "chat") == "chat"
            and (
                not needle
                or any(
                    needle in value.casefold()
                    for value in (
                        item.deployment_id,
                        item.model_name,
                        resolve_provider(item.deltallm_params),
                    )
                )
            )
        ),
        key=lambda item: (item.model_name, item.deployment_id),
    )
    selected = deployments.get(selected_id) if selected_id else None
    return SelectorOptionsPage(
        data=tuple(_option(item) for item in matches[offset : offset + limit]),
        selected=_option(selected) if selected is not None else None,
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(matches),
    )
