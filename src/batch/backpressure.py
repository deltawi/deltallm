from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "batch:backpressure:model_group:"
_REDIS_TTL_BUFFER_SECONDS = 5
_WARNING_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class BatchModelGroupDeferral:
    model_group: str
    reason: str
    until_epoch_seconds: int

    def remaining_seconds(self, *, now_epoch_seconds: int | None = None) -> int:
        now = int(time.time()) if now_epoch_seconds is None else int(now_epoch_seconds)
        return max(0, int(self.until_epoch_seconds) - now)


class BatchModelGroupDeferred(RuntimeError):
    def __init__(self, *, model_group: str, reason: str, retry_after_seconds: int) -> None:
        self.model_group = model_group
        self.reason = reason
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(
            "batch model group is temporarily deferred "
            f"model_group={model_group} reason={reason} retry_after_seconds={self.retry_after_seconds}"
        )


class BatchBackpressureCoordinator:
    def __init__(
        self,
        *,
        redis_client: Any | None,
        enabled: bool,
        min_delay_seconds: int,
        max_delay_seconds: int,
    ) -> None:
        self.redis = redis_client
        self.enabled = bool(enabled)
        self.min_delay_seconds = max(1, int(min_delay_seconds))
        self.max_delay_seconds = max(self.min_delay_seconds, int(max_delay_seconds))
        self._local_deferrals: dict[str, BatchModelGroupDeferral] = {}
        self._last_warning_at = 0

    async def defer_model_group(
        self,
        model_group: str,
        *,
        delay_seconds: int,
        reason: str,
    ) -> BatchModelGroupDeferral | None:
        if not self.enabled:
            return None
        normalized_group = str(model_group or "").strip()
        if not normalized_group:
            return None

        delay = self._clamp_delay(delay_seconds)
        now = int(time.time())
        until = now + delay
        deferral = BatchModelGroupDeferral(
            model_group=normalized_group,
            reason=self._normalize_reason(reason),
            until_epoch_seconds=until,
        )
        key = self._key_for_model_group(normalized_group)
        payload = json.dumps(
            {
                "reason": deferral.reason,
                "until": deferral.until_epoch_seconds,
                "last_seen": now,
            },
            separators=(",", ":"),
        )

        if self.redis is None:
            self._store_local(key, deferral)
            return deferral

        try:
            await self.redis.setex(key, delay + _REDIS_TTL_BUFFER_SECONDS, payload)
        except Exception as exc:
            self._store_local(key, deferral)
            self._log_backend_warning("batch model group backpressure redis write failed: %s", exc)
        else:
            self._store_local(key, deferral)
        return deferral

    async def is_model_group_deferred(self, model_group: str) -> bool:
        return await self.get_model_group_deferral(model_group) is not None

    async def get_model_group_deferral(self, model_group: str) -> BatchModelGroupDeferral | None:
        if not self.enabled:
            return None
        normalized_group = str(model_group or "").strip()
        if not normalized_group:
            return None

        key = self._key_for_model_group(normalized_group)
        if self.redis is not None:
            try:
                raw = await self.redis.get(key)
            except Exception as exc:
                self._log_backend_warning("batch model group backpressure redis read failed: %s", exc)
                return self._get_local(key)
            deferral = self._decode_deferral(raw=raw, model_group=normalized_group)
            if deferral is not None and deferral.remaining_seconds() > 0:
                return deferral
            return self._get_local(key)

        return self._get_local(key)

    def _clamp_delay(self, delay_seconds: int) -> int:
        try:
            requested = int(delay_seconds)
        except (TypeError, ValueError):
            requested = self.min_delay_seconds
        return min(self.max_delay_seconds, max(self.min_delay_seconds, requested))

    @staticmethod
    def _normalize_reason(reason: str) -> str:
        return str(reason or "unknown").strip() or "unknown"

    @staticmethod
    def _key_for_model_group(model_group: str) -> str:
        digest = hashlib.sha256(model_group.encode("utf-8")).hexdigest()
        return f"{_REDIS_KEY_PREFIX}{digest}"

    def _store_local(self, key: str, deferral: BatchModelGroupDeferral) -> None:
        self._local_deferrals[key] = deferral

    def _get_local(self, key: str) -> BatchModelGroupDeferral | None:
        deferral = self._local_deferrals.get(key)
        if deferral is None:
            return None
        if deferral.remaining_seconds() <= 0:
            self._local_deferrals.pop(key, None)
            return None
        return deferral

    @staticmethod
    def _decode_deferral(raw: Any, *, model_group: str) -> BatchModelGroupDeferral | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(str(raw))
            reason = str(payload.get("reason") or "unknown")
            until = int(payload.get("until") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return BatchModelGroupDeferral(
            model_group=model_group,
            reason=reason,
            until_epoch_seconds=until,
        )

    def _log_backend_warning(self, message: str, exc: Exception) -> None:
        now = int(time.time())
        if now - self._last_warning_at < _WARNING_INTERVAL_SECONDS:
            logger.debug(message, exc)
            return
        self._last_warning_at = now
        logger.warning(message, exc)
