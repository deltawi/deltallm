from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from src.callbacks.base import CustomLogger

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment,misc]


class S3Callback(CustomLogger):
    def __init__(
        self,
        bucket: str | None = None,
        region: str = "us-east-1",
        prefix: str = "litellm-logs/",
        compression: str | None = None,
    ) -> None:
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 package required. Install with: pip install boto3")
        self.bucket = bucket or os.getenv("LITELLM_S3_BUCKET")
        self.region = region
        self.prefix = prefix
        self.compression = compression
        self._s3 = None

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3", region_name=self.region)
        return self._s3

    def _generate_key(self, kwargs: dict[str, Any]) -> str:
        now = datetime.now(tz=UTC)
        request_id = kwargs.get("litellm_call_id") or "unknown"
        key = (
            f"{self.prefix}"
            f"year={now.year}/"
            f"month={now.month:02d}/"
            f"day={now.day:02d}/"
            f"{request_id}.json"
        )
        if self.compression == "gzip":
            key += ".gz"
        return key

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await asyncio.to_thread(self._upload_log, kwargs, response_obj, start_time, end_time, None)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await asyncio.to_thread(self._upload_log, kwargs, None, start_time, end_time, exception)

    def _upload_log(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
        exception: Exception | None,
    ) -> None:
        if not self.bucket:
            logger.warning("s3 callback missing bucket")
            return

        entry: dict[str, Any] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "request_id": kwargs.get("litellm_call_id"),
            "call_type": kwargs.get("call_type"),
            "model": kwargs.get("model"),
            "api_provider": kwargs.get("api_provider"),
            "user": kwargs.get("user"),
            "team_id": kwargs.get("team_id"),
            "api_key": kwargs.get("api_key"),
            "usage": kwargs.get("usage"),
            "response_cost": kwargs.get("response_cost"),
            "cache_hit": kwargs.get("cache_hit"),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "latency_ms": max(0.0, (end_time - start_time).total_seconds() * 1000),
            "metadata": kwargs.get("metadata"),
            "tags": kwargs.get("tags", []),
        }

        if exception is not None:
            entry["error"] = {
                "type": exception.__class__.__name__,
                "message": str(exception),
            }

        if kwargs.get("redacted"):
            entry["redacted"] = True
        else:
            entry["messages"] = kwargs.get("messages")
            entry["response"] = response_obj

        body = json.dumps(entry, default=str).encode("utf-8")
        if self.compression == "gzip":
            body = gzip.compress(body)

        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self._generate_key(kwargs),
                Body=body,
                ContentType="application/json",
                ContentEncoding="gzip" if self.compression == "gzip" else None,
                Metadata={
                    "model": str(kwargs.get("model") or "unknown"),
                    "user": str(kwargs.get("user") or "unknown"),
                    "team": str(kwargs.get("team_id") or "unknown"),
                },
            )
        except ClientError as exc:
            logger.warning("s3 upload failed: %s", exc)
