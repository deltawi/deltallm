"""Typed consumer of Helm's allocation report; no replica arithmetic lives here."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

Count = Annotated[int, Field(ge=0, le=10**12, strict=True)]
Role = Literal["api", "batchWorker"]
MAX_REPORT_BYTES = 2 * 1024 * 1024


class ReportModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )


class PoolAllocation(ReportModel):
    postgresql: Count
    redis_critical: Count
    redis_cache: Count
    upstream_http: Count
    control_http: Count
    auxiliary_http: Count


class ProcessDescriptors(ReportModel):
    python: Count
    engine: Count
    engine_processes: Count
    pod: Count


class RoleAllocation(ReportModel):
    max_replicas: Count
    processes_per_pod: Count
    steady_processes: Count
    peak_processes: Count
    pools: PoolAllocation
    file_descriptors: ProcessDescriptors


class CacheRedisAllocation(ReportModel):
    separate: bool
    max_clients: Count
    reserved_clients: Count


class DescriptorAllocation(ReportModel):
    process_limit: Count
    engine_limit: Count
    inbound_connections_per_process: Count
    node_peak: Count
    node_limit: Count
    max_pods_per_node: Count


class ProviderAllocation(ReportModel):
    rpm: Count
    tpm: Count
    concurrency: Count
    maximum_rpm: Count
    maximum_tpm: Count
    maximum_concurrency: Count
    enforcement: Literal["workload-envelope"]
    model_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = Field(
        min_length=1, max_length=1024
    )


class CapacityReport(ReportModel):
    schema_version: Literal[1]
    extended: bool
    production: bool
    roles: dict[Role, RoleAllocation] = Field(min_length=1, max_length=2)
    peak_processes: Count
    postgresql_connections: Count
    redis_critical_connections: Count
    redis_cache_connections: Count
    redis_connections: Count
    postgresql_maximum: Count
    redis_maximum: Count
    proxy_pools_per_client: Count
    cache_redis: CacheRedisAllocation
    file_descriptors: DescriptorAllocation
    provider_domains: dict[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")], ProviderAllocation
    ] = Field(max_length=64)

    @classmethod
    def read(cls, path: str | Path) -> "CapacityReport":
        try:
            with Path(path).open("rb") as source:
                data = source.read(MAX_REPORT_BYTES + 1)
            if len(data) > MAX_REPORT_BYTES:
                raise ValueError("capacity report exceeds its byte limit")
            return cls.model_validate_json(data)
        except (OSError, ValueError, ValidationError):
            # File contents and operator paths can contain secrets if miswired.
            raise RuntimeError("Deployment capacity report is missing or invalid") from None
