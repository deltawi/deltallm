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
    accounting_protocol_enabled: bool
    accounting_execution_mode: str
    accounting_grants_enabled: bool
    accounting_projection_worker_enabled: bool
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

    def validate_production(
        self,
        *,
        role: str = "api",
        accounting_worker_present: bool = False,
        accounting_request_present: bool = False,
    ) -> None:
        if self.accounting_execution_mode == "local_journal":
            if not (
                self.accounting_protocol_enabled
                and self.accounting_grants_enabled
                and accounting_worker_present
                and accounting_request_present
                and self.accounting_projection_worker_enabled == (role == "accountingWorker")
            ):
                raise RuntimeError("Runtime native accounting topology is incompatible")
            if role in {"accountingRequest", "accountingWorker"}:
                return
            # Minimal native processors do not consume unrelated legacy outboxes.
            accounting_worker_present = False
        owns_durable_workers = not accounting_worker_present or role == "accountingWorker"
        durable_workers_ready = not owns_durable_workers or (
            self.audit_ingestion_worker_enabled
            and self.spend_ingestion_worker_enabled
            and (
                not self.accounting_protocol_enabled
                or self.accounting_execution_mode == "local_journal"
                or self.accounting_projection_worker_enabled
            )
        )
        required = (
            self.gateway_ingress_enabled,
            self.gateway_preflight_capacity_enabled,
            self.audit_enabled,
            durable_workers_ready,
            self.spend_operation_intents_enabled or self.accounting_protocol_enabled,
            self.redis_degraded_mode == "fail_closed",
            self.budget_enforcement_query_mode == "combined",
            self.audit_ingestion_mode == self.spend_ingestion_mode == "outbox",
            self.model_deployment_source == "db_only",
            not self.model_deployment_bootstrap_from_config,
        )
        if not all(required):
            raise RuntimeError("Runtime production admission and durability policy is incompatible")
