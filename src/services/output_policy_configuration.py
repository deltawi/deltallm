from __future__ import annotations

from src.config import AppConfig
from src.db.output_policy import OutputPolicyDatabase, read_output_policy_presence


async def validate_output_policy_configuration(
    db: OutputPolicyDatabase, config: AppConfig, *, redis_available: bool, degraded_mode: str
) -> None:
    presence = await read_output_policy_presence(db)
    key_enabled, shared_enabled = presence.key_enabled, presence.shared_enabled
    if (key_enabled or shared_enabled) and (not redis_available or degraded_mode != "fail_closed"):
        raise ValueError("Output TPM requires Redis and fail_closed mode")
    settings = config.general_settings
    if shared_enabled and (settings.enable_jwt_auth or settings.custom_auth):
        raise ValueError("Shared output TPM requires stored API-key authentication")
