"""An explicit allowlist prevents credentials or full runtime settings entering results."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.database_settings import DatabaseAllocationSettings
from src.ingress import IngressLimits
from src.services.auth_fallback import AuthFallbackLimits


class ServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    server_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    server_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_python: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    api_processes: int = Field(ge=1, le=16)
    host_cpu_count: int | None = Field(ge=1)
    main_db_pool_size: int = Field(ge=1)
    telemetry_db_pool_size: int = Field(ge=1)
    preflight_global: int = Field(ge=1)
    preflight_org: int = Field(ge=1)
    budget_query_mode: Literal["legacy", "combined"]
    audit_ingestion_mode: Literal["outbox"]
    spend_ingestion_mode: Literal["outbox"]
    redis_degraded_mode: Literal["fail_closed"]
    postgres_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){0,2}$")
    redis_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){0,2}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_allocations: DatabaseAllocationSettings | None = None
    ingress: IngressLimits | None = None
    auth_fallback: AuthFallbackLimits | None = None
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    cpu_limit_cores: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    memory_limit_mib: int | None = Field(default=None, gt=0)
    postgres_cpu_limit_cores: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    postgres_memory_limit_mib: int | None = Field(default=None, gt=0)
    redis_cpu_limit_cores: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    redis_memory_limit_mib: int | None = Field(default=None, gt=0)


def read_manifest(path: Path) -> ServerManifest:
    return ServerManifest.model_validate_json(path.read_text())


async def local_manifest(api_processes: int) -> ServerManifest:
    import yaml

    from src.config import GeneralSettings, Settings, _resolve_env_token
    from src.config_startup import startup_field_values
    from src.db.allocation_config import resolve_allocation_settings
    from tests.performance.gateway_concurrency_dependencies import local_dependencies

    profile_path = Path(
        os.getenv("DELTALLM_CONFIG_PATH", "tests/performance/gateway_concurrency_profile.yaml")
    )
    profile = yaml.safe_load(profile_path.read_text())["general_settings"]
    # Resolve only the explicit, nonsecret budget allowlist. Loading the entire
    # application config would unnecessarily resolve provider/master credentials.
    budget_fields = set(DatabaseAllocationSettings.model_fields) | {
        prefix + field.name
        for prefix, model in (
            ("gateway_ingress_", IngressLimits),
            ("auth_fallback_", AuthFallbackLimits),
        )
        for field in fields(model)
    }
    general = GeneralSettings.model_validate(
        _resolve_env_token(
            {name: value for name, value in profile.items() if name in budget_fields}
        )
    )
    environment = Settings()
    async with local_dependencies() as dependencies:
        async with asyncio.timeout(5):
            rows = await dependencies.database.query_raw("SHOW server_version")
            redis_version = (await dependencies.redis.info("server"))["redis_version"]
        match = re.match(r"[0-9]+(?:\.[0-9]+){0,2}", rows[0]["server_version"])
        if match is None:
            raise ValueError("Cannot identify PostgreSQL version")
        postgres_version = match.group()
    digest = hashlib.sha256()
    for path in sorted(Path("src").rglob("*.py")) + [Path("uv.lock")]:
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return ServerManifest(
        server_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        server_source_sha256=digest.hexdigest(),
        server_python=sys.version.split()[0],
        api_processes=api_processes,
        host_cpu_count=os.cpu_count(),
        main_db_pool_size=profile["db_pool_size"],
        telemetry_db_pool_size=profile["telemetry_db_pool_size"],
        preflight_global=profile["gateway_preflight_global_max_parallel"],
        preflight_org=profile["gateway_preflight_org_max_parallel"],
        budget_query_mode=profile["budget_enforcement_query_mode"],
        audit_ingestion_mode=profile["audit_ingestion_mode"],
        spend_ingestion_mode=profile["spend_ingestion_mode"],
        redis_degraded_mode=profile["redis_degraded_mode"],
        postgres_version=postgres_version,
        redis_version=redis_version,
        profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        database_allocations=resolve_allocation_settings(general, environment),
        ingress=IngressLimits.from_settings(general, environment),
        auth_fallback=AuthFallbackLimits(
            **startup_field_values(
                AuthFallbackLimits(), general, environment, prefix="auth_fallback_"
            )
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-processes", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = asyncio.run(local_manifest(args.api_processes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n")
