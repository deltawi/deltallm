from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from typing import Any, Callable

import pytest

from src.config import UIBrandingPayload
from src.config_runtime.dynamic import (
    DynamicConfigManager,
    DynamicConfigPersistenceError,
    DynamicConfigPostCommitApplyError,
)
from src.db.ui_branding_assets import UIBrandingAssetRepository
from src.services.ui_branding_assets import UIBrandingAssetService, validate_branding_asset

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xf8\x0f"
    b"\x00\x01\x05\x01\x01'\x18\xe3f\x00\x00\x00\x00IEND\xaeB`\x82"
)
_CUSTOM_CONFIG = {
    "general_settings": {
        "instance_name": "Integration Brand",
        "log_level": "DEBUG",
        "ui_branding": {
            "logo_mark_url": "/ui/api/branding/assets/logo_mark?v=" + "a" * 64,
            "logo_full_url": "/ui/api/branding/assets/logo_full?v=" + "b" * 64,
            "favicon_url": "/ui/api/branding/assets/favicon?v=" + "c" * 64,
            "primary_color": "#112233",
            "secondary_color": "#445566",
            "menu_hover_color": "#778899",
        },
    },
    "router_settings": {"routing_strategy": "simple-shuffle"},
}


@dataclass(frozen=True, slots=True)
class _DatabaseSnapshot:
    config_row: dict[str, object] | None
    asset_rows: list[dict[str, object]]


async def _connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL and the generated prisma client")
        pytest.skip("DATABASE_URL and prisma client are required")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


async def _snapshot_database(db: Any) -> _DatabaseSnapshot:
    config_rows = await db.query_raw(
        """
        SELECT config_value, updated_by, updated_at
        FROM deltallm_config
        WHERE config_name = $1
        """,
        "proxy_config",
    )
    asset_rows = await db.query_raw(
        """
        SELECT asset_key,
               content_type,
               encode(content, 'base64') AS content_base64,
               content_sha256,
               size_bytes,
               original_filename,
               updated_by,
               created_at,
               updated_at
        FROM deltallm_ui_branding_asset
        WHERE asset_key IN ($1, $2, $3)
        ORDER BY asset_key
        """,
        "logo_mark",
        "logo_full",
        "favicon",
    )
    return _DatabaseSnapshot(
        config_row=dict(config_rows[0]) if config_rows else None,
        asset_rows=[dict(row) for row in asset_rows],
    )


async def _restore_database(db: Any, snapshot: _DatabaseSnapshot) -> None:
    async with db.tx() as transaction:
        await transaction.execute_raw(
            "DELETE FROM deltallm_ui_branding_asset WHERE asset_key IN ($1, $2, $3)",
            "logo_mark",
            "logo_full",
            "favicon",
        )
        for row in snapshot.asset_rows:
            await transaction.execute_raw(
                """
                INSERT INTO deltallm_ui_branding_asset (
                    asset_key, content_type, content, content_sha256, size_bytes,
                    original_filename, updated_by, created_at, updated_at
                )
                VALUES ($1, $2, decode($3, 'base64'), $4, $5, $6, $7, $8, $9)
                """,
                row["asset_key"],
                row["content_type"],
                row["content_base64"],
                row["content_sha256"],
                row["size_bytes"],
                row["original_filename"],
                row["updated_by"],
                row["created_at"],
                row["updated_at"],
            )
        if snapshot.config_row is None:
            await transaction.execute_raw(
                "DELETE FROM deltallm_config WHERE config_name = $1",
                "proxy_config",
            )
        else:
            await transaction.execute_raw(
                """
                INSERT INTO deltallm_config (config_name, config_value, updated_by, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (config_name) DO UPDATE
                SET config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
                """,
                "proxy_config",
                snapshot.config_row["config_value"],
                snapshot.config_row["updated_by"],
                snapshot.config_row["updated_at"],
            )


async def _seed_custom_branding(db: Any) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_config (config_name, config_value, updated_by, updated_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (config_name) DO UPDATE
        SET config_value = EXCLUDED.config_value,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        """,
        "proxy_config",
        json.dumps(_CUSTOM_CONFIG),
        "branding-integration-test",
    )
    repository = UIBrandingAssetRepository(db)
    await repository.delete_all_known()
    for asset_key in ("logo_mark", "logo_full", "favicon"):
        asset = validate_branding_asset(
            asset_key,
            _PNG_BYTES,
            original_filename=f"{asset_key}.png",
        )
        await repository.upsert(
            asset_key=asset.asset_key,
            content_type=asset.content_type,
            content=asset.content,
            content_sha256=asset.content_sha256,
            size_bytes=asset.size_bytes,
            original_filename=asset.original_filename,
            updated_by="branding-integration-test",
        )


def _factory_update() -> dict[str, object]:
    factory = UIBrandingPayload()
    return {
        "general_settings": {
            "instance_name": factory.instance_name,
            "ui_branding": factory.model_dump(exclude={"instance_name"}),
        }
    }


async def _reset_with_manager(manager: DynamicConfigManager) -> None:
    async def delete_assets(transaction: Any) -> None:
        await UIBrandingAssetRepository(transaction).delete_all_known()

    await manager.update_config(
        _factory_update(),
        updated_by="branding-integration-test",
        transaction_mutation=delete_assets,
    )


async def _read_config(db: Any) -> dict[str, object]:
    rows = await db.query_raw(
        "SELECT config_value FROM deltallm_config WHERE config_name = $1",
        "proxy_config",
    )
    value = rows[0]["config_value"]
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


async def _known_asset_count(db: Any) -> int:
    rows = await db.query_raw(
        """
        SELECT COUNT(*)::int AS count
        FROM deltallm_ui_branding_asset
        WHERE asset_key IN ($1, $2, $3)
        """,
        "logo_mark",
        "logo_full",
        "favicon",
    )
    return int(rows[0]["count"])


class _WrappedTransactionContext:
    def __init__(self, context: Any, wrapper: Callable[[Any], Any]) -> None:
        self.context = context
        self.wrapper = wrapper

    async def __aenter__(self) -> Any:
        transaction = await self.context.__aenter__()
        return self.wrapper(transaction)

    async def __aexit__(self, exc_type, exc, traceback) -> object:  # noqa: ANN001
        return await self.context.__aexit__(exc_type, exc, traceback)


class _DatabaseProxy:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def query_raw(self, query: str, *params: object) -> Any:
        return await self.db.query_raw(query, *params)

    async def execute_raw(self, query: str, *params: object) -> Any:
        return await self.db.execute_raw(query, *params)


class _FailConfigWriteTransaction(_DatabaseProxy):
    async def execute_raw(self, query: str, *params: object) -> Any:
        if "INSERT INTO deltallm_config" in query:
            raise RuntimeError("injected config write failure")
        return await super().execute_raw(query, *params)


class _FailConfigWriteDatabase(_DatabaseProxy):
    def tx(self) -> _WrappedTransactionContext:
        return _WrappedTransactionContext(self.db.tx(), _FailConfigWriteTransaction)


class _ObserveAdvisoryLockTransaction(_DatabaseProxy):
    def __init__(self, db: Any, lock_attempted: asyncio.Event) -> None:
        super().__init__(db)
        self.lock_attempted = lock_attempted

    async def query_raw(self, query: str, *params: object) -> Any:
        if "pg_advisory_xact_lock" in query:
            self.lock_attempted.set()
        return await super().query_raw(query, *params)


class _ObserveAdvisoryLockDatabase(_DatabaseProxy):
    def __init__(self, db: Any, lock_attempted: asyncio.Event) -> None:
        super().__init__(db)
        self.lock_attempted = lock_attempted

    def tx(self) -> _WrappedTransactionContext:
        return _WrappedTransactionContext(
            self.db.tx(),
            lambda transaction: _ObserveAdvisoryLockTransaction(
                transaction,
                self.lock_attempted,
            ),
        )


@pytest.mark.asyncio
async def test_real_postgres_branding_reset_is_atomic_and_idempotent() -> None:
    db = await _connect_prisma()
    snapshot = await _snapshot_database(db)
    manager: DynamicConfigManager | None = None
    try:
        await _seed_custom_branding(db)
        manager = DynamicConfigManager(db, None, {}, poll_interval_seconds=0)
        await manager.initialize()

        await _reset_with_manager(manager)
        await _reset_with_manager(manager)

        stored = await _read_config(db)
        branding = stored["general_settings"]["ui_branding"]  # type: ignore[index]
        assert stored["general_settings"]["instance_name"] == "DeltaLLM"  # type: ignore[index]
        assert stored["general_settings"]["log_level"] == "DEBUG"  # type: ignore[index]
        assert branding == UIBrandingPayload().model_dump(exclude={"instance_name"})
        assert await _known_asset_count(db) == 0
    finally:
        if manager is not None:
            await manager.close()
        await _restore_database(db, snapshot)
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ("config_write", "callback"))
async def test_real_postgres_branding_reset_rolls_back_both_sides(
    failure_point: str,
) -> None:
    db = await _connect_prisma()
    snapshot = await _snapshot_database(db)
    manager: DynamicConfigManager | None = None
    try:
        await _seed_custom_branding(db)
        manager_db = _FailConfigWriteDatabase(db) if failure_point == "config_write" else db
        manager = DynamicConfigManager(manager_db, None, {}, poll_interval_seconds=0)
        await manager.initialize()

        async def delete_then_maybe_fail(transaction: Any) -> None:
            await UIBrandingAssetRepository(transaction).delete_all_known()
            if failure_point == "callback":
                raise RuntimeError("injected callback failure")

        with pytest.raises(DynamicConfigPersistenceError):
            await manager.update_config(
                _factory_update(),
                updated_by="branding-integration-test",
                transaction_mutation=delete_then_maybe_fail,
            )

        assert await _read_config(db) == _CUSTOM_CONFIG
        assert await _known_asset_count(db) == 3
    finally:
        if manager is not None:
            await manager.close()
        await _restore_database(db, snapshot)
        await db.disconnect()


@pytest.mark.asyncio
async def test_real_postgres_replica_updates_serialize_and_preserve_fields() -> None:
    first_db = await _connect_prisma()
    second_db = await _connect_prisma()
    snapshot = await _snapshot_database(first_db)
    first: DynamicConfigManager | None = None
    second: DynamicConfigManager | None = None
    release_first = asyncio.Event()
    try:
        await _seed_custom_branding(first_db)
        first_locked = asyncio.Event()
        second_lock_attempted = asyncio.Event()
        first = DynamicConfigManager(first_db, None, {}, poll_interval_seconds=0)
        second = DynamicConfigManager(
            _ObserveAdvisoryLockDatabase(second_db, second_lock_attempted),
            None,
            {},
            poll_interval_seconds=0,
        )
        await first.initialize()
        await second.initialize()

        async def held_reset(transaction: Any) -> None:
            first_locked.set()
            await release_first.wait()
            await UIBrandingAssetRepository(transaction).delete_all_known()

        first_task = asyncio.create_task(
            first.update_config(
                _factory_update(),
                updated_by="first-replica",
                transaction_mutation=held_reset,
            )
        )
        await first_locked.wait()
        second_task = asyncio.create_task(
            second.update_config(
                {"router_settings": {"routing_strategy": "weighted"}},
                updated_by="second-replica",
            )
        )
        await second_lock_attempted.wait()
        assert second_task.done() is False

        release_first.set()
        await asyncio.gather(first_task, second_task)

        stored = await _read_config(first_db)
        assert stored["general_settings"]["instance_name"] == "DeltaLLM"  # type: ignore[index]
        assert stored["general_settings"]["log_level"] == "DEBUG"  # type: ignore[index]
        assert stored["router_settings"]["routing_strategy"] == "weighted"  # type: ignore[index]
        assert await _known_asset_count(first_db) == 0
    finally:
        release_first.set()
        if first is not None:
            await first.close()
        if second is not None:
            await second.close()
        await _restore_database(first_db, snapshot)
        await second_db.disconnect()
        await first_db.disconnect()


@pytest.mark.asyncio
async def test_real_postgres_committed_apply_failure_recovers_from_source() -> None:
    db = await _connect_prisma()
    snapshot = await _snapshot_database(db)
    manager: DynamicConfigManager | None = None
    try:
        await _seed_custom_branding(db)
        manager = DynamicConfigManager(db, None, {}, poll_interval_seconds=0)
        asset_service = UIBrandingAssetService(db)
        await manager.initialize()
        await asset_service.initialize(manager.get_app_config())
        manager.subscribe(asset_service.on_config_change)
        reject_factory = True

        async def reject_once(config, _changes) -> None:  # noqa: ANN001
            if reject_factory and config.general_settings.instance_name == "DeltaLLM":
                raise RuntimeError("injected local apply failure")

        manager.subscribe(reject_once)

        with pytest.raises(DynamicConfigPostCommitApplyError) as raised:
            await _reset_with_manager(manager)

        assert raised.value.committed_app_config.general_settings.instance_name == "DeltaLLM"
        assert manager.get_app_config().general_settings.instance_name == "Integration Brand"
        assert (await _read_config(db))["general_settings"]["instance_name"] == "DeltaLLM"  # type: ignore[index]
        assert await _known_asset_count(db) == 0

        reject_factory = False
        assert await manager._reload_config_from_source(source="poll") is True
        assert manager.get_app_config().general_settings.instance_name == "DeltaLLM"
        assert await asset_service.get_asset("logo_mark") is None
    finally:
        if manager is not None:
            await manager.close()
        await _restore_database(db, snapshot)
        await db.disconnect()
