"""One small batch job end to end: upload, create, poll, read the output file."""

from __future__ import annotations

from collections.abc import Callable
import json
from time import monotonic, perf_counter, sleep
from typing import Any

import httpx

from scripts.live_smoke.config import SmokeConfig
from scripts.live_smoke.gateway_client import (
    CALL_ERRORS,
    GatewayClient,
    GatewayError,
    describe_error,
)
from scripts.live_smoke.results import CheckResult, Status

BATCH_ITEMS = 2
TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})
ENDPOINT_BY_MODE = {"chat": "/v1/chat/completions", "embedding": "/v1/embeddings"}


def batch_lines(model: str, mode: str, config: SmokeConfig) -> list[dict[str, object]]:
    endpoint = ENDPOINT_BY_MODE[mode]
    lines = []
    for index in range(BATCH_ITEMS):
        if mode == "chat":
            body: dict[str, object] = {
                "model": model,
                "messages": [{"role": "user", "content": f"Reply with the number {index}."}],
                "max_tokens": config.max_tokens,
            }
        else:
            body = {"model": model, "input": f"live smoke batch item {index}"}
        lines.append(
            {
                "custom_id": f"live-smoke-{index}",
                "method": "POST",
                "url": endpoint,
                "body": body,
            }
        )
    return lines


def _check_output(client: GatewayClient, file_id: str) -> None:
    response = client.call("GET", f"/v1/files/{file_id}/content", "read batch output")
    rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    if len(rows) != BATCH_ITEMS:
        raise GatewayError("read batch output", 200, f"{len(rows)} of {BATCH_ITEMS} rows")
    for row in rows:
        if not isinstance(row, dict):
            raise GatewayError("read batch output", 200, "an output row is not a JSON object")
        status_code = (row.get("response") or {}).get("status_code")
        if status_code != 200 or row.get("error"):
            raise GatewayError(
                "read batch output",
                200,
                f"{row.get('custom_id')}: status {status_code}, error {row.get('error')}",
            )


def _first_error(client: GatewayClient, file_id: object) -> str:
    if not file_id:
        return ""
    response = client.request("GET", f"/v1/files/{file_id}/content")
    if response.status_code != 200:
        return ""
    for line in response.text.splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                return ""
            error = (row.get("error") if isinstance(row, dict) else None) or {}
            message = error.get("message") if isinstance(error, dict) else error
            return f", first error: {str(message)[:200]}"
    return ""


def _cancel(client: GatewayClient, batch_id: str) -> str:
    """Cancel an unfinished batch so it does no more work. Best effort."""

    try:
        response = client.request("POST", f"/v1/batches/{batch_id}/cancel")
    except httpx.HTTPError:
        return "; cancel failed"
    return "; cancelled" if response.status_code == 200 else "; cancel failed"


def _drive_batch(
    client: GatewayClient,
    model: str,
    mode: str,
    config: SmokeConfig,
    state: dict[str, Any],
    *,
    clock: Callable[[], float],
    wait: Callable[[float], None],
) -> None:
    """Run the batch. ``state`` keeps the last batch body, so a caller can cancel it."""

    content = "".join(json.dumps(line) + "\n" for line in batch_lines(model, mode, config))
    uploaded = client.json(
        "POST",
        "/v1/files",
        "upload batch input",
        data={"purpose": "batch"},
        files={"file": ("live-smoke.jsonl", content.encode(), "application/jsonl")},
    )
    batch = client.json(
        "POST",
        "/v1/batches",
        "create batch",
        json={
            "input_file_id": uploaded["id"],
            "endpoint": ENDPOINT_BY_MODE[mode],
            "completion_window": "24h",
            "metadata": {"source": "live-smoke"},
        },
    )
    state.update(batch)
    deadline = clock() + config.batch_timeout
    while batch.get("status") not in TERMINAL_STATUSES:
        if clock() >= deadline:
            raise GatewayError(
                "wait for batch",
                0,
                f"still {batch.get('status')} after {config.batch_timeout:.0f} s",
            )
        wait(config.batch_poll_interval)
        batch = client.json("GET", f"/v1/batches/{state['id']}", "read batch")
        state.update(batch)
    counts = batch.get("request_counts") or {}
    if batch.get("status") != "completed" or counts.get("failed"):
        reason = _first_error(client, batch.get("error_file_id"))
        raise GatewayError(
            "finish batch", 0, f"status {batch.get('status')}, counts {counts}{reason}"
        )
    if not batch.get("output_file_id"):
        raise GatewayError("finish batch", 0, "completed batch has no output file")
    _check_output(client, str(batch["output_file_id"]))


def run_batch(
    client: GatewayClient,
    model: str,
    mode: str,
    config: SmokeConfig,
    *,
    clock: Callable[[], float] = monotonic,
    wait: Callable[[float], None] = sleep,
) -> CheckResult:
    started = perf_counter()
    name = f"{mode} batch on {model}"
    state: dict[str, Any] = {}
    try:
        _drive_batch(client, model, mode, config, state, clock=clock, wait=wait)
    except CALL_ERRORS as exc:
        detail = describe_error(exc)
        batch_id = state.get("id")
        if batch_id:
            if state.get("status") not in TERMINAL_STATUSES:
                detail += _cancel(client, str(batch_id))
            detail += f" (batch {batch_id})"
        return CheckResult("batch", name, Status.FAIL, detail, mode, _ms(started))
    return CheckResult(
        "batch", name, Status.PASS, f"{BATCH_ITEMS} items completed", mode, _ms(started)
    )


def _ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
