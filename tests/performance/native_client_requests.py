"""Check ordinary clients before the native qualification arrival windows."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import httpx

from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.lifecycle_cluster import LOAD_KEY
from tests.performance.native_qualification_economics import accounting_snapshot, wait_native_drain
from tests.performance.run_gateway_concurrency import valid_completion
from src.request_identity import valid_request_id


async def verify_native_clients(
    api_ports: list[int], worker_ports: list[int], output: Path
) -> None:
    async with local_database() as db:
        before = await accounting_snapshot(db)
    cases = [(value, False) for value in [None, "", "bad id", "x" * 257, "client-check-123"]]
    cases.append((None, True))
    rows, functional_checks = [], []
    body = {
        "model": "concurrency-fixture",
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "stream": False,
        "metadata": {"cache": False},
    }
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for index, port in enumerate(api_ports):
            for supplied, streaming in cases:
                headers = {"Authorization": f"Bearer {LOAD_KEY}"}
                if supplied is not None:
                    headers["x-request-id"] = supplied
                payload = dict(body)
                if streaming:
                    payload.update(
                        stream=True,
                        stream_options={"include_usage": True},
                        messages=[{"role": "user", "content": "Native complete stream fixture."}],
                    )
                response = await client.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions", headers=headers, json=payload
                )
                resolved = response.headers.get("x-request-id", "")
                try:
                    completion_valid = (
                        valid_stream(response.text)
                        if streaming
                        else valid_completion(response.json())
                    )
                except (ValueError, TypeError, KeyError, AttributeError):
                    completion_valid = False
                rows.append(
                    {
                        "api_process": index,
                        "input_id_kind": "omitted"
                        if supplied is None
                        else "valid"
                        if supplied == "client-check-123"
                        else "invalid",
                        "status": response.status_code,
                        "streaming": streaming,
                        "request_id": resolved,
                        "passed": response.status_code == 200
                        and completion_valid
                        and valid_request_id(resolved)
                        and (
                            resolved == supplied
                            if supplied == "client-check-123"
                            else resolved != supplied
                        ),
                    }
                )
            base = f"http://127.0.0.1:{port}"
            authorized = {"Authorization": f"Bearer {LOAD_KEY}"}
            response = await client.get(base + "/v1/models", headers=authorized)
            try:
                visible = {item["id"] for item in response.json()["data"]}
            except (ValueError, KeyError, TypeError):
                visible = set()
            functional_checks.append(
                {
                    "api_process": index,
                    "check": "model_visibility",
                    "passed": response.status_code == 200 and visible == {"concurrency-fixture"},
                }
            )
            response = await client.post(base + "/v1/chat/completions", json=body)
            functional_checks.append(
                {
                    "api_process": index,
                    "check": "authentication_required",
                    "passed": response.status_code == 401,
                }
            )
            response = await client.post(
                base + "/v1/chat/completions",
                headers=authorized,
                json={**body, "messages": "invalid"},
            )
            functional_checks.append(
                {
                    "api_process": index,
                    "check": "invalid_input_rejected",
                    "passed": response.status_code == 422,
                }
            )
            response = await client.get(base + "/ui")
            functional_checks.append(
                {
                    "api_process": index,
                    "check": "packaged_ui",
                    "passed": response.status_code == 200
                    and "text/html" in response.headers.get("content-type", "")
                    and "<script" in response.text,
                }
            )
        async with local_database() as db:
            drain = await wait_native_drain(db)
            after = await accounting_snapshot(db)
        readiness = []
        for port in api_ports + worker_ports:
            response = await client.get(f"http://127.0.0.1:{port}/health/readiness")
            readiness.append(response.status_code)
    passed = (
        all(row["passed"] for row in rows)
        and all(check["passed"] for check in functional_checks)
        and drain["passed"]
        and int(after["facts"]) - int(before["facts"]) == len(rows)
        and Decimal(str(after["fact_charge"])) - Decimal(str(before["fact_charge"]))
        == Decimal(len(rows)) * Decimal("0.000007")
        and all(status == 200 for status in readiness)
    )
    result = {
        "passed": passed,
        "requests": rows,
        "functional_checks": functional_checks,
        "before": before,
        "after": after,
        "accounting_drain": drain,
        "readiness_statuses": readiness,
    }
    (output / "native-client-requests.json").write_text(json.dumps(result, indent=2) + "\n")
    if not passed:
        raise RuntimeError("Ordinary native client requests failed readiness or accounting")


def valid_stream(payload: str) -> bool:
    lines = [
        line.removeprefix("data: ") for line in payload.splitlines() if line.startswith("data: ")
    ]
    if not lines or lines[-1] != "[DONE]":
        return False
    chunks = [json.loads(line) for line in lines[:-1]]
    content = "".join(
        choice.get("delta", {}).get("content", "")
        for chunk in chunks
        for choice in chunk.get("choices", [])
    )
    usage = next((chunk["usage"] for chunk in reversed(chunks) if chunk.get("usage")), {})
    return (
        content == "OK" and usage.get("prompt_tokens") == 5 and usage.get("completion_tokens") == 1
    )
