"""Startup-only accounting protocol and projection worker settings."""

from ipaddress import ip_network
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class AccountingProtocolSettings(BaseModel):
    accounting_protocol_enabled: bool = False
    accounting_execution_mode: Literal["assigned", "local_journal"] = "assigned"
    accounting_request_url: str | None = Field(default=None, max_length=2048)
    accounting_rpc_signing_secret: SecretStr | None = Field(
        default=None, min_length=32, max_length=4096
    )
    accounting_rpc_allow_http: bool = False
    accounting_rpc_allowed_ports: tuple[Annotated[int, Field(ge=1, le=65535, strict=True)], ...] = (
        Field(default=(443,), min_length=1, max_length=16)
    )
    accounting_rpc_allowed_private_cidrs: tuple[Annotated[str, Field(max_length=128)], ...] = Field(
        default=(), max_length=16
    )
    accounting_rpc_max_connections: int = Field(default=16, ge=1, le=256)
    accounting_local_max_subjects: int = Field(default=1024, ge=1, le=4096)
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

    @field_validator("accounting_request_url")
    @classmethod
    def validate_rpc_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            origin = urlsplit(value)
            port = origin.port
        except ValueError:
            raise ValueError("accounting RPC origin is invalid") from None
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
            or port == 0
        ):
            raise ValueError("accounting RPC requires a credential-free HTTP origin")
        return value.rstrip("/")

    @field_validator("accounting_rpc_allowed_private_cidrs")
    @classmethod
    def validate_rpc_private_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        networks = tuple(ip_network(value, strict=True) for value in values)
        if any(not network.is_private for network in networks):
            raise ValueError("accounting RPC allowlist must contain private networks")
        return tuple(str(network) for network in networks)

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
        if self.accounting_execution_mode == "local_journal":
            if not self.accounting_protocol_enabled or not self.accounting_grants_enabled:
                raise ValueError("local journal requires enabled accounting and funded grants")
            if self.accounting_projection_poll_interval_ms > 5000:
                raise ValueError("local journal projection polling must be at most five seconds")
            if self.accounting_projection_maintenance_interval_ms > 2000:
                raise ValueError("local journal recovery must run at least every two seconds")
        return self


ACCOUNTING_PROTOCOL_FIELDS = frozenset(AccountingProtocolSettings.model_fields)
