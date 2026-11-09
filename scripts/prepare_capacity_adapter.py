"""Prepare a pinned adapter chart and private serving-TLS values; never deploy it."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import BaseModel, ConfigDict, Field
import yaml

MONITORING = Path(__file__).resolve().parents[1] / "deploy/kubernetes/monitoring"
MAX_CHART_BYTES = 8 * 1024 * 1024


class ChartDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    url: str = Field(pattern=r"^https://github\.com/prometheus-community/helm-charts/releases/")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MonitoringDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    adapter_chart: ChartDependency = Field(alias="adapterChart")
    prometheus_image: str = Field(alias="prometheusImage", pattern=r"@sha256:[a-f0-9]{64}$")
    metrics_server_image: str = Field(alias="metricsServerImage", pattern=r"@sha256:[a-f0-9]{64}$")

    edge_image: str = Field(alias="edgeImage", pattern=r"@sha256:[a-f0-9]{64}$")

    @classmethod
    def read(cls) -> "MonitoringDependencies":
        return cls.model_validate_json((MONITORING / "dependencies.json").read_text())


@dataclass(frozen=True)
class AdapterFiles:
    chart: Path
    values: Path


def serving_tls(namespace: str, service: str) -> dict[str, str | bool]:
    """A private CA and server cert for this monitoring release's service names."""
    for name in (namespace, service):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
            raise ValueError("Adapter namespace and service must be DNS labels")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DeltaLLM metrics serving CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    dns = f"{service}.{namespace}.svc"
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns)]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns), x509.DNSName(dns + ".cluster.local")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return {
        "enable": True,
        "ca": ca.public_bytes(serialization.Encoding.PEM).decode(),
        "certificate": certificate.public_bytes(serialization.Encoding.PEM).decode(),
        "key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }


def download_chart(chart: ChartDependency) -> bytes:
    """Fetch the pinned chart, falling back to its OCI copy on ghcr.io.

    GitHub release downloads sometimes return 504 for many minutes. The chart
    publisher also pushes each chart to ghcr.io, where the chart layer is the
    same file; the caller checks the pinned SHA-256 either way.
    """

    for attempt in range(3):
        try:
            with urllib.request.urlopen(chart.url, timeout=30) as response:
                return response.read(MAX_CHART_BYTES + 1)
        except OSError as error:
            print(f"chart download attempt {attempt + 1} failed: {error}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    name = chart.url.rsplit("/", 1)[-1].removesuffix(f"-{chart.version}.tgz")
    repository = f"prometheus-community/charts/{name}"
    token_url = f"https://ghcr.io/token?scope=repository:{repository}:pull"
    with urllib.request.urlopen(token_url, timeout=30) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repository}/blobs/sha256:{chart.sha256}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_CHART_BYTES + 1)


def prepare_adapter(
    output: Path, *, namespace: str, release: str, prometheus_url: str
) -> AdapterFiles:
    dependencies = MonitoringDependencies.read()
    output.mkdir(parents=True, exist_ok=True)
    chart = output / f"prometheus-adapter-{dependencies.adapter_chart.version}.tgz"
    if chart.exists():
        with chart.open("rb") as source:
            data = source.read(MAX_CHART_BYTES + 1)
    else:
        data = download_chart(dependencies.adapter_chart)
    if (
        len(data) > MAX_CHART_BYTES
        or hashlib.sha256(data).hexdigest() != dependencies.adapter_chart.sha256
    ):
        raise RuntimeError("Prometheus Adapter chart checksum verification failed")
    if not chart.exists():
        with chart.open("xb") as target:
            target.write(data)
    values = yaml.safe_load((MONITORING / "prometheus-adapter-values.yaml").read_text())
    values["fullnameOverride"] = release
    values["tls"] = serving_tls(namespace, release)
    values["prometheus"]["url"] = prometheus_url
    path = output / "adapter-private-values.yaml"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        yaml.safe_dump(values, target)
    return AdapterFiles(chart, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--namespace", default="monitoring")
    parser.add_argument("--release", default="deltallm-metrics")
    parser.add_argument("--prometheus-url", default="http://prometheus.monitoring.svc")
    args = parser.parse_args()
    files = prepare_adapter(
        args.output,
        namespace=args.namespace,
        release=args.release,
        prometheus_url=args.prometheus_url,
    )
    print(json.dumps({"chart": str(files.chart), "private_values_file": str(files.values)}))


if __name__ == "__main__":
    main()
