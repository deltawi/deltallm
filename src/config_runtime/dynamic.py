from __future__ import annotations

import asyncio
import json
import logging
import time
from copy import deepcopy
from typing import Any, Awaitable, Callable

from src.config import AppConfig
from src.config_runtime.loader import build_app_config, deep_merge
from src.config_runtime.secrets import SecretResolver

logger = logging.getLogger(__name__)

ConfigSubscriber = Callable[[AppConfig, dict[str, list[str]]], Awaitable[None] | None]


class DynamicConfigManager:
    """Merges file + DB config and propagates changes via Redis pub/sub."""

    def __init__(
        self,
        db_client: Any | None,
        redis_client: Any | None,
        file_config: dict[str, Any],
        secret_resolver: SecretResolver | None = None,
        channel_name: str = "config_updates",
    ) -> None:
        self.db = db_client
        self.redis = redis_client
        self.file_config = deepcopy(file_config)
        self.secret_resolver = secret_resolver or SecretResolver()
        self.channel_name = channel_name

        self._db_config: dict[str, Any] = {}
        self._config = build_app_config(self.file_config, {}, self.secret_resolver)
        self._subscribers: list[ConfigSubscriber] = []
        self._pubsub_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def initialize(self) -> None:
        self._db_config = await self._load_from_db()
        self._config = build_app_config(self.file_config, self._db_config, self.secret_resolver)

        if self.redis is not None:
            self._pubsub_task = asyncio.create_task(self._listen_for_changes())

    async def close(self) -> None:
        self._stopping = True
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None

    def subscribe(self, callback: ConfigSubscriber) -> None:
        self._subscribers.append(callback)

    def get_config(self) -> dict[str, Any]:
        return self._config.model_dump(mode="python", exclude_none=True)

    def get_app_config(self) -> AppConfig:
        return self._config.model_copy(deep=True)

    async def update_config(self, config_update: dict[str, Any], updated_by: str) -> None:
        self._db_config = deep_merge(self._db_config, config_update)
        await self._store_db_config(self._db_config, updated_by=updated_by)
        await self._reload_config()
        await self._publish_reload_event(event_type="config_updated")

    async def _listen_for_changes(self) -> None:
        if self.redis is None:
            return

        pubsub = self.redis.pubsub()
        try:
            await pubsub.subscribe(self.channel_name)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue

                payload = message.get("data")
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8")

                data: dict[str, Any] = {}
                if isinstance(payload, str):
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = {}

                if data.get("type") in {"config_updated", "model_updated"}:
                    await self._reload_config()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._stopping:
                logger.error("config pub/sub error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(self.channel_name)
            except Exception:
                pass
            await pubsub.close()

    async def _reload_config(self) -> None:
        old_config = self._config.model_dump(mode="python", exclude_none=True)
        self._db_config = await self._load_from_db()
        new_app_config = build_app_config(self.file_config, self._db_config, self.secret_resolver)
        new_config = new_app_config.model_dump(mode="python", exclude_none=True)

        changes = self._detect_changes(old_config, new_config)
        self._config = new_app_config

        if not any(changes.values()):
            return

        for callback in list(self._subscribers):
            try:
                result = callback(self._config, changes)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error("config subscriber callback failed: %s", exc)

    async def _load_from_db(self) -> dict[str, Any]:
        if self.db is None:
            return deepcopy(self._db_config)

        try:
            rows = await self.db.query_raw(
                """
                SELECT config_value
                FROM litellm_config
                WHERE config_name = $1
                LIMIT 1
                """,
                "proxy_config",
            )
        except Exception as exc:
            logger.debug("failed loading config from db: %s", exc)
            return deepcopy(self._db_config)

        if not rows:
            return {}

        value = rows[0].get("config_value")
        if value is None:
            return {}

        if isinstance(value, str):
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                return {}
        elif isinstance(value, dict):
            payload = value
        else:
            return {}

        return payload if isinstance(payload, dict) else {}

    async def _store_db_config(self, config: dict[str, Any], updated_by: str) -> None:
        if self.db is None:
            return

        payload = json.dumps(config)
        try:
            await self.db.execute_raw(
                """
                INSERT INTO litellm_config (config_name, config_value, updated_by, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (config_name) DO UPDATE
                SET config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                "proxy_config",
                payload,
                updated_by,
            )
        except Exception as exc:
            logger.warning("failed to persist dynamic config: %s", exc)

    async def _publish_reload_event(self, event_type: str) -> None:
        if self.redis is None:
            return

        try:
            await self.redis.publish(
                self.channel_name,
                json.dumps(
                    {
                        "type": event_type,
                        "timestamp": time.time(),
                    }
                ),
            )
        except Exception as exc:
            logger.warning("failed publishing config update event: %s", exc)

    @staticmethod
    def _detect_changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
        old_keys = set(old.keys())
        new_keys = set(new.keys())

        changes = {
            "added": sorted(new_keys - old_keys),
            "removed": sorted(old_keys - new_keys),
            "modified": [],
        }

        for key in sorted(old_keys & new_keys):
            if old[key] != new[key]:
                changes["modified"].append(key)

        return changes
