"""Bounded in-cluster load jobs use the existing disposable cluster owner."""

from __future__ import annotations
import asyncio
import base64
import binascii
import json
from pathlib import Path
import re
from time import perf_counter
import httpx
from scripts.measure_gateway_load import RunResult, read_results
from tests.performance.gateway_concurrency_fixture import MODEL
from tests.performance.lifecycle_cluster import LifecycleCluster, LOAD_KEY

CLUSTER_GENERATOR_JOB = "capacity-load-generator"
CLUSTER_GENERATOR_PROOF_JOB = "capacity-load-generator-proof"
CLUSTER_GENERATOR_OUTPUT = "/tmp/deltallm-load-results"
CLUSTER_GENERATOR_SCRIPT_CONFIG = "capacity-load-generator-script"
CLUSTER_GENERATOR_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "measure_gateway_load.py"
)
CLUSTER_GENERATOR_COORDINATOR_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "measure_gateway_load_sharded.py"
)
CLUSTER_GENERATOR_ARTIFACT_PORT = 8081
CLUSTER_GENERATOR_MANIFEST_BYTES = 4096
CLUSTER_GENERATOR_RAW_BYTES = 64 * 1024 * 1024
CLUSTER_GENERATOR_SUMMARY_BYTES = 65536
CLUSTER_GENERATOR_PUBLICATION_GRACE_SECONDS = 90


def cluster_generator_job(
    image: str,
    endpoints: list[str],
    *,
    rate: float,
    duration: float,
    job_name: str = CLUSTER_GENERATOR_JOB,
    model: str = MODEL,
    bypass_cache: bool = True,
) -> dict[str, object]:
    """Build one bounded multi-process generator job without credentials in arguments."""

    if (
        not image
        or not 1 <= len(endpoints) <= 16
        or job_name not in {CLUSTER_GENERATOR_JOB, CLUSTER_GENERATOR_PROOF_JOB}
    ):
        raise ValueError("cluster load generator requires an image and bounded endpoints")
    arguments = [
        "/opt/deltallm-load/measure_gateway_load_sharded.py",
        "--workers",
        str(len(endpoints)),
        "--model",
        model,
        "--rate",
        str(rate),
        "--duration",
        str(duration),
        "--max-in-flight",
        "1000",
        "--max-keepalive",
        "100",
        "--timeout",
        "10",
        "--drain-timeout",
        "15",
        "--expect-fixed-one-token",
        "--output-dir",
        CLUSTER_GENERATOR_OUTPUT,
        "--artifact-server-port",
        str(CLUSTER_GENERATOR_ARTIFACT_PORT),
        "--artifact-server-timeout",
        str(CLUSTER_GENERATOR_PUBLICATION_GRACE_SECONDS + 30),
    ]
    if bypass_cache:
        arguments.append("--bypass-cache")
    for endpoint in endpoints:
        arguments.extend(("--url", endpoint))
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name},
        "spec": {
            "activeDeadlineSeconds": int(
                duration + CLUSTER_GENERATOR_PUBLICATION_GRACE_SECONDS + 60
            ),
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/instance": "gateway",
                        "app.kubernetes.io/component": "load-generator",
                    }
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "restartPolicy": "Never",
                    "terminationGracePeriodSeconds": 5,
                    "containers": [
                        {
                            "name": "generator",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "workingDir": "/app",
                            "command": ["python"],
                            "args": arguments,
                            "ports": [
                                {
                                    "name": "artifacts",
                                    "containerPort": CLUSTER_GENERATOR_ARTIFACT_PORT,
                                }
                            ],
                            "volumeMounts": [
                                {
                                    "name": "generator-script",
                                    "mountPath": "/opt/deltallm-load",
                                    "readOnly": True,
                                }
                            ],
                            "env": [{"name": "DELTALLM_LOAD_API_KEY", "value": LOAD_KEY}],
                            "resources": {
                                "requests": {"cpu": "1", "memory": "256Mi"},
                                "limits": {"cpu": "4", "memory": "1Gi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "generator-script",
                            "configMap": {"name": CLUSTER_GENERATOR_SCRIPT_CONFIG},
                        }
                    ],
                },
            },
        },
    }


def cluster_generator_script_config() -> dict[str, object]:
    script = CLUSTER_GENERATOR_SCRIPT.read_text(encoding="utf-8")
    coordinator = CLUSTER_GENERATOR_COORDINATOR_SCRIPT.read_text(encoding="utf-8")
    if len(script.encode()) + len(coordinator.encode()) > 512 * 1024:
        raise ValueError("cluster load generator scripts exceed their bounded ConfigMap budget")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CLUSTER_GENERATOR_SCRIPT_CONFIG},
        "data": {
            "measure_gateway_load.py": script,
            "measure_gateway_load_sharded.py": coordinator,
        },
    }


def read_cluster_generator_artifact(
    cluster: LifecycleCluster,
    pod: str,
    name: str,
    *,
    maximum_bytes: int,
) -> bytes | None:
    """Read one bounded UTF-8 artifact when the pod port-forward is unavailable."""

    if not re.fullmatch(
        r"artifact-manifest\.json|gateway-load-[0-9a-f]{32}(?:-summary\.json|\.jsonl(?:\.gz)?)",
        name,
    ):
        raise ValueError("load generator artifact name is invalid")
    if not 1 <= maximum_bytes <= CLUSTER_GENERATOR_RAW_BYTES:
        raise ValueError("load generator artifact byte budget is invalid")
    program = (
        "import base64,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);limit=int(sys.argv[2]);"
        "sys.exit(2) if p.stat().st_size>limit else None;"
        "data=p.read_bytes();"
        "sys.exit(2) if len(data)>limit else "
        "sys.stdout.write(base64.b64encode(data).decode('ascii'))"
    )
    result = cluster.kubectl(
        "exec",
        pod,
        "--",
        "python",
        "-c",
        program,
        f"{CLUSTER_GENERATOR_OUTPUT}/{name}",
        str(maximum_bytes),
        timeout=30,
        check=False,
    )
    if result.returncode:
        return None
    try:
        body = base64.b64decode(result.stdout, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(body) > maximum_bytes:
        raise RuntimeError("load generator artifact exceeds its budget")
    return body


async def run_cluster_generator(
    cluster: LifecycleCluster,
    image: str,
    endpoints: list[str],
    *,
    rate: float,
    duration: float,
    output: Path,
    job_name: str = CLUSTER_GENERATOR_JOB,
    model: str = MODEL,
    bypass_cache: bool = True,
) -> RunResult:
    """Run traffic inside the owned cluster and rehydrate only bounded artifacts."""

    if output.exists():
        raise ValueError("cluster load generator output already exists")
    document = cluster_generator_job(
        image,
        endpoints,
        rate=rate,
        duration=duration,
        job_name=job_name,
        model=model,
        bypass_cache=bypass_cache,
    )
    await asyncio.to_thread(cluster.apply, [cluster_generator_script_config(), document])
    ready = await asyncio.to_thread(
        cluster.kubectl,
        "wait",
        "--for=condition=Ready",
        "pod",
        "-l",
        f"job-name={job_name}",
        "--timeout=60s",
        timeout=75,
        check=False,
    )
    if ready.returncode:
        raise RuntimeError("in-cluster load generator did not become ready")
    pods = await asyncio.to_thread(
        cluster.kubectl,
        "get",
        "pods",
        "-l",
        f"job-name={job_name}",
        "-o",
        "json",
    )
    items = json.loads(pods.stdout).get("items", [])
    if len(items) != 1:
        raise RuntimeError("in-cluster load generator pod identity is ambiguous")
    pod = items[0]["metadata"]["name"]
    try:
        with cluster.forward(
            "pod/" + pod,
            CLUSTER_GENERATOR_ARTIFACT_PORT,
        ) as artifact_port:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{artifact_port}",
                timeout=5,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                deadline = perf_counter() + duration + CLUSTER_GENERATOR_PUBLICATION_GRACE_SECONDS
                exec_fallback_at = perf_counter() + duration + 20
                manifest_bytes: bytes | None = None
                artifact_transport = "http"
                while perf_counter() < deadline:
                    try:
                        response = await client.get("/artifact-manifest.json")
                        if response.status_code == 200:
                            if len(response.content) > CLUSTER_GENERATOR_MANIFEST_BYTES:
                                raise RuntimeError("load generator artifact manifest is too large")
                            manifest_bytes = response.content
                            break
                    except httpx.TransportError:
                        pass
                    if perf_counter() >= exec_fallback_at:
                        manifest_bytes = await asyncio.to_thread(
                            read_cluster_generator_artifact,
                            cluster,
                            pod,
                            "artifact-manifest.json",
                            maximum_bytes=CLUSTER_GENERATOR_MANIFEST_BYTES,
                        )
                        if manifest_bytes is not None:
                            artifact_transport = "kubectl_exec"
                            break
                        exec_fallback_at = perf_counter() + 5
                    await asyncio.sleep(0.25)
                if manifest_bytes is None:
                    raise RuntimeError("in-cluster load generator artifacts were not published")
                manifest = json.loads(manifest_bytes)
                raw_name = manifest.get("raw") if isinstance(manifest, dict) else None
                summary_name = manifest.get("summary") if isinstance(manifest, dict) else None
                raw_match = (
                    re.fullmatch(r"gateway-load-([0-9a-f]{32})\.jsonl(?:\.gz)?", raw_name)
                    if isinstance(raw_name, str)
                    else None
                )
                summary_match = (
                    re.fullmatch(
                        r"gateway-load-([0-9a-f]{32})-summary\.json",
                        summary_name,
                    )
                    if isinstance(summary_name, str)
                    else None
                )
                if (
                    raw_match is None
                    or summary_match is None
                    or raw_match.group(1) != summary_match.group(1)
                ):
                    raise RuntimeError("in-cluster load generator manifest is invalid")

                async def download_http(name: str, maximum_bytes: int) -> bytes:
                    body = bytearray()
                    async with client.stream("GET", "/" + name) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > maximum_bytes:
                                raise RuntimeError("load generator artifact exceeds its budget")
                            body.extend(chunk)
                    return bytes(body)

                if artifact_transport == "http":
                    try:
                        raw_body, summary_body = await asyncio.gather(
                            download_http(raw_name, CLUSTER_GENERATOR_RAW_BYTES),
                            download_http(summary_name, CLUSTER_GENERATOR_SUMMARY_BYTES),
                        )
                    except httpx.TransportError:
                        artifact_transport = "kubectl_exec"
                if artifact_transport == "kubectl_exec":
                    raw_body, summary_body = await asyncio.gather(
                        asyncio.to_thread(
                            read_cluster_generator_artifact,
                            cluster,
                            pod,
                            raw_name,
                            maximum_bytes=CLUSTER_GENERATOR_RAW_BYTES,
                        ),
                        asyncio.to_thread(
                            read_cluster_generator_artifact,
                            cluster,
                            pod,
                            summary_name,
                            maximum_bytes=CLUSTER_GENERATOR_SUMMARY_BYTES,
                        ),
                    )
                    if raw_body is None or summary_body is None:
                        raise RuntimeError("load generator artifacts disappeared during transfer")
                cluster.event(
                    "load_generator_artifacts_retrieved",
                    transport=artifact_transport,
                    raw_bytes=len(raw_body),
                    summary_bytes=len(summary_body),
                )
        output.mkdir(parents=True)
        from tests.performance.native_qualification_resources import pod_cpu_counters

        generator_cpu = await asyncio.to_thread(pod_cpu_counters, cluster, pod)
        (output / "cpu-counters.json").write_text(
            json.dumps(
                {"window": "generator container lifetime", "counters": generator_cpu}, indent=2
            )
            + "\n"
        )
        raw_path = output / raw_name
        summary_path = output / summary_name
        raw_path.write_bytes(raw_body)
        summary_path.write_bytes(summary_body)
        return read_results(raw_path, summary_path)
    finally:
        logs = await asyncio.to_thread(
            cluster.kubectl,
            "logs",
            f"job/{job_name}",
            "--all-containers=true",
            check=False,
        )
        (cluster.output / f"{job_name}.log").write_text(
            (logs.stdout + logs.stderr)[-2 * 1024 * 1024 :]
        )
        await asyncio.to_thread(
            cluster.kubectl,
            "delete",
            "job",
            job_name,
            "--wait=true",
            "--timeout=60s",
            check=False,
        )
