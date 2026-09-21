"""Disposable kind ownership for the exact-image lifecycle acceptance job.

Every Kubernetes command uses a newly created kubeconfig; the user's current
context is never read or modified. Only this invocation's cluster is deleted.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

import yaml

KIND_VERSION = "v0.31.0"
NODE_IMAGE = (
    "kindest/node:v1.34.3@sha256:08497ee19eace7b4b5348db5c6a1591d7752b164530a36f855cb0f2bdcbadd48"
)
NAMESPACE = "lifecycle"
MASTER_KEY = "sk-pr8-local-master-only-0000000000000000"
SALT_KEY = "pr8-local-salt-only-0000000000000000"
LOAD_KEY = "sk-concurrency-pr8-local-only-000000000000"


class LifecycleCluster:
    def __init__(self, output: Path, *, kind: str = "kind") -> None:
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.kind = kind
        self.name = "deltallm-pr8-" + uuid4().hex[:8]
        self.directory = tempfile.TemporaryDirectory(prefix="deltallm-pr8-")
        self.kubeconfig = str(Path(self.directory.name) / "kubeconfig")
        self.sequence = 0
        self._sequence_lock = threading.Lock()
        self.started = time.monotonic()
        self.events: list[dict[str, object]] = []

    def event(self, name: str, **details: object) -> None:
        if len(self.events) >= 256:
            raise RuntimeError("lifecycle event allocation exceeded")
        self.events.append({"event": name, "seconds": time.monotonic() - self.started, **details})
        (self.output / "events.json").write_text(json.dumps(self.events, indent=2) + "\n")

    def run(
        self, *command: str, timeout: float = 300, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        with self._sequence_lock:
            self.sequence += 1
            sequence = self.sequence
        # Artifact logs are bounded by command time, and retained only on this
        # disposable fixture. No environment or production credentials are logged.
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:

            def decoded(value: bytes | str | None) -> str:
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

            result = subprocess.CompletedProcess(
                command, 124, decoded(error.stdout), decoded(error.stderr) + "\ncommand timeout\n"
            )
        (self.output / f"command-{sequence:03}.log").write_text(
            (result.stdout + result.stderr)[-2 * 1024 * 1024 :]
        )
        if check and result.returncode:
            raise RuntimeError(f"{command[0]} failed; see command-{sequence:03}.log")
        return result

    def kubectl(self, *args: str, **options: object) -> subprocess.CompletedProcess[str]:
        return self.run(
            "kubectl", "--kubeconfig", self.kubeconfig, "-n", NAMESPACE, *args, **options
        )

    def helm(self, *args: str, **options: object) -> subprocess.CompletedProcess[str]:
        return self.run("helm", "--kubeconfig", self.kubeconfig, "-n", NAMESPACE, *args, **options)

    def apply(self, documents: list[dict[str, object]]) -> None:
        path = Path(self.directory.name) / "resources.yaml"
        path.write_text(yaml.safe_dump_all(documents))
        self.kubectl("apply", "-f", str(path))

    @contextmanager
    def owned(self, image: str):
        version = self.run(self.kind, "version").stdout
        if KIND_VERSION not in version:
            raise ValueError(f"Acceptance requires kind {KIND_VERSION}")
        try:
            self.run(
                self.kind,
                "create",
                "cluster",
                "--name",
                self.name,
                "--kubeconfig",
                self.kubeconfig,
                "--image",
                NODE_IMAGE,
                "--wait",
                "180s",
                timeout=600,
            )
            self.kubectl(
                "-n", "kube-system", "rollout", "status", "daemonset/kube-proxy", "--timeout=120s"
            )
            self.kubectl(
                "-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=120s"
            )
            self.run(self.kind, "load", "docker-image", image, "--name", self.name, timeout=600)
            self.kubectl("create", "namespace", NAMESPACE)
            self.event("cluster_created", image=image, node_image=NODE_IMAGE)
            yield self
        finally:
            try:
                self.kubectl("get", "pods,jobs,deployments", "-o", "json", check=False, timeout=30)
                self.kubectl(
                    "logs",
                    "-l",
                    "app.kubernetes.io/instance=gateway",
                    "--all-containers",
                    "--prefix",
                    "--tail=300",
                    "--request-timeout=5s",
                    "--pod-running-timeout=5s",
                    check=False,
                    timeout=30,
                )
            finally:
                self.run(
                    self.kind,
                    "delete",
                    "cluster",
                    "--name",
                    self.name,
                    "--kubeconfig",
                    self.kubeconfig,
                    check=False,
                    timeout=120,
                )
                self.directory.cleanup()

    @contextmanager
    def follow_logs(self, pod: str):
        with (self.output / (pod + ".log")).open("w") as log:
            process = subprocess.Popen(
                [
                    "kubectl",
                    "--kubeconfig",
                    self.kubeconfig,
                    "-n",
                    NAMESPACE,
                    "logs",
                    "--follow",
                    "--timestamps",
                    pod,
                ],
                stdout=log,
                stderr=log,
            )
            try:
                yield
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    @contextmanager
    def forward(self, target: str, remote: int):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log = tempfile.TemporaryFile()
        process = subprocess.Popen(
            [
                "kubectl",
                "--kubeconfig",
                self.kubeconfig,
                "-n",
                NAMESPACE,
                "port-forward",
                "--address",
                "127.0.0.1",
                target,
                f"{port}:{remote}",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("fixture port-forward exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("fixture port-forward did not start")
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
