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
    cases, rows = [None, "", "bad id", "x" * 257, "client-check-123"], []
    body = {
        "model": "concurrency-fixture",
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "stream": False,
        "metadata": {"cache": False},
    }
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for index, port in enumerate(api_ports):
            for supplied in cases:
                headers = {"Authorization": f"Bearer {LOAD_KEY}"}
                if supplied is not None:
                    headers["x-request-id"] = supplied
                response = await client.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions", headers=headers, json=body
                )
                resolved = response.headers.get("x-request-id", "")
                try:
                    completion_valid = valid_completion(response.json())
                except (ValueError, TypeError):
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
        async with local_database() as db:
            drain = await wait_native_drain(db)
            after = await accounting_snapshot(db)
        readiness = []
        for port in api_ports + worker_ports:
            response = await client.get(f"http://127.0.0.1:{port}/health/readiness")
            readiness.append(response.status_code)
    passed = (
        all(row["passed"] for row in rows)
        and drain["passed"]
        and int(after["facts"]) - int(before["facts"]) == len(rows)
        and Decimal(str(after["fact_charge"])) - Decimal(str(before["fact_charge"]))
        == Decimal(len(rows)) * Decimal("0.000007")
        and all(status == 200 for status in readiness)
    )
    result = {
        "passed": passed,
        "requests": rows,
        "before": before,
        "after": after,
        "accounting_drain": drain,
        "readiness_statuses": readiness,
    }
    (output / "native-client-requests.json").write_text(json.dumps(result, indent=2) + "\n")
    if not passed:
        raise RuntimeError("Ordinary native client requests failed readiness or accounting")
