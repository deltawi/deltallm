from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

from src.models.errors import InvalidRequestError
from src.providers.resolution import resolve_provider


def _serialize_json_body(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canonical_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    encoded = []
    for key, value in pairs:
        encoded.append((quote(key, safe="-_.~"), quote(value, safe="-_.~")))
    encoded.sort()
    return "&".join(f"{k}={v}" for k, v in encoded)


def _canonical_uri(path: str) -> str:
    return quote(path or "/", safe="/-_.~")


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = hmac.new(f"AWS4{secret}".encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def apply_request_signing(
    *,
    params: dict[str, Any],
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any],
) -> tuple[dict[str, str], bytes | None]:
    provider = resolve_provider(params)
    if provider != "bedrock":
        return headers, None

    access_key = str(params.get("aws_access_key_id") or os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = str(params.get("aws_secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    session_token = str(params.get("aws_session_token") or os.getenv("AWS_SESSION_TOKEN") or "").strip() or None
    region = str(params.get("region") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip()
    if not access_key or not secret_key:
        raise InvalidRequestError(message="Bedrock provider requires AWS credentials")

    body_bytes = _serialize_json_body(json_body)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    now = datetime.now(tz=UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    service = "bedrock"
    parsed = urlparse(url)

    signed_headers_map = {
        "content-type": headers.get("Content-Type", "application/json"),
        "host": parsed.netloc,
        "x-amz-content-sha256": body_hash,
        "x-amz-date": amz_date,
    }
    if session_token:
        signed_headers_map["x-amz-security-token"] = session_token

    canonical_headers = "".join(f"{key}:{' '.join(str(value).strip().split())}\n" for key, value in sorted(signed_headers_map.items()))
    signed_headers = ";".join(sorted(signed_headers_map.keys()))
    canonical_request = "\n".join(
        [
            method.upper(),
            _canonical_uri(parsed.path),
            _canonical_query(parsed.query),
            canonical_headers,
            signed_headers,
            body_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    final_headers = dict(headers)
    final_headers["Content-Type"] = signed_headers_map["content-type"]
    final_headers["Host"] = signed_headers_map["host"]
    final_headers["X-Amz-Content-Sha256"] = body_hash
    final_headers["X-Amz-Date"] = amz_date
    final_headers["Authorization"] = authorization
    if session_token:
        final_headers["X-Amz-Security-Token"] = session_token

    return final_headers, body_bytes
