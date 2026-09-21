"""Compare committed baseline and candidate through the canonical arrival runner."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
from time import monotonic
from uuid import uuid4

import httpx
import yaml

from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_fixture import seed
from tests.performance.gateway_concurrency_manifest import local_manifest
from tests.performance.lifecycle_cluster import LOAD_KEY, MASTER_KEY, SALT_KEY
from tests.performance.run_gateway_concurrency import measure
from tests.performance.run_lifecycle_acceptance import ready


class ComparisonEnvironment:
    def __init__(self, output: Path):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.name = "deltallm-pr8-compare-" + uuid4().hex[:8]
        self.containers: list[str] = []

    def run(
        self, *args: str, timeout: float = 180, check: bool = True, include_stderr: bool = False
    ) -> str:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f"{args[0]} failed: {(result.stdout + result.stderr)[-4096:]}")
        return result.stdout + (result.stderr if include_stderr else "")

    def container(self, suffix: str, image: str, *args: str, command: tuple[str, ...] = ()) -> str:
        name = self.name + "-" + suffix
        self.containers.append(name)
        self.run(
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            "deltallm.test=pr8",
            "--network",
            self.name,
            "--network-alias",
            suffix,
            *args,
            image,
            *command,
        )
        return name

    def port(self, name: str, port: int) -> int:
        item = json.loads(self.run("docker", "inspect", name))[0]
        return int(item["NetworkSettings"]["Ports"][f"{port}/tcp"][0]["HostPort"])

    @contextmanager
    def owned(self):
        self.run("docker", "network", "create", "--label", "deltallm.test=pr8", self.name)
        try:
            yield self
        finally:
            for name in reversed(self.containers):
                logs = self.run(
                    "docker", "logs", "--tail", "500", name, check=False, include_stderr=True
                )
                (self.output / (name + ".log")).write_text(logs)
                self.run("docker", "rm", "--force", name, check=False)
            self.run("docker", "network", "rm", self.name, check=False)


async def compare(args: argparse.Namespace, environment: ComparisonEnvironment) -> None:
    db = environment.container(
        "postgres",
        "postgres:15",
        "--publish",
        "127.0.0.1::5432",
        "--memory",
        "1g",
        "--cpus",
        "2",
        "--env",
        "POSTGRES_PASSWORD=fixture-only",
        "--env",
        "POSTGRES_DB=deltallm_concurrency",
        command=("-c", "shared_preload_libraries=pg_stat_statements", "-c", "max_connections=300"),
    )
    redis = environment.container(
        "redis", "redis:7", "--publish", "127.0.0.1::6379", "--memory", "1g", "--cpus", "2"
    )
    environment.container(
        "provider",
        args.image,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=128m",
        "--mount",
        f"type=bind,source={Path('tests/performance/gateway_concurrency_mock.py').resolve()},target=/fixture/mock.py,readonly",
        command=(
            "python",
            "-m",
            "uvicorn",
            "mock:app",
            "--app-dir",
            "/fixture",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
        ),
    )
    migration = environment.run(
        "docker",
        "run",
        "--rm",
        "--network",
        environment.name,
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--env",
        "DATABASE_URL=postgresql://postgres:fixture-only@postgres:5432/deltallm_concurrency",
        args.image,
        "python",
        "-m",
        "src.prisma_bootstrap",
        "--timeout-seconds",
        "120",
    )
    (args.output / "migration.log").write_text(migration)
    os.environ.update(
        DATABASE_URL=f"postgresql://postgres:fixture-only@127.0.0.1:{environment.port(db, 5432)}/deltallm_concurrency",
        REDIS_URL=f"redis://127.0.0.1:{environment.port(redis, 6379)}/0",
        DELTALLM_LOAD_API_KEY=LOAD_KEY,
        DELTALLM_MASTER_KEY=MASTER_KEY,
        DELTALLM_SALT_KEY=SALT_KEY,
    )
    await seed()
    async with local_database() as database:
        await database.execute_raw("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    config = yaml.safe_load(Path("tests/performance/gateway_concurrency_profile.yaml").read_text())
    general = config["general_settings"]
    general.update(
        migration_mode="external",
        budget_enforcement_query_mode="combined",
        spend_operation_intents_enabled=True,
        database_url="postgresql://postgres:fixture-only@postgres:5432/deltallm_concurrency",
        redis_url="redis://redis:6379/0",
        master_key=MASTER_KEY,
        salt_key=SALT_KEY,
    )
    config["model_list"][0]["deltallm_params"]["api_base"] = "http://provider:8000/v1"
    config["router_settings"]["timeout"] = 350
    profile = args.output.resolve() / "profile.yaml"
    profile.write_text(yaml.safe_dump(config))
    os.environ["DELTALLM_CONFIG_PATH"] = str(profile)
    reports = {}
    for label, image in (("before", args.baseline_image), ("after", args.image)):
        command = (
            ("python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "4000")
            if label == "before"
            else ()
        )
        name = environment.container(
            label,
            image,
            "--publish",
            "127.0.0.1::4000",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=128m",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--mount",
            f"type=bind,source={profile},target=/app/config/config.yaml,readonly",
            command=command,
        )
        port = environment.port(name, 4000)
        url = f"http://127.0.0.1:{port}"
        await ready(url, timeout=90)
        manifest = await local_manifest(1)
        metadata = json.loads(args.baseline_manifest.read_text()) if label == "before" else {}
        image_info = json.loads(environment.run("docker", "image", "inspect", image))[0]
        overrides = {
            key: value for key, value in metadata.items() if key in type(manifest).model_fields
        }
        manifest = manifest.model_copy(
            update=overrides
            | {
                "image_digest": image_info["Id"],
                "cpu_limit_cores": 2.0,
                "memory_limit_mib": 2048,
                "postgres_cpu_limit_cores": 2.0,
                "postgres_memory_limit_mib": 1024,
                "redis_cpu_limit_cores": 2.0,
                "redis_memory_limit_mib": 1024,
            }
        )
        manifest_path = args.output / (label + "-manifest.json")
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
        report = await measure(
            argparse.Namespace(
                url=url + "/v1/chat/completions",
                metrics_url=[url + "/metrics"],
                rate=args.rate,
                duration=args.duration,
                label=label,
                output_dir=args.output / label,
                server_manifest=manifest_path,
            )
        )
        assert report["success_count"] == args.rate * args.duration, report["error_counts"]
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            started = monotonic()
            async with client.stream(
                "POST",
                url + "/v1/chat/completions",
                headers={"Authorization": "Bearer " + LOAD_KEY},
                json={
                    "model": "concurrency-fixture",
                    "max_tokens": 1,
                    "stream": True,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "metadata": {"cache": False},
                },
            ) as response:
                assert response.status_code == 200
                assert await anext(response.aiter_bytes())
                report["stream_ttft_seconds"] = monotonic() - started
        reports[label] = report
        environment.run("docker", "stop", "--time", "90", name, timeout=100)
        state = json.loads(environment.run("docker", "inspect", name))[0]["State"]
        report["exit_code"] = state["ExitCode"]
        assert state["ExitCode"] == 0, state
    (args.output / "comparison.json").write_text(json.dumps(reports, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--baseline-image", required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rate", type=int, default=10)
    parser.add_argument("--duration", type=int, default=10)
    args = parser.parse_args()
    environment = ComparisonEnvironment(args.output)
    with environment.owned():
        asyncio.run(compare(args, environment))


if __name__ == "__main__":
    main()
