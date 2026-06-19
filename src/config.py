from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.auth.roles import TeamRole, validate_team_role
from src.governance.access_groups import normalize_access_group_list
from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CREATE_SESSION_CLEANUP_SCAN_LIMIT,
    DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_ORPHAN_GRACE_SECONDS,
    DEFAULT_CREATE_SESSION_PROMOTION_TX_MAX_WAIT_SECONDS,
    DEFAULT_CREATE_SESSION_PROMOTION_TX_TIMEOUT_SECONDS,
    DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
)
from src.batch.scheduling.modes import (
    SchedulerMode,
    SchedulerShadowMode,
    resolve_scheduler_modes_from_settings,
)
from src.batch.webhooks.crypto import decode_batch_webhook_encryption_key
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


ChatBatchingMode = Literal["disabled", "concurrent", "sync_microbatch"]
SelfRegistrationMode = Literal["sso_allowed_domain", "request_access"]


class BatchModelCapacityInfo(BaseModel):
    max_in_flight: int | None = Field(default=None, ge=1, strict=True)
    max_claim_work_units: int | None = Field(default=None, ge=1, strict=True)
    capacity_fraction: float | None = Field(default=None, gt=0.0, le=1.0)


class ChatBatchingConfig(BaseModel):
    mode: ChatBatchingMode = "concurrent"
    max_in_flight: int | None = Field(default=None, ge=1)
    upstream_max_batch_size: int | None = Field(default=None, ge=1)
    max_total_input_tokens: int | None = Field(default=None, ge=1)
    require_homogeneous_params: bool = True

    @model_validator(mode="after")
    def validate_sync_microbatch_limits(self) -> "ChatBatchingConfig":
        if self.mode != "sync_microbatch":
            return self
        if (self.upstream_max_batch_size or 0) < 2:
            raise ValueError("chat_batching.upstream_max_batch_size must be at least 2 when mode is sync_microbatch")
        if self.require_homogeneous_params is not True:
            raise ValueError("chat_batching.require_homogeneous_params=false is not supported for sync_microbatch")
        return self


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
    timeout: int | None = None
    rpm: int | None = None
    tpm: int | None = None
    weight: int = 1
    stream_timeout: int | None = None
    max_tokens: int | None = None
    chat_batching: ChatBatchingConfig | None = None

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
    access_groups: list[str] = Field(default_factory=list)
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
    batch_capacity: BatchModelCapacityInfo | None = None
    default_params: dict[str, Any] | None = None

    @field_validator("access_groups", mode="before")
    @classmethod
    def validate_access_groups(cls, value: object) -> list[str]:
        return normalize_access_group_list(value, strict=True)


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
    access_groups: list[str] = Field(default_factory=list)
    members: list[RouteGroupMember] = Field(default_factory=list)

    @field_validator("access_groups", mode="before")
    @classmethod
    def validate_access_groups(cls, value: object) -> list[str]:
        return normalize_access_group_list(value, strict=True)


class RouterSettings(BaseModel):
    routing_strategy: RoutingStrategyName = "simple-shuffle"
    num_retries: int = 0
    retry_after: float = 0
    timeout: float = 600
    cooldown_time: int = 60
    allowed_fails: int = 2
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


class SelfRegistrationLimitDefaults(BaseModel):
    max_budget: float | None = Field(default=None, gt=0)
    soft_budget: float | None = Field(default=None, gt=0)
    rpm_limit: int | None = Field(default=None, gt=0)
    tpm_limit: int | None = Field(default=None, gt=0)
    rph_limit: int | None = Field(default=None, gt=0)
    rpd_limit: int | None = Field(default=None, gt=0)
    tpd_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_soft_budget_ceiling(self) -> "SelfRegistrationLimitDefaults":
        if (
            self.max_budget is not None
            and self.soft_budget is not None
            and self.soft_budget > self.max_budget
        ):
            raise ValueError("soft_budget must be less than or equal to max_budget")
        return self


class SelfRegistrationDefaultOrg(SelfRegistrationLimitDefaults):
    id: str | None = None
    name: str | None = None

    @field_validator("id", "name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class SelfRegistrationDefaultTeam(SelfRegistrationLimitDefaults):
    id: str | None = None
    alias: str | None = None
    role: str = TeamRole.DEVELOPER
    self_service_keys_enabled: bool = True
    self_service_max_keys_per_user: int | None = Field(default=None, gt=0)
    self_service_budget_ceiling: float | None = Field(default=None, gt=0)
    self_service_require_expiry: bool = True
    self_service_max_expiry_days: int | None = Field(default=None, gt=0)

    @field_validator("id", "alias")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        return validate_team_role(value)


class SelfRegistrationDefaultUser(SelfRegistrationLimitDefaults):
    user_role: str = "internal_user"

    @field_validator("user_role")
    @classmethod
    def normalize_user_role(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("user_role cannot be blank")
        return normalized


class SelfRegistrationSettings(BaseModel):
    enabled: bool = False
    mode: SelfRegistrationMode = "sso_allowed_domain"
    allowed_domains: list[str] = Field(default_factory=list)
    require_email_verification: bool = True
    require_admin_approval: bool = False
    default_org: SelfRegistrationDefaultOrg = Field(default_factory=SelfRegistrationDefaultOrg)
    default_team: SelfRegistrationDefaultTeam = Field(default_factory=SelfRegistrationDefaultTeam)
    default_user: SelfRegistrationDefaultUser = Field(default_factory=SelfRegistrationDefaultUser)

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def normalize_allowed_domains_input(cls, value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        raise ValueError("allowed_domains must be a list of domains")

    @field_validator("allowed_domains")
    @classmethod
    def normalize_allowed_domains(cls, value: list[str]) -> list[str]:
        normalized_domains: list[str] = []
        seen: set[str] = set()
        for raw_domain in value:
            domain = str(raw_domain or "").strip().lower()
            if not domain:
                continue
            if "@" in domain or "/" in domain or "\\" in domain or domain.startswith(".") or domain.endswith("."):
                raise ValueError("allowed_domains entries must be bare email domains")
            if domain not in seen:
                normalized_domains.append(domain)
                seen.add(domain)
        return normalized_domains

    @model_validator(mode="after")
    def validate_enabled_defaults(self) -> "SelfRegistrationSettings":
        if not self.enabled:
            return self
        if not self.default_org.id:
            raise ValueError("self_registration.default_org.id is required when self-registration is enabled")
        if not self.default_team.id:
            raise ValueError("self_registration.default_team.id is required when self-registration is enabled")
        if self.mode == "sso_allowed_domain" and not self.allowed_domains:
            raise ValueError(
                "self_registration.allowed_domains is required when mode is sso_allowed_domain"
            )
        return self


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
    model_config = ConfigDict(hide_input_in_errors=True)

    instance_name: str = "DeltaLLM"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    master_key: str | None = None
    deltallm_key_header_name: str = "Authorization"
    salt_key: str | None = None
    database_url: str | None = None
    db_pool_size: int = Field(default=20, gt=0)
    db_pool_timeout: int = Field(default=30, ge=0)
    upstream_http_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    upstream_http_read_timeout_seconds: float = Field(default=300.0, gt=0)
    upstream_http_write_timeout_seconds: float = Field(default=30.0, gt=0)
    upstream_http_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    upstream_http_max_connections: int = Field(default=500, gt=0)
    upstream_http_max_keepalive_connections: int = Field(default=100, ge=0)
    upstream_http_keepalive_expiry_seconds: float = Field(default=60.0, ge=0)
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
    self_registration: SelfRegistrationSettings = Field(default_factory=SelfRegistrationSettings)
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
    cache_invalidation_worker_enabled: bool = True
    cache_invalidation_worker_poll_interval_seconds: float = Field(default=5.0, gt=0)
    cache_invalidation_worker_batch_size: int = Field(default=25, ge=1, le=500)
    cache_invalidation_worker_max_concurrency: int = Field(default=4, ge=1, le=50)
    cache_invalidation_worker_lease_seconds: int = Field(default=60, ge=5)
    cache_invalidation_worker_record_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    cache_invalidation_max_attempts: int = Field(default=10, ge=1, le=100)
    cache_invalidation_retry_initial_seconds: int = Field(default=5, ge=1)
    cache_invalidation_retry_max_seconds: int = Field(default=300, ge=1)
    cache_invalidation_immediate_timeout_seconds: float = Field(default=0.5, gt=0, le=30)
    governance_notifications_enabled: bool = False
    budget_notifications_enabled: bool = False
    key_lifecycle_notifications_enabled: bool = False
    budget_alert_ttl_seconds: int = Field(default=3600, ge=60)
    slack_alerting_enabled: bool = False
    slack_webhook_url: SecretStr | None = None
    slack_alert_kinds: list[str] = Field(default_factory=list)
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
    embeddings_batch_completion_outbox_worker_enabled: bool = True
    batch_webhook_enabled: bool = False
    batch_webhook_worker_enabled: bool = True
    batch_webhook_observability_enabled: bool = True
    batch_webhook_encryption_key: SecretStr | None = None
    batch_webhook_poll_interval_seconds: float = Field(default=1.0, gt=0.0)
    batch_webhook_observability_refresh_interval_seconds: float = Field(default=15.0, gt=0.0)
    batch_webhook_max_concurrency: int = Field(default=4, ge=1, le=100)
    batch_webhook_lease_seconds: int = Field(default=30, ge=5)
    batch_webhook_timeout_seconds: float = Field(default=10.0, gt=0.0)
    batch_webhook_max_attempts: int = Field(default=8, ge=1, le=50)
    batch_webhook_retry_initial_seconds: int = Field(default=5, ge=1)
    batch_webhook_retry_max_seconds: int = Field(default=3_600, ge=1)
    batch_webhook_allowed_ports: list[int] = Field(default_factory=lambda: [443])
    batch_webhook_allowed_private_cidrs: list[str] = Field(default_factory=list)
    batch_webhook_allow_http: bool = False
    batch_webhook_delivery_retention_days: int = Field(default=30, ge=1)
    batch_webhook_cleanup_max_rows_per_run: int = Field(
        default=10_000,
        ge=1,
        le=1_000_000,
    )
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
    embeddings_batch_create_session_cleanup_enabled: bool = True
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
    embeddings_batch_create_soft_precheck_enabled: bool = True
    embeddings_batch_create_idempotency_enabled: bool = False
    embeddings_batch_create_promotion_tx_max_wait_seconds: float = Field(
        default=DEFAULT_CREATE_SESSION_PROMOTION_TX_MAX_WAIT_SECONDS,
        gt=0.0,
    )
    embeddings_batch_create_promotion_tx_timeout_seconds: float = Field(
        default=DEFAULT_CREATE_SESSION_PROMOTION_TX_TIMEOUT_SECONDS,
        gt=0.0,
    )
    embeddings_batch_create_promotion_insert_chunk_size: int = Field(default=500, ge=1, le=10_000)
    embeddings_batch_max_file_bytes: int = Field(default=52_428_800, ge=1_024)
    embeddings_batch_max_items_per_batch: int = Field(default=10_000, ge=1)
    embeddings_batch_max_line_bytes: int = Field(default=1_048_576, ge=1_024)
    embeddings_batch_max_pending_batches_per_scope: int = Field(default=20, ge=0)
    embeddings_batch_item_claim_limit: int = 20
    embeddings_batch_max_attempts: int = 3
    embeddings_batch_retry_initial_seconds: int = Field(default=5, ge=1)
    embeddings_batch_retry_max_seconds: int = Field(default=300, ge=1)
    embeddings_batch_retry_multiplier: float = Field(default=2.0, ge=1.0)
    embeddings_batch_retry_jitter: bool = True
    embeddings_batch_microbatch_retry_enabled: bool = True
    embeddings_batch_microbatch_max_group_retries: int = Field(default=2, ge=0)
    embeddings_batch_microbatch_min_reduced_size: int = Field(default=1, ge=1)
    embeddings_batch_microbatch_reduce_factor: float = Field(default=0.5, gt=0.0, le=1.0)
    embeddings_batch_model_group_backpressure_enabled: bool = True
    embeddings_batch_model_group_backpressure_min_seconds: int = Field(default=5, ge=1)
    embeddings_batch_model_group_backpressure_max_seconds: int = Field(default=300, ge=1)
    embeddings_batch_scheduler_mode: SchedulerMode = "fifo_v1"
    embeddings_batch_scheduler_shadow_mode: SchedulerShadowMode = "none"
    embeddings_batch_scheduler_shadow_decision_timeout_seconds: float = Field(default=0.5, gt=0.0)
    embeddings_batch_scheduler_shadow_max_pending_decisions: int = Field(default=16, ge=0)
    embeddings_batch_scheduler_enabled: bool = False
    embeddings_batch_scheduler_shadow_enabled: bool = False
    embeddings_batch_scheduler_strict_model_homogeneity_enabled: bool = False
    embeddings_batch_scheduler_default_service_tier: str = "standard"
    embeddings_batch_scheduler_estimator_version: Literal["v1"] = "v1"
    embeddings_batch_scheduler_backfill_enabled: bool = False
    embeddings_batch_scheduler_backfill_interval_seconds: float = Field(default=60.0, gt=0.0)
    embeddings_batch_scheduler_backfill_scan_limit: int = Field(default=500, ge=1, le=5_000)
    embeddings_batch_stale_lease_sweeper_enabled: bool = True
    embeddings_batch_stale_lease_sweeper_interval_seconds: float = Field(default=60.0, gt=0.0)
    embeddings_batch_stale_lease_sweeper_failure_interval_seconds: float = Field(default=30.0, gt=0.0)
    embeddings_batch_stale_lease_sweeper_page_size: int = Field(default=100, ge=1, le=1_000)
    embeddings_batch_stale_lease_sweeper_max_rows_per_run: int = Field(default=500, ge=1, le=5_000)
    embeddings_batch_scheduler_claim_mode: Literal["job_fifo", "work_slice"] = "job_fifo"
    embeddings_batch_advisory_lock_mode: Literal["dual", "canonical"] = "dual"
    embeddings_batch_work_claim_max_items: int = Field(default=0, ge=0, le=200)
    embeddings_batch_work_claim_max_work_units: int = Field(default=0, ge=0)
    embeddings_batch_work_claim_min_items_for_microbatch: int = Field(default=4, ge=1, le=200)
    embeddings_batch_claim_diagnostics_enabled: bool = True
    embeddings_batch_claim_diagnostic_interval_seconds: float = Field(default=60.0, ge=1.0)
    embeddings_batch_claim_diagnostic_max_keys: int = Field(default=1024, ge=1, le=100_000)
    embeddings_batch_model_capacity_enabled: bool = False
    embeddings_batch_default_model_max_in_flight: int = Field(default=16, ge=1)
    embeddings_batch_default_model_max_claim_work_units: int = Field(default=64, ge=1)
    embeddings_batch_model_capacity_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    embeddings_batch_model_capacity_refresh_seconds: float = Field(default=5.0, gt=0.0)
    embeddings_batch_model_capacity_fail_open: bool = False
    embeddings_batch_tenant_fair_share_enabled: bool = False
    embeddings_batch_scheduler_base_quantum_work_units: int = Field(default=16, ge=1)
    embeddings_batch_scheduler_max_deficit_multiplier: int = Field(default=8, ge=1)
    embeddings_batch_tenant_max_in_flight_work_units: int = Field(default=0, ge=0)
    embeddings_batch_tenant_max_queued_work_units: int = Field(default=0, ge=0)
    embeddings_batch_scheduler_max_active_flows_per_decision: int = Field(
        default=100,
        ge=1,
        le=1_000,
    )
    embeddings_batch_scheduler_max_candidate_jobs_per_flow: int = Field(
        default=50,
        ge=1,
        le=1_000,
    )
    embeddings_batch_tenant_scope_preference: str = "organization,team,api_key,user"
    embeddings_batch_tenant_fair_share_disabled_model_groups: list[str] = Field(default_factory=list)
    embeddings_batch_size_aware_scheduling_enabled: bool = False
    embeddings_batch_aging_seconds_per_work_unit: int = Field(default=30, ge=1)
    embeddings_batch_max_age_credit_work_units: int = Field(default=1_000, ge=0)
    embeddings_batch_min_large_job_claim_interval_seconds: int = Field(default=30, ge=0)
    embeddings_batch_small_job_fast_lane_enabled: bool = False
    embeddings_batch_small_job_max_work_units: int = Field(default=100, ge=1)
    embeddings_batch_finalization_first: bool = True
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

    @field_validator("batch_webhook_encryption_key")
    @classmethod
    def validate_batch_webhook_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        decode_batch_webhook_encryption_key(value)
        return value

    @field_validator("batch_webhook_allowed_ports")
    @classmethod
    def validate_batch_webhook_allowed_ports(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for raw_port in value:
            port = int(raw_port)
            if port < 1 or port > 65_535:
                raise ValueError("batch_webhook_allowed_ports entries must be between 1 and 65535")
            if port not in seen:
                normalized.append(port)
                seen.add(port)
        return normalized

    @field_validator("batch_webhook_allowed_private_cidrs")
    @classmethod
    def validate_batch_webhook_allowed_private_cidrs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_cidr in value:
            try:
                cidr = str(ip_network(str(raw_cidr or "").strip(), strict=False))
            except ValueError as exc:
                raise ValueError(
                    "batch_webhook_allowed_private_cidrs entries must be valid IPv4 or IPv6 CIDRs"
                ) from exc
            if cidr not in seen:
                normalized.append(cidr)
                seen.add(cidr)
        return normalized

    @model_validator(mode="after")
    def validate_upstream_http_pool(self) -> "GeneralSettings":
        if self.upstream_http_max_keepalive_connections > self.upstream_http_max_connections:
            raise ValueError(
                "upstream_http_max_keepalive_connections must be less than or equal to "
                "upstream_http_max_connections"
            )
        if self.batch_webhook_enabled and self.batch_webhook_encryption_key is None:
            raise ValueError(
                "batch_webhook_enabled requires batch_webhook_encryption_key to be set"
            )
        if self.batch_webhook_enabled and not self.batch_webhook_allowed_ports:
            raise ValueError("batch_webhook_enabled requires at least one allowed webhook port")
        if self.batch_webhook_retry_max_seconds < self.batch_webhook_retry_initial_seconds:
            raise ValueError(
                "batch_webhook_retry_max_seconds must be greater than or equal to "
                "batch_webhook_retry_initial_seconds"
            )
        if self.batch_webhook_lease_seconds <= self.batch_webhook_timeout_seconds:
            raise ValueError(
                "batch_webhook_lease_seconds must be greater than batch_webhook_timeout_seconds"
            )
        scheduler_modes = resolve_scheduler_modes_from_settings(self)
        mode_control_explicit = (
            "embeddings_batch_scheduler_mode" in self.model_fields_set
            or "embeddings_batch_scheduler_shadow_mode" in self.model_fields_set
        )
        scheduler_configured = (
            scheduler_modes.active_uses_work_slice or scheduler_modes.shadow_mode != "none"
        )
        if scheduler_configured and not mode_control_explicit:
            if self.embeddings_batch_scheduler_claim_mode != "work_slice":
                raise ValueError(
                    "active batch scheduler v2 requires "
                    "embeddings_batch_scheduler_claim_mode='work_slice'"
                )
        if (
            not mode_control_explicit
            and self.embeddings_batch_tenant_fair_share_enabled
            and not self.embeddings_batch_model_capacity_enabled
        ):
            raise ValueError(
                "tenant fair-share scheduling requires embeddings_batch_model_capacity_enabled=true"
            )
        if (
            not mode_control_explicit
            and self.embeddings_batch_size_aware_scheduling_enabled
            and not self.embeddings_batch_model_capacity_enabled
        ):
            raise ValueError(
                "size-aware batch scheduling requires embeddings_batch_model_capacity_enabled=true"
            )
        if (
            not mode_control_explicit
            and self.embeddings_batch_size_aware_scheduling_enabled
            and not (
                self.embeddings_batch_tenant_fair_share_enabled
                or self.embeddings_batch_scheduler_shadow_enabled
            )
        ):
            raise ValueError(
                "size-aware batch scheduling requires embeddings_batch_tenant_fair_share_enabled=true "
                "or embeddings_batch_scheduler_shadow_enabled=true"
            )
        if (
            not mode_control_explicit
            and self.embeddings_batch_scheduler_shadow_enabled
            and not self.embeddings_batch_model_capacity_enabled
        ):
            raise ValueError(
                "batch scheduler shadow mode requires embeddings_batch_model_capacity_enabled=true"
            )
        if self.slack_alerting_enabled and self.slack_webhook_url is None:
            raise ValueError("slack_alerting_enabled requires slack_webhook_url to be set")
        if self.slack_alerting_enabled:
            from src.notifications.types import NOTIFICATION_ALERT_TYPES

            unknown = [kind for kind in self.slack_alert_kinds if kind not in NOTIFICATION_ALERT_TYPES]
            if unknown:
                allowed = ", ".join(sorted(NOTIFICATION_ALERT_TYPES))
                raise ValueError(
                    f"slack_alert_kinds contains unknown alert types {unknown}; allowed values are: {allowed}"
                )
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, hide_input_in_errors=True)

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


_SENSITIVE_CONFIG_FIELD_MARKERS = ("key", "password", "secret", "token", "url")


def _safe_config_validation_message(exc: ValidationError) -> str:
    errors = exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    details: list[str] = []
    for error in errors[:10]:
        location_parts = [str(part) for part in error.get("loc") or ()]
        error_type = str(error.get("type") or "")
        if error_type == "extra_forbidden":
            parent = ".".join(location_parts[:-1])
            location = f"{parent}.<unsupported-field>" if parent else "<unsupported-field>"
            message = "unsupported configuration field"
        else:
            location = ".".join(location_parts) or "<configuration>"
            is_sensitive = any(
                marker in part.lower()
                for part in location_parts
                for marker in _SENSITIVE_CONFIG_FIELD_MARKERS
            )
            message = (
                "invalid sensitive value"
                if is_sensitive
                else str(error.get("msg") or "invalid value")
            )
        details.append(f"{location}: {message}")

    if len(errors) > len(details):
        details.append(f"{len(errors) - len(details)} additional validation error(s)")
    suffix = "; ".join(details) or "configuration validation failed"
    return f"Resolved configuration is invalid. {suffix}"


def resolve_app_config_with_secrets(raw_config: dict[str, Any], secret_resolver: Any | None = None) -> AppConfig:
    from src.config_runtime.secrets import SecretResolver

    resolver = secret_resolver or SecretResolver()
    resolved_input = _resolve_env_token(raw_config)
    resolution_failed = False
    try:
        resolved = resolver.resolve_tree(resolved_input)
    except Exception:
        resolution_failed = True

    if resolution_failed:
        raise ValueError(
            "Failed to resolve configuration secrets. Check secret references and provider availability."
        )

    validation_message: str | None = None
    try:
        return AppConfig.model_validate(resolved)
    except ValidationError as exc:
        validation_message = _safe_config_validation_message(exc)

    if validation_message is None:  # pragma: no cover - model_validate either returns or raises
        raise RuntimeError("resolved configuration validation failed")
    raise ValueError(validation_message)


def load_yaml_config(path: str | Path) -> AppConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return AppConfig()

    data = yaml.safe_load(cfg_path.read_text()) or {}
    return resolve_app_config_with_secrets(data)


@lru_cache
def get_settings() -> Settings:
    return Settings()
