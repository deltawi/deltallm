from __future__ import annotations

import asyncio
import json
import logging
import time
from copy import deepcopy
from typing import Any, Awaitable, Callable

from src.batch.scheduling import resolve_scheduler_modes_from_settings, scheduler_rollback_events
from src.config import AppConfig
from src.config_runtime.loader import build_app_config, deep_merge
from src.config_runtime.secrets import SecretResolver
from src.metrics import increment_batch_scheduler_rollback, increment_config_reload

logger = logging.getLogger(__name__)

ConfigSubscriber = Callable[[AppConfig, dict[str, list[str]]], Awaitable[None] | None]
ConfigTransactionMutation = Callable[[Any], Awaitable[None]]
_CONFIG_ROW_NAME = "proxy_config"
_CONFIG_ADVISORY_LOCK_NAME = "deltallm_config:proxy_config"
_FIELD_SET_SENSITIVE_GENERAL_SETTINGS = frozenset(
    {
        "embeddings_batch_scheduler_mode",
        "embeddings_batch_scheduler_shadow_mode",
    }
)


class DynamicConfigPersistenceError(RuntimeError):
    """Raised when a dynamic config update cannot be durably persisted."""


class DynamicConfigValidationError(ValueError):
    """Raised when a dynamic config update cannot be validated."""


class DynamicConfigLoadError(RuntimeError):
    """Raised when a dynamic config reload cannot read the durable source."""


class DynamicConfigRestartRequiredError(RuntimeError):
    """Raised when an update changes fields that are bound during process startup."""


class DynamicConfigPostCommitApplyError(RuntimeError):
    """Raised when durable config committed but the local runtime rejected it."""

    def __init__(self, committed_app_config: AppConfig) -> None:
        super().__init__("dynamic config committed but failed to apply locally")
        self.committed_app_config = committed_app_config.model_copy(deep=True)


_STARTUP_ONLY_GENERAL_SETTINGS = frozenset(
    {
        "database_url",
        "db_pool_size",
        "db_pool_timeout",
        "telemetry_db_pool_size",
        "telemetry_db_pool_timeout_seconds",
        "spend_ingestion_mode",
        "audit_ingestion_mode",
        "prompt_singleflight_max_keys",
        "prompt_singleflight_timeout_seconds",
        "email_enabled",
        "email_worker_enabled",
        "email_worker_poll_interval_seconds",
        "email_worker_max_concurrency",
        "email_worker_batch_size",
        "email_worker_delivery_lease_seconds",
        "email_worker_audit_lease_seconds",
        "email_worker_startup_timeout_seconds",
        "email_worker_shutdown_drain_timeout_seconds",
    }
)


class DynamicConfigManager:
    """Merges file + DB config and propagates changes via Redis pub/sub."""

    def __init__(
        self,
        db_client: Any | None,
        redis_client: Any | None,
        file_config: dict[str, Any],
        secret_resolver: SecretResolver | None = None,
        channel_name: str = "config_updates",
        poll_interval_seconds: float | None = 30.0,
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
        self._poll_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._update_lock = asyncio.Lock()
        self._config_generation = 0
        if poll_interval_seconds is None:
            self._poll_interval_seconds = 0.0
        else:
            self._poll_interval_seconds = max(0.0, float(poll_interval_seconds))

    async def initialize(self) -> None:
        self._db_config = await self._load_from_db(allow_stale_on_error=True)
        self._config = build_app_config(self.file_config, self._db_config, self.secret_resolver)
        self._config_generation = 1

        if self.redis is not None:
            self._pubsub_task = asyncio.create_task(self._listen_for_changes())
        if self.db is not None and self._poll_interval_seconds > 0:
            self._poll_task = asyncio.create_task(self._poll_for_changes())

    async def close(self) -> None:
        self._stopping = True
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def subscribe(self, callback: ConfigSubscriber) -> None:
        self._subscribers.append(callback)

    def get_config(self) -> dict[str, Any]:
        return self._config.model_dump(mode="python", exclude_none=True)

    def get_app_config(self) -> AppConfig:
        return self._config.model_copy(deep=True)

    def get_config_generation(self) -> int:
        return self._config_generation

    async def update_config(
        self,
        config_update: dict[str, Any],
        updated_by: str,
        *,
        transaction_mutation: ConfigTransactionMutation | None = None,
    ) -> None:
        async with self._update_lock:
            previous_db_config = deepcopy(self._db_config)
            next_db_config, next_app_config = await self._persist_merged_config(
                config_update,
                updated_by=updated_by,
                transaction_mutation=transaction_mutation,
            )
            try:
                await self._apply_db_config(next_db_config, app_config=next_app_config)
            except Exception as exc:
                if transaction_mutation is None:
                    await self._restore_db_config_if_current(
                        expected_config=next_db_config,
                        previous_config=previous_db_config,
                        updated_by=f"rollback:{updated_by}",
                    )
                    raise
                increment_config_reload(
                    source="admin_update",
                    result="post_commit_apply_failed",
                )
                logger.warning(
                    "dynamic config committed but local apply failed error_type=%s",
                    type(exc).__name__,
                )
                # PostgreSQL is already authoritative. Wake peers even though this
                # process kept its last-known-good runtime snapshot.
                await self._publish_reload_event(event_type="config_updated")
                raise DynamicConfigPostCommitApplyError(next_app_config) from exc
            await self._publish_reload_event(event_type="config_updated")

    async def _persist_merged_config(
        self,
        config_update: dict[str, Any],
        *,
        updated_by: str,
        transaction_mutation: ConfigTransactionMutation | None = None,
    ) -> tuple[dict[str, Any], AppConfig]:
        if self.db is None:
            if transaction_mutation is not None:
                raise DynamicConfigPersistenceError(
                    "database-backed config mutation requires a database"
                )
            next_db_config = deep_merge(self._db_config, config_update)
            next_app_config = self._build_app_config(next_db_config)
            if self._config_generation > 0:
                self._reject_startup_only_changes(next_app_config)
            return next_db_config, next_app_config

        transaction_factory = getattr(self.db, "tx", None)
        if not callable(transaction_factory):
            # Lightweight test doubles and compatibility clients may not expose
            # transactions. Re-read before merging so they still avoid stale
            # process-local state; production Prisma clients use the locked path.
            current_db_config = await self._load_from_db()
            next_db_config = deep_merge(current_db_config, config_update)
            next_app_config = self._build_app_config(next_db_config)
            if self._config_generation > 0:
                self._reject_startup_only_changes(next_app_config)
            if transaction_mutation is not None:
                await transaction_mutation(self.db)
            await self._store_db_config(next_db_config, updated_by=updated_by)
            return next_db_config, next_app_config

        try:
            async with transaction_factory() as transaction:
                # A transaction-scoped advisory lock also serializes creation of
                # the singleton row, where SELECT ... FOR UPDATE has no row to lock.
                await transaction.query_raw(
                    "SELECT pg_advisory_xact_lock(hashtext(CAST($1 AS TEXT)))::text AS locked",
                    _CONFIG_ADVISORY_LOCK_NAME,
                )
                current_db_config = await self._load_from_db_client(transaction, for_update=True)
                next_db_config = deep_merge(current_db_config, config_update)
                next_app_config = self._build_app_config(next_db_config)
                if self._config_generation > 0:
                    self._reject_startup_only_changes(next_app_config)
                if transaction_mutation is not None:
                    await transaction_mutation(transaction)
                await self._store_db_config(
                    next_db_config,
                    updated_by=updated_by,
                    db_client=transaction,
                )
        except (
            DynamicConfigPersistenceError,
            DynamicConfigRestartRequiredError,
            DynamicConfigValidationError,
        ):
            raise
        except Exception as exc:
            logger.warning("failed to persist dynamic config transaction: %s", exc)
            raise DynamicConfigPersistenceError("failed to persist dynamic config") from exc

        return next_db_config, next_app_config

    async def _restore_db_config_if_current(
        self,
        *,
        expected_config: dict[str, Any],
        previous_config: dict[str, Any],
        updated_by: str,
    ) -> None:
        if self.db is None:
            return

        try:
            transaction_factory = getattr(self.db, "tx", None)
            if not callable(transaction_factory):
                current_config = await self._load_from_db()
                if current_config == expected_config:
                    await self._store_db_config(previous_config, updated_by=updated_by)
                return

            async with transaction_factory() as transaction:
                await transaction.query_raw(
                    "SELECT pg_advisory_xact_lock(hashtext(CAST($1 AS TEXT)))::text AS locked",
                    _CONFIG_ADVISORY_LOCK_NAME,
                )
                current_config = await self._load_from_db_client(transaction, for_update=True)
                if current_config != expected_config:
                    return
                await self._store_db_config(
                    previous_config,
                    updated_by=updated_by,
                    db_client=transaction,
                )
        except Exception:
            logger.exception("failed rolling back rejected dynamic config")

    async def publish_model_updated(self) -> None:
        await self._publish_reload_event(event_type="model_updated")

    async def _listen_for_changes(self) -> None:
        if self.redis is None:
            return

        pubsub = None
        try:
            pubsub = self.redis.pubsub()
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

                event_type = data.get("type")
                if event_type == "config_updated":
                    await self._reload_config_from_source(source="pubsub")
                elif event_type == "model_updated":
                    await self._reload_config_from_source(
                        source="pubsub",
                        forced_modified_keys=("model_list",),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._stopping:
                increment_config_reload(source="pubsub", result="listener_failed")
                logger.error("config pub/sub error: %s", exc)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(self.channel_name)
                except Exception:
                    pass
                await pubsub.close()

    async def _poll_for_changes(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._poll_interval_seconds)
            await self._reload_config_from_source(source="poll")

    async def _reload_config_from_source(
        self,
        *,
        source: str,
        forced_modified_keys: tuple[str, ...] = (),
    ) -> bool:
        try:
            changed = await self._reload_config(forced_modified_keys=forced_modified_keys)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            increment_config_reload(source=source, result="failed")
            logger.warning("dynamic config reload failed source=%s: %s", source, exc)
            return False
        increment_config_reload(source=source, result="applied" if changed else "unchanged")
        return changed

    async def _reload_config(self, *, forced_modified_keys: tuple[str, ...] = ()) -> bool:
        db_config = await self._load_from_db()
        return await self._apply_db_config(db_config, forced_modified_keys=forced_modified_keys)

    async def _apply_db_config(
        self,
        db_config: dict[str, Any],
        *,
        forced_modified_keys: tuple[str, ...] = (),
        app_config: AppConfig | None = None,
    ) -> bool:
        previous_app_config = self._config
        new_app_config = app_config or self._build_app_config(db_config)
        if self._config_generation > 0:
            self._reject_startup_only_changes(new_app_config)

        changes = self._detect_app_config_changes(previous_app_config, new_app_config)
        if forced_modified_keys:
            # Some runtime changes are driven by repository state rather than the
            # persisted config blob. Merge in the synthetic keys so subscribers
            # still refresh the relevant in-memory state on peer pods.
            changes["modified"] = sorted(set(changes["modified"]) | set(forced_modified_keys))
        if not any(changes.values()):
            self._db_config = deepcopy(db_config)
            return False

        try:
            await self._notify_subscribers(new_app_config, changes)
        except Exception:
            rollback_changes = self._detect_app_config_changes(
                new_app_config,
                previous_app_config,
            )
            for callback in list(self._subscribers):
                try:
                    result = callback(previous_app_config, rollback_changes)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("config subscriber rollback failed")
            raise
        self._db_config = deepcopy(db_config)
        self._config = new_app_config
        self._config_generation += 1
        self._record_scheduler_rollbacks(
            previous_config=previous_app_config,
            current_config=new_app_config,
        )
        return True

    def _reject_startup_only_changes(self, next_app_config: AppConfig) -> None:
        current = self._config.general_settings
        candidate = next_app_config.general_settings
        changed = sorted(
            field_name
            for field_name in _STARTUP_ONLY_GENERAL_SETTINGS
            if getattr(current, field_name) != getattr(candidate, field_name)
        )
        if changed:
            fields = ", ".join(f"general_settings.{field_name}" for field_name in changed)
            raise DynamicConfigRestartRequiredError(
                f"startup-only settings require a restart: {fields}"
            )

    def _build_app_config(self, db_config: dict[str, Any]) -> AppConfig:
        try:
            return build_app_config(self.file_config, db_config, self.secret_resolver)
        except Exception as exc:
            raise DynamicConfigValidationError("failed to validate dynamic config") from exc

    @staticmethod
    def _record_scheduler_rollbacks(
        *, previous_config: AppConfig, current_config: AppConfig
    ) -> None:
        previous_modes = resolve_scheduler_modes_from_settings(previous_config.general_settings)
        current_modes = resolve_scheduler_modes_from_settings(current_config.general_settings)
        for event in scheduler_rollback_events(previous=previous_modes, current=current_modes):
            increment_batch_scheduler_rollback(
                from_mode=event.from_mode,
                to_mode=event.to_mode,
                reason=event.reason,
            )

    async def _notify_subscribers(
        self,
        app_config: AppConfig,
        changes: dict[str, list[str]],
    ) -> None:
        for callback in list(self._subscribers):
            result = callback(app_config, changes)
            if asyncio.iscoroutine(result):
                await result

    async def _load_from_db(self, *, allow_stale_on_error: bool = False) -> dict[str, Any]:
        if self.db is None:
            return deepcopy(self._db_config)

        try:
            return await self._load_from_db_client(self.db)
        except Exception as exc:
            if allow_stale_on_error:
                logger.debug("failed loading config from db, using stale dynamic config: %s", exc)
                return deepcopy(self._db_config)
            raise DynamicConfigLoadError("failed loading dynamic config from db") from exc

    @staticmethod
    async def _load_from_db_client(db_client: Any, *, for_update: bool = False) -> dict[str, Any]:
        lock_clause = " FOR UPDATE" if for_update else ""
        rows = await db_client.query_raw(
            f"""
            SELECT config_value
            FROM deltallm_config
            WHERE config_name = $1
            LIMIT 1{lock_clause}
            """,
            _CONFIG_ROW_NAME,
        )
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

    async def _store_db_config(
        self,
        config: dict[str, Any],
        updated_by: str,
        *,
        db_client: Any | None = None,
    ) -> None:
        client = db_client or self.db
        if client is None:
            return

        payload = json.dumps(config)
        try:
            await client.execute_raw(
                """
                INSERT INTO deltallm_config (config_name, config_value, updated_by, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (config_name) DO UPDATE
                SET config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                _CONFIG_ROW_NAME,
                payload,
                updated_by,
            )
        except Exception as exc:
            logger.warning("failed to persist dynamic config: %s", exc)
            raise DynamicConfigPersistenceError("failed to persist dynamic config") from exc

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
    def _detect_app_config_changes(
        old_config: AppConfig,
        new_config: AppConfig,
    ) -> dict[str, list[str]]:
        changes = DynamicConfigManager._detect_changes(
            old_config.model_dump(mode="python", exclude_none=True),
            new_config.model_dump(mode="python", exclude_none=True),
        )
        old_general_fields = set(getattr(old_config.general_settings, "model_fields_set", set()))
        new_general_fields = set(getattr(new_config.general_settings, "model_fields_set", set()))
        if (old_general_fields ^ new_general_fields) & _FIELD_SET_SENSITIVE_GENERAL_SETTINGS:
            changes["modified"] = sorted(set(changes["modified"]) | {"general_settings"})
        return changes

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
