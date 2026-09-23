"""Finite startup policy snapshot governed by a deployment capacity contract."""

from pydantic import BaseModel, ConfigDict


class CapacityRuntimePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway_ingress_enabled: bool
    gateway_ingress_max_active: int
    gateway_preflight_capacity_enabled: bool
    gateway_preflight_global_max_parallel: int
    gateway_preflight_org_max_parallel: int
    redis_degraded_mode: str
    budget_enforcement_query_mode: str
    audit_enabled: bool
    audit_ingestion_mode: str
    audit_ingestion_worker_enabled: bool
    spend_ingestion_mode: str
    spend_ingestion_worker_enabled: bool
    spend_operation_intents_enabled: bool
    model_deployment_source: str
    model_deployment_bootstrap_from_config: bool
    upstream_http_max_connections: int
    upstream_http_max_keepalive_connections: int
    upstream_http_connect_timeout_seconds: float
    upstream_http_read_timeout_seconds: float
    upstream_http_write_timeout_seconds: float
    upstream_http_pool_timeout_seconds: float
    upstream_http_keepalive_expiry_seconds: float

    @classmethod
    def from_general(cls, general: BaseModel) -> "CapacityRuntimePolicy":
        return cls.model_validate(general.model_dump(include=set(cls.model_fields)))

    def validate_production(self) -> None:
        required = (
            self.gateway_ingress_enabled,
            self.gateway_preflight_capacity_enabled,
            self.audit_enabled,
            self.audit_ingestion_worker_enabled,
            self.spend_ingestion_worker_enabled,
            self.spend_operation_intents_enabled,
            self.redis_degraded_mode == "fail_closed",
            self.budget_enforcement_query_mode == "combined",
            self.audit_ingestion_mode == self.spend_ingestion_mode == "outbox",
            self.model_deployment_source == "db_only",
            not self.model_deployment_bootstrap_from_config,
        )
        if not all(required):
            raise RuntimeError("Runtime production admission and durability policy is incompatible")
