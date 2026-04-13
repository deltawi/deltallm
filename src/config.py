from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CREATE_SESSION_CLEANUP_SCAN_LIMIT,
    DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_ORPHAN_GRACE_SECONDS,
    DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
)
from src.upstream_auth import (
    supports_custom_openai_compatible_auth,
    validate_auth_header_format,
    validate_auth_header_name,
)


ModelMode = Literal[
    "chat",
    "embedding",
    "image_generation",
    "audio_speech",
    "audio_transcription",
    "rerank",
]

RoutingStrategyName = Literal[
    "simple-shuffle",
    "least-busy",
    "latency-based-routing",
    "cost-based-routing",
    "usage-based-routing",
    "tag-based-routing",
    "priority-based-routing",
    "weighted",
    "rate-limit-aware",
]


class DeltaLLMParams(BaseModel):
    model: str
    provider: str | None = None
    region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    auth_header_name: str | None = None
    auth_header_format: str | None = None
    timeout: int | None = 300
    rpm: int | None = None
    tpm: int | None = None
    weight: int = 1
    stream_timeout: int | None = None
    max_tokens: int | None = None

    @field_validator("auth_header_name")
    @classmethod
    def validate_custom_auth_header_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_auth_header_name(value)

    @field_validator("auth_header_format")
    @classmethod
    def validate_custom_auth_header_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_auth_header_format(value)

    @model_validator(mode="after")
    def validate_custom_auth_headers_supported_provider(self) -> "DeltaLLMParams":
        if self.auth_header_name is None and self.auth_header_format is None:
            return self

        provider = str(self.provider or "").strip().lower()
        if not provider:
            model_value = str(self.model or "").strip()
            provider = model_value.split("/", 1)[0].strip().lower() if "/" in model_value else ""

        if not supports_custom_openai_compatible_auth(provider):
            raise ValueError(f"Custom auth headers are not supported for provider '{provider or 'unknown'}'")
        return self


class ModelInfo(BaseModel):
    mode: ModelMode = "chat"
    weight: int = 1
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None
    input_cost_per_token_cache_hit: float | None = None
    output_cost_per_token_cache_hit: float | None = None
    batch_input_cost_per_token: float | None = None
    batch_output_cost_per_token: float | None = None
    batch_price_multiplier: float | None = None
    input_cost_per_character: float | None = None
    output_cost_per_character: float | None = None
    input_cost_per_second: float | None = None
    output_cost_per_second: float | None = None
    input_cost_per_image: float | None = None
    output_cost_per_image: float | None = None
    input_cost_per_audio_token: float | None = None
    output_cost_per_audio_token: float | None = None
    output_vector_size: int | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    image_pm_limit: int | None = None
    audio_seconds_pm_limit: int | None = None
    char_pm_limit: int | None = None
    rerank_units_pm_limit: int | None = None
    max_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    upstream_max_batch_inputs: int | None = Field(default=None, ge=1)
    default_params: dict[str, Any] | None = None


class ModelDeployment(BaseModel):
    model_config = {"populate_by_name": True}

    model_name: str
    named_credential_id: str | None = None
    deltallm_params: DeltaLLMParams = Field(validation_alias=AliasChoices("deltallm_params", "litellm_params"))
    model_info: ModelInfo | None = None
    deployment_id: str | None = None


class RouteGroupMember(BaseModel):
    deployment_id: str
    enabled: bool = True
    weight: int | None = None
    priority: int | None = None


class RouteGroupConfig(BaseModel):
    key: str
    enabled: bool = True
    strategy: RoutingStrategyName | None = None
    members: list[RouteGroupMember] = Field(default_factory=list)


class RouterSettings(BaseModel):
    routing_strategy: RoutingStrategyName = "simple-shuffle"
    num_retries: int = 0
    retry_after: float = 0
    timeout: float = 600
    cooldown_time: int = 60
    allowed_fails: int = 0
    enable_pre_call_checks: bool = False
    model_group_alias: dict[str, str] = Field(default_factory=dict)
    route_groups: list[RouteGroupConfig] = Field(default_factory=list)


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
        if mode is not None and mode not in ("pre_call", "post_call"):
            raise ValueError("mode must be pre_call or post_call")
        action = value.get("default_action")
        if action is not None and action not in ("block", "log"):
            raise ValueError("default_action must be block or log")
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
    db_pool_size: int = Field(default=20, gt=0)
    db_pool_timeout: int = Field(default=30, ge=0)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_url: str | None = None
    redis_degraded_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    cache_enabled: bool = False
    cache_backend: Literal["memory", "redis", "s3"] = "memory"
    cache_ttl: int = 3600
    cache_max_size: int = 10000
    stream_cache_max_bytes: int = Field(default=262_144, gt=0)
    stream_cache_max_fragments: int = Field(default=2_048, gt=0)
    failover_event_history_size: int = Field(default=1_000, gt=0)
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
    sso_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    enable_jwt_auth: bool = False
    jwt_public_key_url: str | None = None
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    jwt_claims_mapping: dict[str, str] = Field(default_factory=dict)
    custom_auth: str | None = None
    platform_bootstrap_admin_email: str | None = None
    platform_bootstrap_admin_password: str | None = None
    auth_session_ttl_hours: int = 12
    invitation_token_ttl_hours: int = Field(default=72, ge=1, le=720)
    password_reset_token_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    api_key_auth_cache_ttl_seconds: int = 300
    governance_notifications_enabled: bool = False
    budget_notifications_enabled: bool = False
    key_lifecycle_notifications_enabled: bool = False
    budget_alert_ttl_seconds: int = Field(default=3600, ge=60)
    email_enabled: bool = False
    email_provider: Literal["smtp", "resend", "sendgrid"] = "smtp"
    email_from_address: str | None = None
    email_from_name: str | None = None
    email_reply_to: str | None = None
    email_base_url: str | None = None
    email_max_attempts: int = Field(default=5, ge=1, le=20)
    email_retry_initial_seconds: int = Field(default=60, ge=1)
    email_retry_max_seconds: int = Field(default=3600, ge=1)
    email_worker_enabled: bool = True
    email_worker_poll_interval_seconds: float = Field(default=5.0, gt=0)
    email_worker_max_concurrency: int = Field(default=3, ge=1, le=20)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    smtp_use_starttls: bool = True
    resend_api_key: str | None = None
    resend_webhook_signing_secret: str | None = None
    resend_webhook_tolerance_seconds: int = Field(default=300, ge=30, le=3600)
    sendgrid_api_key: str | None = None
    model_deployment_source: Literal["hybrid", "db_only", "config_only"] = "hybrid"
    model_deployment_bootstrap_from_config: bool = True
    embeddings_batch_enabled: bool = False
    embeddings_batch_worker_enabled: bool = True
    embeddings_batch_storage_backend: Literal["local", "s3"] = "local"
    embeddings_batch_storage_dir: str = ".deltallm/batch-artifacts"
    embeddings_batch_s3_bucket: str | None = None
    embeddings_batch_s3_region: str = "us-east-1"
    embeddings_batch_s3_prefix: str = "deltallm/batch-artifacts"
    embeddings_batch_s3_endpoint_url: str | None = None
    embeddings_batch_s3_access_key_id: str | None = None
    embeddings_batch_s3_secret_access_key: str | None = None
    embeddings_batch_s3_spool_max_bytes: int = Field(default=8_388_608, gt=0)
    embeddings_batch_poll_interval_seconds: float = 1.0
    embeddings_batch_heartbeat_interval_seconds: float = Field(default=15.0, gt=0)
    embeddings_batch_job_lease_seconds: int = Field(default=120, ge=5)
    embeddings_batch_item_lease_seconds: int = Field(default=360, ge=30)
    embeddings_batch_finalization_retry_delay_seconds: int = Field(default=60, ge=1)
    embeddings_batch_worker_concurrency: int = Field(default=4, ge=1, le=100)
    embeddings_batch_item_buffer_multiplier: int = Field(default=2, ge=1, le=10)
    embeddings_batch_storage_chunk_size: int = Field(default=65_536, ge=1_024)
    embeddings_batch_finalization_page_size: int = Field(default=500, ge=10, le=10_000)
    embeddings_batch_create_buffer_size: int = Field(default=200, ge=1, le=10_000)
    embeddings_batch_create_sessions_enabled: bool = False
    embeddings_batch_create_session_cleanup_enabled: bool = False
    embeddings_batch_create_session_cleanup_interval_seconds: float = Field(
        default=DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
        gt=0,
    )
    embeddings_batch_create_session_cleanup_scan_limit: int = Field(
        default=DEFAULT_CREATE_SESSION_CLEANUP_SCAN_LIMIT,
        ge=1,
        le=1000,
    )
    embeddings_batch_create_stage_orphan_grace_seconds: int = Field(
        default=DEFAULT_CREATE_SESSION_ORPHAN_GRACE_SECONDS,
        ge=60,
    )
    embeddings_batch_create_session_completed_retention_seconds: int = Field(
        default=DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
        ge=60,
    )
    embeddings_batch_create_session_retryable_retention_seconds: int = Field(
        default=DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
        ge=60,
    )
    embeddings_batch_create_session_failed_retention_seconds: int = Field(
        default=DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
        ge=60,
    )
    embeddings_batch_create_soft_precheck_enabled: bool = False
    embeddings_batch_create_idempotency_enabled: bool = False
    embeddings_batch_create_promotion_insert_chunk_size: int = Field(default=500, ge=1, le=10_000)
    embeddings_batch_max_file_bytes: int = Field(default=52_428_800, ge=1_024)
    embeddings_batch_max_items_per_batch: int = Field(default=10_000, ge=1)
    embeddings_batch_max_line_bytes: int = Field(default=1_048_576, ge=1_024)
    embeddings_batch_max_pending_batches_per_scope: int = Field(default=20, ge=0)
    embeddings_batch_item_claim_limit: int = 20
    embeddings_batch_max_attempts: int = 3
    batch_completed_artifact_retention_days: int = 7
    batch_failed_artifact_retention_days: int = 14
    batch_metadata_retention_days: int = 30
    embeddings_batch_gc_enabled: bool = True
    embeddings_batch_gc_interval_seconds: float = 86400.0
    embeddings_batch_gc_scan_limit: int = 200
    callable_target_scope_policy_mode: Literal["legacy", "shadow", "enforce"] = "enforce"
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
    db_pool_size: int | None = Field(default=None, gt=0)
    db_pool_timeout: int | None = Field(default=None, ge=0)
    redis_url: str | None = None
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_degraded_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    salt_key: str | None = None
    callable_target_scope_policy_mode: Literal["legacy", "shadow", "enforce"] = "enforce"

    @field_validator("master_key")
    @classmethod
    def validate_master_key(cls, value: str | None) -> str | None:
        return _validate_master_key_strength(value)


@dataclass(frozen=True)
class DatabaseConnectionSettings:
    url: str
    pool_size: int
    pool_timeout: int


def resolve_salt_key(config: AppConfig, settings: Settings) -> str:
    candidate = config.general_settings.salt_key or settings.salt_key
    if candidate is None or not candidate.strip():
        raise ValueError("Salt key is required. Set `general_settings.salt_key` or `DELTALLM_SALT_KEY`.")
    normalized = candidate.strip()
    if normalized == "change-me":
        raise ValueError("Insecure salt key is not allowed. Configure a unique non-default salt key.")
    return normalized


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _apply_database_pool_settings(database_url: str, *, pool_size: int, pool_timeout: int) -> str:
    parsed = urlsplit(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["connection_limit"] = str(pool_size)
    query["pool_timeout"] = str(pool_timeout)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def resolve_database_settings(config: AppConfig, settings: Settings) -> DatabaseConnectionSettings | None:
    candidate_url = (
        _normalize_optional_str(settings.database_url)
        or _normalize_optional_str(config.general_settings.database_url)
        or _normalize_optional_str(os.getenv("DATABASE_URL"))
    )
    if candidate_url is None:
        return None

    pool_size = settings.db_pool_size or config.general_settings.db_pool_size
    pool_timeout = settings.db_pool_timeout
    if pool_timeout is None:
        pool_timeout = config.general_settings.db_pool_timeout

    return DatabaseConnectionSettings(
        url=_apply_database_pool_settings(
            candidate_url,
            pool_size=pool_size,
            pool_timeout=pool_timeout,
        ),
        pool_size=pool_size,
        pool_timeout=pool_timeout,
    )


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
