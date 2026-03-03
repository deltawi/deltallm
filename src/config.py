from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ModelMode = Literal[
    "chat",
    "embedding",
    "image_generation",
    "audio_speech",
    "audio_transcription",
    "rerank",
]


class DeltaLLMParams(BaseModel):
    model: str
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    timeout: int | None = 300
    rpm: int | None = None
    tpm: int | None = None
    weight: int = 1
    stream_timeout: int | None = None
    max_tokens: int | None = None


class ModelInfo(BaseModel):
    mode: ModelMode = "chat"
    weight: int = 1
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None
    batch_input_cost_per_token: float | None = None
    batch_output_cost_per_token: float | None = None
    batch_price_multiplier: float | None = None
    input_cost_per_character: float | None = None
    input_cost_per_second: float | None = None
    input_cost_per_image: float | None = None
    output_cost_per_image: float | None = None
    input_cost_per_audio_token: float | None = None
    output_cost_per_audio_token: float | None = None
    output_vector_size: int | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    max_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    default_params: dict[str, Any] | None = None


class ModelDeployment(BaseModel):
    model_config = {"populate_by_name": True}

    model_name: str
    deltallm_params: DeltaLLMParams = Field(validation_alias=AliasChoices("deltallm_params", "litellm_params"))
    model_info: ModelInfo | None = None
    deployment_id: str | None = None


class RouterSettings(BaseModel):
    routing_strategy: Literal[
        "simple-shuffle",
        "least-busy",
        "latency-based-routing",
        "cost-based-routing",
        "usage-based-routing",
        "tag-based-routing",
        "priority-based-routing",
        "weighted",
        "rate-limit-aware",
    ] = "simple-shuffle"
    num_retries: int = 0
    retry_after: float = 0
    timeout: float = 600
    cooldown_time: int = 60
    allowed_fails: int = 0
    enable_pre_call_checks: bool = False
    model_group_alias: dict[str, str] = Field(default_factory=dict)


class GuardrailConfig(BaseModel):
    model_config = {"populate_by_name": True}

    guardrail_name: str
    deltallm_params: dict[str, Any] = Field(validation_alias=AliasChoices("deltallm_params", "litellm_params"))

    @field_validator("deltallm_params")
    @classmethod
    def validate_deltallm_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "guardrail" not in value:
            raise ValueError("deltallm_params must include 'guardrail' class path")
        mode = value.get("mode")
        if mode is not None and mode not in ("pre_call", "post_call", "during_call"):
            raise ValueError("mode must be pre_call, post_call, or during_call")
        return value


class DeltaLLMSettings(BaseModel):
    fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    context_window_fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    content_policy_fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    guardrails: list[GuardrailConfig] = Field(default_factory=list)
    success_callback: list[str] = Field(default_factory=list)
    failure_callback: list[str] = Field(default_factory=list)
    callbacks: list[str] = Field(default_factory=list)
    callback_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    turn_off_message_logging: bool = False


def _validate_master_key_strength(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) < 32:
        raise ValueError("master_key must be at least 32 characters long")
    has_letter = any(char.isalpha() for char in normalized)
    has_digit = any(char.isdigit() for char in normalized)
    if not (has_letter and has_digit):
        raise ValueError("master_key must include both letters and digits")
    return normalized


class GeneralSettings(BaseModel):
    instance_name: str = "DeltaLLM"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    master_key: str | None = None
    deltallm_key_header_name: str = "Authorization"
    salt_key: str | None = None
    database_url: str | None = None
    db_pool_size: int = 20
    db_pool_timeout: int = 30
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_url: str | None = None
    redis_degraded_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    cache_enabled: bool = False
    cache_backend: Literal["memory", "redis", "s3"] = "memory"
    cache_ttl: int = 3600
    cache_max_size: int = 10000
    background_health_checks: bool = False
    health_check_interval: int = 300
    health_check_model: str = "gpt-3.5-turbo"
    prometheus_endpoint: str = "/metrics"
    metrics_retention_days: int = 30
    enable_sso: bool = False
    sso_provider: Literal["microsoft", "google", "okta", "oidc", "saml"] = "oidc"
    sso_client_id: str | None = None
    sso_client_secret: str | None = None
    sso_authorize_url: str | None = None
    sso_token_url: str | None = None
    sso_userinfo_url: str | None = None
    sso_redirect_uri: str | None = None
    sso_scope: str = "openid email profile"
    sso_admin_email_list: list[str] = Field(default_factory=list)
    sso_default_team_id: str | None = None
    enable_jwt_auth: bool = False
    jwt_public_key_url: str | None = None
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    jwt_claims_mapping: dict[str, str] = Field(default_factory=dict)
    custom_auth: str | None = None
    platform_bootstrap_admin_email: str | None = None
    platform_bootstrap_admin_password: str | None = None
    auth_session_ttl_hours: int = 12
    api_key_auth_cache_ttl_seconds: int = 300
    model_deployment_source: Literal["hybrid", "db_only", "config_only"] = "hybrid"
    model_deployment_bootstrap_from_config: bool = True
    embeddings_batch_enabled: bool = False
    embeddings_batch_worker_enabled: bool = True
    embeddings_batch_storage_dir: str = ".deltallm/batch-artifacts"
    embeddings_batch_poll_interval_seconds: float = 1.0
    embeddings_batch_item_claim_limit: int = 20
    embeddings_batch_max_attempts: int = 3
    batch_completed_artifact_retention_days: int = 7
    batch_failed_artifact_retention_days: int = 14
    batch_metadata_retention_days: int = 30
    embeddings_batch_gc_enabled: bool = True
    embeddings_batch_gc_interval_seconds: float = 86400.0
    embeddings_batch_gc_scan_limit: int = 200
    audit_enabled: bool = True
    audit_retention_worker_enabled: bool = True
    audit_retention_interval_seconds: float = 86400.0
    audit_retention_scan_limit: int = 500
    audit_metadata_retention_days: int = 365
    audit_payload_retention_days: int = 90
    # If enabled, control-plane audit events marked critical are written synchronously.
    # If disabled, critical events are queued unless explicitly allowlisted below.
    audit_control_sync_enabled: bool = True
    # Optional list of control-plane audit actions that must remain synchronous
    # even when audit_control_sync_enabled is false.
    audit_control_sync_actions: list[str] = Field(default_factory=list)

    @field_validator("master_key")
    @classmethod
    def validate_master_key(cls, value: str | None) -> str | None:
        return _validate_master_key_strength(value)


class AppConfig(BaseModel):
    model_config = {"populate_by_name": True}

    model_list: list[ModelDeployment] = Field(default_factory=list)
    router_settings: RouterSettings = Field(default_factory=RouterSettings)
    deltallm_settings: DeltaLLMSettings = Field(default_factory=DeltaLLMSettings, validation_alias=AliasChoices("deltallm_settings", "litellm_settings"))
    general_settings: GeneralSettings = Field(default_factory=GeneralSettings)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DELTALLM_", extra="ignore")

    app_name: str = "DeltaLLM Core API"
    app_env: str = "dev"
    log_level: str = "INFO"
    config_path: str = "config.yaml"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    master_key: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_degraded_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    salt_key: str | None = None

    @field_validator("master_key")
    @classmethod
    def validate_master_key(cls, value: str | None) -> str | None:
        return _validate_master_key_strength(value)


def resolve_salt_key(config: AppConfig, settings: Settings) -> str:
    candidate = config.general_settings.salt_key or settings.salt_key
    if candidate is None or not candidate.strip():
        raise ValueError("Salt key is required. Set `general_settings.salt_key` or `DELTALLM_SALT_KEY`.")
    normalized = candidate.strip()
    if normalized == "change-me":
        raise ValueError("Insecure salt key is not allowed. Configure a unique non-default salt key.")
    return normalized


def _resolve_env_token(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("os.environ/"):
        env_name = value.split("/", 1)[1]
        return os.getenv(env_name)
    if isinstance(value, dict):
        return {k: _resolve_env_token(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_token(v) for v in value]
    return value


def resolve_app_config_with_secrets(raw_config: dict[str, Any], secret_resolver: Any | None = None) -> AppConfig:
    from src.config_runtime.secrets import SecretResolver

    resolver = secret_resolver or SecretResolver()
    resolved_input = _resolve_env_token(raw_config)
    try:
        resolved = resolver.resolve_tree(resolved_input)
    except Exception as exc:
        raise ValueError(
            "Failed to resolve configuration secrets. Check secret references and provider availability."
        ) from exc

    try:
        return AppConfig.model_validate(resolved)
    except ValidationError as exc:
        raise ValueError("Resolved configuration is invalid. Check config values and resolved secrets.") from exc


def load_yaml_config(path: str | Path) -> AppConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return AppConfig()

    data = yaml.safe_load(cfg_path.read_text()) or {}
    return resolve_app_config_with_secrets(data)


@lru_cache
def get_settings() -> Settings:
    return Settings()
