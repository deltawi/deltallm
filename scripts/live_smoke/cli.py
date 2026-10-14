"""Command line entry point: python -m scripts.live_smoke --base-url URL.

Exit codes: 0 when every check passes, 1 when a check fails, 2 when the run
cannot start (for example, the master key is missing).
"""

from __future__ import annotations

import argparse
from getpass import getpass
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import sys
from time import monotonic, sleep
from typing import TextIO, TypeVar

import httpx

from scripts.live_smoke.batch import ENDPOINT_BY_MODE, run_batch
from scripts.live_smoke.cleanup import cleanup_resources
from scripts.live_smoke.config import (
    DEFAULT_PREFIX,
    MASTER_KEY_ENV,
    ResourceNames,
    SmokeConfig,
    validate_prefix,
)
from scripts.live_smoke.gateway_client import (
    CALL_ERRORS,
    GatewayClient,
    describe_error,
)
from scripts.live_smoke.probes import probe_target, target_mode
from scripts.live_smoke import provisioning
from scripts.live_smoke.results import CheckResult, Recorder, Status
from scripts.live_smoke.tier_sync import Catalog, CallableTarget, load_catalog, sync_tier

T = TypeVar("T")
ACCESS_WAIT_SECONDS = 30.0
# Printable ASCII only: an HTTP client error for a bad header value shows the full value.
MASTER_KEY_PATTERN = re.compile(r"[\x21-\x7e]+")


class SetupAborted(RuntimeError):
    pass


def _csv(values: Sequence[str] | None) -> frozenset[str]:
    return frozenset(
        part.strip() for value in values or () for part in value.split(",") if part.strip()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.live_smoke",
        description=(
            "Check a deployed gateway end to end. Reads the master key from "
            f"{MASTER_KEY_ENV}, or asks for it on a terminal. The key is never an argument."
        ),
    )
    parser.add_argument("--base-url", required=True, help="Gateway URL, e.g. https://gw.example")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="Name prefix of test resources")
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        metavar="NAME",
        help="Only call these models or route groups (repeat or comma-separate)",
    )
    parser.add_argument(
        "--skip-mode",
        action="append",
        dest="skip_modes",
        metavar="MODE",
        help="Do not call targets of this mode, e.g. image_generation",
    )
    parser.add_argument("--no-batch", action="store_true", help="Do not run batch jobs")
    parser.add_argument("--batch-model", help="Chat model for the batch job")
    parser.add_argument("--batch-timeout", type=float, default=300.0, help="Seconds per batch")
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per request")
    parser.add_argument("--max-tokens", type=int, default=32, help="max_tokens for chat calls")
    parser.add_argument("--parallel", type=int, default=4, help="Targets called at once (1-16)")
    parser.add_argument("--tts-voice", default="alloy", help="Voice for speech models")
    parser.add_argument("--image-size", help="Size for image models (default: provider default)")
    parser.add_argument("--json-output", type=Path, help="Also write results to this JSON file")
    cleanup = parser.add_mutually_exclusive_group()
    cleanup.add_argument(
        "--cleanup",
        action="store_true",
        help="After the run, also delete the tier, organization, and test user",
    )
    cleanup.add_argument(
        "--cleanup-only", action="store_true", help="Only delete the test resources"
    )
    return parser


def read_master_key(env: Mapping[str, str], prompt: Callable[[str], str] | None) -> str:
    """Read the key from the environment, else ask for it without echo on a terminal."""

    master_key = env.get(MASTER_KEY_ENV, "").strip()
    if not master_key and prompt is not None:
        master_key = prompt("Master key (input is hidden): ").strip()
    if not master_key:
        raise ValueError(f"set {MASTER_KEY_ENV} to the gateway master key")
    if not MASTER_KEY_PATTERN.fullmatch(master_key):
        raise ValueError(
            "the master key must be one line of printable ASCII characters with no spaces"
        )
    return master_key


def build_config(args: argparse.Namespace, master_key: str) -> SmokeConfig:
    if not 1 <= args.parallel <= 16:
        raise ValueError("--parallel must be between 1 and 16")
    if args.max_tokens < 1 or args.timeout <= 0 or args.batch_timeout <= 0:
        raise ValueError("--max-tokens, --timeout, and --batch-timeout must be positive")
    return SmokeConfig(
        base_url=args.base_url.rstrip("/"),
        master_key=master_key,
        names=ResourceNames(validate_prefix(args.prefix)),
        request_timeout=args.timeout,
        max_tokens=args.max_tokens,
        parallel=args.parallel,
        models=_csv(args.models),
        skip_modes=_csv(args.skip_modes),
        run_batch=not args.no_batch,
        batch_model=args.batch_model,
        batch_timeout=args.batch_timeout,
        tts_voice=args.tts_voice,
        image_size=args.image_size,
        cleanup=args.cleanup,
    )


class SmokeRun:
    def __init__(
        self,
        config: SmokeConfig,
        recorder: Recorder,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self.config = config
        self.names = config.names
        self.recorder = recorder
        self.transport = transport
        self.clock = clock
        self.wait = wait

    def client(self, bearer: str | None = None) -> GatewayClient:
        return GatewayClient(
            self.config.base_url,
            timeout=self.config.request_timeout,
            bearer=bearer,
            transport=self.transport,
        )

    def step(self, name: str, action: Callable[[], T], detail: Callable[[T], str]) -> T:
        try:
            value = action()
        except CALL_ERRORS as exc:
            self.recorder.add(CheckResult("setup", name, Status.FAIL, describe_error(exc)))
            raise SetupAborted(name) from exc
        self.recorder.add(CheckResult("setup", name, Status.PASS, detail(value)))
        return value

    def run(self) -> None:
        with self.client(self.config.master_key) as admin:
            key: provisioning.TestKey | None = None
            try:
                targets, key = self._setup(admin)
                self.recorder.protect(key.raw_key)
                with self.client(key.raw_key) as caller:
                    self._wait_for_access(caller, targets)
                    passed = self._probe_all(caller, targets)
                    if self.config.run_batch:
                        self._run_batches(caller, targets, passed)
            except SetupAborted:
                self.recorder.add(CheckResult("setup", "model checks", Status.SKIP, "setup failed"))
            except Exception as exc:
                # Record any other error, so the summary and the report are still written.
                self.recorder.add(CheckResult("run", "checks", Status.FAIL, describe_error(exc)))
            finally:
                if key is not None:
                    self._delete_key(admin, key)
                if self.config.cleanup:
                    for result in cleanup_resources(
                        admin, self.names, clock=self.clock, wait=self.wait
                    ):
                        self.recorder.add(result)

    def _setup(self, admin: GatewayClient) -> tuple[list[CallableTarget], provisioning.TestKey]:
        names = self.names
        self.step(
            "gateway health",
            lambda: admin.call("GET", "/health/liveliness", "check liveliness"),
            lambda _: "live",
        )
        loaded = self.step(
            "list models",
            lambda: load_catalog(admin),
            _catalog_detail,
        )
        catalog = loaded.targets
        keys = [target.key for target in catalog]
        tier = self.step(
            f"tier {names.tier_key}",
            lambda: sync_tier(admin, names, keys),
            lambda result: (
                "already allows every model"
                if result.activated_version_id is None
                else f"activated a version: +{len(result.added)} -{len(result.removed)} models"
            ),
        )
        scope, _ = self.step(
            "organization",
            lambda: provisioning.ensure_organization(admin, names, tier.tier_id),
            lambda result: f"{result[0].organization_id} {'created' if result[1] else 'reused'}",
        )
        self.step(
            "organization model access",
            lambda: provisioning.grant_organization_access(admin, scope.organization_id, keys),
            lambda detail: detail,
        )
        self.step(
            "team",
            lambda: provisioning.ensure_team(admin, names, scope),
            lambda created: f"{scope.team_id} {'created' if created else 'reused'}",
        )
        user = self.step(
            f"test user {names.user_email}",
            lambda: provisioning.ensure_user(admin, names, scope),
            lambda _: "ready",
        )
        self.step(
            "remove old test keys",
            lambda: provisioning.delete_suite_keys(admin, names, scope.team_id),
            lambda count: f"{count} removed",
        )
        key = self.step(
            "API key created by test user",
            lambda: provisioning.create_key_as_user(
                self.config.base_url,
                user,
                names,
                scope,
                timeout=self.config.request_timeout,
                transport=self.transport,
            ),
            lambda _: "created",
        )
        return self._select_targets(catalog), key

    def _select_targets(self, catalog: list[CallableTarget]) -> list[CallableTarget]:
        if not self.config.models:
            return catalog
        known = {target.key for target in catalog}
        for missing in sorted(self.config.models - known):
            self.recorder.add(CheckResult("model", missing, Status.FAIL, "not on the gateway"))
        return [target for target in catalog if target.key in self.config.models]

    def _wait_for_access(self, caller: GatewayClient, targets: list[CallableTarget]) -> None:
        """Wait while tier and grant changes reach every replica, then report the gaps."""

        expected = {target.key for target in targets if not target.creator_scoped}
        deadline = self.clock() + ACCESS_WAIT_SECONDS
        missing: set[str] = expected
        while True:
            response = caller.request("GET", "/v1/models")
            if response.status_code == 200:
                visible = {str(item.get("id")) for item in response.json().get("data") or []}
                missing = expected - visible
                if not missing:
                    break
            if self.clock() >= deadline:
                break
            self.wait(2.0)
        if response.status_code != 200:
            detail = f"/v1/models answered HTTP {response.status_code}"
            self.recorder.add(CheckResult("setup", "key sees models", Status.FAIL, detail))
        elif missing:
            detail = "missing: " + ", ".join(sorted(missing))
            self.recorder.add(CheckResult("setup", "key sees models", Status.FAIL, detail))
        else:
            detail = f"all {len(expected)} listed in /v1/models"
            self.recorder.add(CheckResult("setup", "key sees models", Status.PASS, detail))

    def _probe_all(self, caller: GatewayClient, targets: list[CallableTarget]) -> set[str]:
        with ThreadPoolExecutor(max_workers=self.config.parallel) as pool:
            batches = list(
                pool.map(lambda target: probe_target(caller, target, self.config), targets)
            )
        passed: set[str] = set()
        for target, results in zip(targets, batches, strict=True):
            for result in results:
                self.recorder.add(result)
            if results and all(item.status in (Status.PASS, Status.WARN) for item in results):
                passed.add(target.key)
        return passed

    def _batch_target(
        self, targets: list[CallableTarget], passed: set[str], mode: str
    ) -> CallableTarget | None:
        if mode == "chat" and self.config.batch_model:
            return CallableTarget(self.config.batch_model, "model", "chat")
        candidates = [
            target for target in targets if target_mode(target) == mode and target.key in passed
        ]
        # Prefer a plain model over a route group: a batch failure is then easier to place.
        candidates.sort(key=lambda target: (target.target_type != "model", target.key))
        return candidates[0] if candidates else None

    def _run_batches(
        self, caller: GatewayClient, targets: list[CallableTarget], passed: set[str]
    ) -> None:
        for mode in ENDPOINT_BY_MODE:
            if mode in self.config.skip_modes:
                continue
            target = self._batch_target(targets, passed, mode)
            if target is None:
                has_mode = any(target_mode(item) == mode for item in targets)
                status = Status.FAIL if has_mode and mode == "chat" else Status.SKIP
                detail = "no working model" if has_mode else "no model of this mode"
                self.recorder.add(CheckResult("batch", f"{mode} batch", status, detail, mode))
                continue
            self.recorder.add(
                run_batch(caller, target.key, mode, self.config, clock=self.clock, wait=self.wait)
            )

    def _delete_key(self, admin: GatewayClient, key: provisioning.TestKey) -> None:
        try:
            provisioning.delete_key(admin, key.token)
        except CALL_ERRORS as exc:
            self.recorder.add(CheckResult("cleanup", "test key", Status.FAIL, describe_error(exc)))
        else:
            self.recorder.add(CheckResult("cleanup", "test key", Status.PASS, "deleted"))


def _catalog_detail(catalog: Catalog) -> str:
    detail = f"{len(catalog.targets)} models and route groups from {catalog.source}"
    scoped = sum(1 for target in catalog.targets if target.creator_scoped)
    if scoped:
        detail += f"; {scoped} created by an account and not shared with the test organization"
    return detail


def cleanup_only(run: SmokeRun) -> None:
    with run.client(run.config.master_key) as admin:
        for result in cleanup_resources(admin, run.names, clock=run.clock, wait=run.wait):
            run.recorder.add(result)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = monotonic,
    wait: Callable[[float], None] = sleep,
    prompt: Callable[[str], str] | None = None,
) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if prompt is None and env is None and sys.stdin.isatty():
        prompt = getpass
    try:
        master_key = read_master_key(os.environ if env is None else env, prompt)
        config = build_config(args, master_key)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    recorder = Recorder(out)
    recorder.protect(master_key)
    run = SmokeRun(config, recorder, transport=transport, clock=clock, wait=wait)
    print(f"DeltaLLM live smoke suite: {config.base_url}", file=out)
    if args.cleanup_only:
        cleanup_only(run)
    else:
        run.run()
    recorder.write_summary()
    if args.json_output:
        recorder.write_json(args.json_output, base_url=config.base_url)
    return 1 if recorder.failed() else 0
