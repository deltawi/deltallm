from __future__ import annotations

from src.config import AppConfig, Settings
from src.db.output_policy import OutputPolicyDatabase, read_output_policy_presence
from src.runtime_settings import resolve_general_setting


def output_policy_configuration_changed(
    current: AppConfig, candidate: AppConfig, runtime_settings: Settings | None
) -> bool:
    before, after = current.general_settings, candidate.general_settings
    return (
        any(
            getattr(before, field) != getattr(after, field)
            for field in ("enable_jwt_auth", "custom_auth", "redis_degraded_mode")
        )
        or any(
            resolve_general_setting(before, runtime_settings, field, default)
            != resolve_general_setting(after, runtime_settings, field, default)
            for field, default in (
                ("tier_policy_mode", "disabled"),
                ("tier_policy_missing_service_mode", "fail_open"),
            )
        )
        or current.router_settings.timeout != candidate.router_settings.timeout
    )


async def validate_output_policy_configuration(
    db: OutputPolicyDatabase,
    config: AppConfig,
    *,
    redis_available: bool,
    degraded_mode: str,
    runtime_settings: Settings | None = None,
) -> None:
    presence = await read_output_policy_presence(db)
    key_enabled, shared_enabled = presence.key_enabled, presence.shared_enabled
    settings = config.general_settings
    tier_mode = resolve_general_setting(settings, runtime_settings, "tier_policy_mode", "disabled")
    tier_missing_mode = resolve_general_setting(
        settings, runtime_settings, "tier_policy_missing_service_mode", "fail_open"
    )
    if presence.tier_enabled and tier_mode == "enforce":
        shared_enabled = True
        if tier_missing_mode != "fail_closed":
            raise ValueError(
                "Tier output TPM requires tier_policy_missing_service_mode=fail_closed"
            )
    if (key_enabled or shared_enabled) and (not redis_available or degraded_mode != "fail_closed"):
        raise ValueError("Output TPM requires Redis and fail_closed mode")
    if shared_enabled and (settings.enable_jwt_auth or settings.custom_auth):
        raise ValueError("Shared output TPM requires stored API-key authentication")
