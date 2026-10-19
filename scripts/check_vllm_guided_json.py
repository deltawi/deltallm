"""Check schema enforcement through a gateway connected to a real vLLM server."""

from __future__ import annotations

import argparse
import json
import os
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: Literal["SCHEMA-432"]
    total: Literal[432]


def check_guided_json(
    client: httpx.Client, *, gateway_url: str, vllm_url: str, model: str, api_key: str
) -> str:
    version_response = client.get(f"{vllm_url.rstrip('/')}/version")
    if version_response.status_code != 200:
        raise RuntimeError("Cannot read the test vLLM version")
    version = version_response.json().get("version")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise RuntimeError("The test vLLM server returned an invalid version")
    schema = Invoice.model_json_schema()
    constraints = (
        {"guided_json": schema},
        {"guided_json": json.dumps(schema)},
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "invoice", "schema": schema},
            }
        },
    )
    for constraint in constraints:
        response = client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Cache-Control": "no-cache"},
            json={
                "model": model,
                # The prompt must conflict with the schema to detect ignored constraints.
                "messages": [{"role": "user", "content": "Reply with the plain text word hello."}],
                "temperature": 0,
                "max_tokens": 256,
                **constraint,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Schema request failed with HTTP {response.status_code}")
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise RuntimeError("Schema generation did not finish")
        Invoice.model_validate_json(choice["message"]["content"])
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--vllm-url", required=True, help="Server root URL, without /v1")
    parser.add_argument("--model", required=True, help="DeltaLLM model alias for this vLLM server")
    args = parser.parse_args()
    api_key = os.environ.get("DELTALLM_TEST_API_KEY")
    if not api_key:
        parser.error("Set DELTALLM_TEST_API_KEY to a test gateway key")
    try:
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
            version = check_guided_json(
                client,
                gateway_url=args.gateway_url,
                vllm_url=args.vllm_url,
                model=args.model,
                api_key=api_key,
            )
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:
        # Do not print server bodies, generated content, URLs, or credentials.
        raise SystemExit(f"vLLM schema check failed ({type(exc).__name__})") from None
    print(f"PASS: vLLM {version}; guided_json object/string and response_format obey the schema")


if __name__ == "__main__":
    main()
