from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiteLLMParams(BaseModel):
    model: str
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    timeout: int | None = 300
    rpm: int | None = None
    tpm: int | None = None
    weight: int = 1


class ModelInfo(BaseModel):
    weight: int = 1
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    max_tokens: int | None = None


class ModelDeployment(BaseModel):
    model_name: str
    litellm_params: LiteLLMParams
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
    guardrail_name: str
    litellm_params: dict[str, Any]

    @field_validator("litellm_params")
    @classmethod
    def validate_litellm_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "guardrail" not in value:
            raise ValueError("litellm_params must include 'guardrail' class path")
        mode = value.get("mode")
        if mode is not None and mode not in ("pre_call", "post_call", "during_call"):
            raise ValueError("mode must be pre_call, post_call, or during_call")
        return value


class LiteLLMSettings(BaseModel):
    fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    context_window_fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    content_policy_fallbacks: list[dict[str, list[str]]] = Field(default_factory=list)
    guardrails: list[GuardrailConfig] = Field(default_factory=list)
    success_callback: list[str] = Field(default_factory=list)
    failure_callback: list[str] = Field(default_factory=list)
    callbacks: list[str] = Field(default_factory=list)
    callback_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    turn_off_message_logging: bool = False


class GeneralSettings(BaseModel):
    instance_name: str = "DeltaLLM"
    master_key: str | None = None
    litellm_key_header_name: str = "Authorization"
    salt_key: str = "change-me"
    database_url: str | None = None
    db_pool_size: int = 20
    db_pool_timeout: int = 30
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_url: str | None = None
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


class AppConfig(BaseModel):
    model_list: list[ModelDeployment] = Field(default_factory=list)
    router_settings: RouterSettings = Field(default_factory=RouterSettings)
    litellm_settings: LiteLLMSettings = Field(default_factory=LiteLLMSettings)
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
    salt_key: str = "change-me"


def _resolve_env_token(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("os.environ/"):
        env_name = value.split("/", 1)[1]
        return os.getenv(env_name)
    if isinstance(value, dict):
        return {k: _resolve_env_token(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_token(v) for v in value]
    return value


def load_yaml_config(path: str | Path) -> AppConfig:
    from src.config_runtime.secrets import SecretResolver

    cfg_path = Path(path)
    if not cfg_path.exists():
        return AppConfig()

    data = yaml.safe_load(cfg_path.read_text()) or {}
    resolved = SecretResolver().resolve_tree(_resolve_env_token(data))
    return AppConfig.model_validate(resolved)


@lru_cache
def get_settings() -> Settings:
    return Settings()
