"""An explicit allowlist prevents credentials or full runtime settings entering results."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    from prisma import Prisma
    from redis.asyncio import Redis
    import yaml

    from tests.performance.gateway_concurrency_fixture import (
        fixture_database_url,
        require_local_url,
    )

    profile_path = Path("tests/performance/gateway_concurrency_profile.yaml")
    profile = yaml.safe_load(profile_path.read_text())["general_settings"]
    db = Prisma(datasource={"url": fixture_database_url()})
    redis = Redis.from_url(
        require_local_url(os.environ["REDIS_URL"], schemes={"redis", "rediss"}),
        decode_responses=True,
        max_connections=2,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    await db.connect()
    try:
        async with asyncio.timeout(5):
            rows = await db.query_raw("SHOW server_version")
            redis_version = (await redis.info("server"))["redis_version"]
        match = re.match(r"[0-9]+(?:\.[0-9]+){0,2}", rows[0]["server_version"])
        if match is None:
            raise ValueError("Cannot identify PostgreSQL version")
        postgres_version = match.group()
    finally:
        await redis.aclose()
        await db.disconnect()
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
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-processes", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = asyncio.run(local_manifest(args.api_processes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n")
