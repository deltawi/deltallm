"""Startup-only accounting protocol and projection worker settings."""

from pydantic import BaseModel, Field, model_validator


class AccountingProtocolSettings(BaseModel):
    accounting_protocol_enabled: bool = False
    accounting_protocol_generation: int = Field(default=1, ge=1, le=2**63 - 1)
    accounting_microbatch_max_size: int = Field(default=8, ge=1, le=256)
    accounting_microbatch_dwell_ms: int = Field(default=2, ge=0, le=50)
    accounting_reservation_max_pending: int = Field(default=4096, ge=1, le=100_000)
    accounting_finalization_max_pending: int = Field(default=8192, ge=1, le=100_000)
    accounting_reservation_max_pending_bytes: int = Field(
        default=8_388_608, ge=1_048_576, le=67_108_864
    )
    accounting_finalization_max_pending_bytes: int = Field(
        default=8_388_608, ge=1_048_576, le=67_108_864
    )
    accounting_statement_timeout_ms: int = Field(default=250, ge=10, le=2000)
    accounting_reservation_ack_timeout_ms: int = Field(default=1000, ge=10, le=5000)
    accounting_finalization_ack_timeout_ms: int = Field(default=2000, ge=10, le=5000)
    accounting_hot_path_db_pool_size: int = Field(default=2, ge=1, le=32)
    accounting_max_provider_attempts: int = Field(default=3, ge=1, le=128)
    accounting_grants_enabled: bool = True
    accounting_grant_target_operations: int = Field(default=32, ge=1, le=1024)
    accounting_grant_ttl_seconds: int = Field(default=30, ge=1, le=300)
    accounting_projection_worker_enabled: bool = False
    accounting_projection_batch_size: int = Field(default=64, ge=1, le=256)
    accounting_projection_max_concurrent_partitions: int = Field(default=4, ge=1, le=64)
    accounting_projection_lease_seconds: int = Field(default=30, ge=5, le=300)
    accounting_projection_poll_interval_ms: int = Field(default=50, ge=10, le=10_000)
    accounting_projection_maintenance_interval_ms: int = Field(default=1000, ge=100, le=60_000)

    @model_validator(mode="after")
    def validate_accounting_queues(self):
        if self.accounting_reservation_max_pending < self.accounting_microbatch_max_size:
            raise ValueError("accounting reservation capacity must fit one microbatch")
        if self.accounting_finalization_max_pending < self.accounting_microbatch_max_size:
            raise ValueError("accounting finalization capacity must fit one microbatch")
        if (
            min(
                self.accounting_reservation_ack_timeout_ms,
                self.accounting_finalization_ack_timeout_ms,
            )
            < 2 * self.accounting_statement_timeout_ms
        ):
            raise ValueError(
                "accounting ACK timeouts must fit one statement attempt and one recovery query"
            )
        if self.accounting_projection_worker_enabled and not self.accounting_protocol_enabled:
            raise ValueError("accounting projection worker requires accounting protocol")
        return self


ACCOUNTING_PROTOCOL_FIELDS = frozenset(AccountingProtocolSettings.model_fields)
