from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TierPolicyInvalidationResult:
    attempted: bool
    reloaded: bool
    notified: bool
    reason: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempted": self.attempted,
            "reloaded": self.reloaded,
            "notified": self.notified,
            "reason": self.reason,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


async def reload_tier_policy(
    request: Request,
    *,
    notify: bool = True,
) -> TierPolicyInvalidationResult:
    return await reload_tier_policy_for_app(request.app, notify=notify)


async def reload_tier_policy_for_app(
    app: Any,
    *,
    notify: bool = True,
) -> TierPolicyInvalidationResult:
    invalidation = getattr(app.state, "governance_invalidation_service", None)
    service = getattr(app.state, "tier_policy_service", None)
    if service is None and invalidation is not None:
        service = getattr(invalidation, "tier_policy_service", None)

    if _service_mode(service) == "disabled":
        return TierPolicyInvalidationResult(
            attempted=False,
            reloaded=False,
            notified=False,
            reason="tier_policy_disabled",
        )

    if invalidation is not None and callable(getattr(invalidation, "invalidate_local", None)):
        reloaded = True
        local_error: str | None = None
        try:
            await invalidation.invalidate_local("tier_policy")
        except Exception as exc:
            logger.warning("failed reloading local tier policy snapshot: %s", exc)
            reloaded = False
            local_error = str(exc)

        notify_method = getattr(invalidation, "notify", None)
        notify_attempted = bool(notify and callable(notify_method))
        notified = False
        notify_error: str | None = None
        if notify_attempted:
            try:
                notify_result = await notify_method("tier_policy")
            except Exception as exc:
                logger.warning("failed notifying tier policy snapshot invalidation: %s", exc)
                notify_error = str(exc)
            else:
                notified = True if notify_result is None else bool(notify_result)

        return TierPolicyInvalidationResult(
            attempted=True,
            reloaded=reloaded,
            notified=notified,
            reason=_governance_reload_reason(
                reloaded=reloaded,
                notified=notified,
                notify_attempted=notify_attempted,
                notify_error=notify_error,
            ),
            error=_combined_error(
                local_error=local_error,
                notify_error=notify_error,
            ),
        )

    if service is not None and callable(getattr(service, "reload", None)):
        try:
            await service.reload()
        except Exception as exc:
            logger.warning("failed reloading tier policy snapshot: %s", exc)
            return TierPolicyInvalidationResult(
                attempted=True,
                reloaded=False,
                notified=False,
                reason="reload_failed",
                error=str(exc),
            )
        return TierPolicyInvalidationResult(
            attempted=True,
            reloaded=True,
            notified=False,
            reason="reloaded_without_broadcast",
        )

    return TierPolicyInvalidationResult(
        attempted=False,
        reloaded=False,
        notified=False,
        reason="service_unavailable",
    )


def _governance_reload_reason(
    *,
    reloaded: bool,
    notified: bool,
    notify_attempted: bool,
    notify_error: str | None,
) -> str:
    if reloaded:
        if notified:
            return "reloaded_and_notified"
        if notify_error is not None:
            return "remote_notify_failed"
        if notify_attempted:
            return "remote_notify_unavailable"
        return "reloaded"

    if notified:
        return "local_reload_failed_remote_notified"
    if notify_error is not None:
        return "local_reload_failed_remote_notify_failed"
    if notify_attempted:
        return "local_reload_failed_remote_notify_unavailable"
    return "local_reload_failed"


def _combined_error(
    *,
    local_error: str | None,
    notify_error: str | None,
) -> str | None:
    if local_error and notify_error:
        return f"{local_error}; remote notify failed: {notify_error}"
    return local_error or notify_error


def _service_mode(service: Any | None) -> str | None:
    if service is None:
        return None
    mode = str(getattr(service, "mode", "") or "").strip().lower()
    return mode or None
