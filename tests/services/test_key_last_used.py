from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from src.db.identity.key_last_used import KEY_LAST_USED_WRITE_CHUNK, KeyLastUsedRepository
from src.db.identity.key_repository import KeyRecord
from src.metrics.prometheus import get_prometheus_registry
from src.models.errors import AuthenticationError
from src.services.identity.keys.key_last_used import KeyLastUsedLimits, KeyLastUsedRecorder
from src.services.identity.keys.key_service import KeyService

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = T0

    def __call__(self) -> datetime:
        return self.value


class RecordingWriter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.batches: list[dict[str, datetime]] = []

    async def write(self, usages: Mapping[str, datetime]) -> int:
        self.batches.append(dict(usages))
        if self.error is not None:
            raise self.error
        return len(usages)


class HangingWriter:
    async def write(self, usages: Mapping[str, datetime]) -> int:
        del usages
        await asyncio.sleep(3600)
        return 0


def _event_count(event: str) -> float:
    value = get_prometheus_registry().get_sample_value(
        "deltallm_key_last_used_events_total", {"event": event}
    )
    return value or 0.0


@pytest.mark.asyncio
async def test_record_keeps_one_newest_entry_per_key_until_flush() -> None:
    clock = Clock()
    writer = RecordingWriter()
    recorder = KeyLastUsedRecorder(writer, now=clock)

    recorder.record("hash-a")
    clock.value = T0 + timedelta(seconds=30)
    recorder.record("hash-a")
    recorder.record("hash-b")
    await recorder.flush()
    await recorder.flush()

    assert writer.batches == [{"hash-a": T0 + timedelta(seconds=30), "hash-b": clock.value}]
    assert recorder.pending_count == 0


@pytest.mark.asyncio
async def test_record_drops_new_keys_when_pending_map_is_full() -> None:
    writer = RecordingWriter()
    recorder = KeyLastUsedRecorder(writer, KeyLastUsedLimits(max_pending_keys=2), now=Clock())
    dropped = _event_count("dropped")

    for token_hash in ("hash-a", "hash-b", "hash-c", "hash-a"):
        recorder.record(token_hash)
    await recorder.flush()

    assert set(writer.batches[0]) == {"hash-a", "hash-b"}
    assert _event_count("dropped") == dropped + 1


@pytest.mark.asyncio
async def test_failed_flush_is_counted_and_dropped_without_retry() -> None:
    writer = RecordingWriter(error=RuntimeError("database unavailable"))
    recorder = KeyLastUsedRecorder(writer, now=Clock())
    failed = _event_count("flush_failed")
    recorder.record("hash-a")

    await recorder.flush()
    await recorder.flush()

    assert len(writer.batches) == 1
    assert recorder.pending_count == 0
    assert _event_count("flush_failed") == failed + 1


@pytest.mark.asyncio
async def test_flush_timeout_is_bounded() -> None:
    recorder = KeyLastUsedRecorder(
        HangingWriter(), KeyLastUsedLimits(flush_timeout_seconds=0.01), now=Clock()
    )
    failed = _event_count("flush_failed")
    recorder.record("hash-a")

    await asyncio.wait_for(recorder.flush(), timeout=1)

    assert _event_count("flush_failed") == failed + 1


@pytest.mark.asyncio
async def test_run_flushes_on_interval_and_once_more_after_stop() -> None:
    writer = RecordingWriter()
    recorder = KeyLastUsedRecorder(
        writer, KeyLastUsedLimits(flush_interval_seconds=0.01), now=Clock()
    )
    task = asyncio.create_task(recorder.run())
    try:
        recorder.record("hash-a")
        async with asyncio.timeout(1):
            while not writer.batches:
                await asyncio.sleep(0.005)
        recorder.stop()
        recorder.record("hash-b")
        await asyncio.wait_for(task, timeout=1)
    finally:
        task.cancel()

    assert {"hash-a"} in [set(batch) for batch in writer.batches]
    assert "hash-b" in writer.batches[-1]


class RecordingDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], list[str]]] = []

    async def execute_raw(self, query: str, *params: object) -> int:
        tokens, timestamps = params
        assert isinstance(tokens, list) and isinstance(timestamps, list)
        self.calls.append((query, tokens, timestamps))
        return len(tokens)


@pytest.mark.asyncio
async def test_repository_writes_sorted_bounded_chunks_without_lock_waits() -> None:
    db = RecordingDatabase()
    usages = {f"hash-{index:05d}": T0 for index in reversed(range(KEY_LAST_USED_WRITE_CHUNK + 1))}

    updated = await KeyLastUsedRepository(db).write(usages)

    assert updated == KEY_LAST_USED_WRITE_CHUNK + 1
    assert [len(tokens) for _, tokens, _ in db.calls] == [KEY_LAST_USED_WRITE_CHUNK, 1]
    assert db.calls[0][1] == sorted(db.calls[0][1])
    assert db.calls[0][2][0] == T0.isoformat()
    assert "SKIP LOCKED" in db.calls[0][0]


class InMemoryRepo:
    def __init__(self, records: dict[str, KeyRecord]) -> None:
        self.records = records

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        return self.records.get(token_hash)


@pytest.mark.asyncio
async def test_key_service_records_only_successful_authentication() -> None:
    salt = "test-salt"
    raw_key = "sk-last-used"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo({token_hash: KeyRecord(token=token_hash)})
    writer = RecordingWriter()
    recorder = KeyLastUsedRecorder(writer, now=Clock())
    service = KeyService(repository=repo, salt=salt, last_used=recorder)

    await service.validate_key(raw_key)
    with pytest.raises(AuthenticationError):
        await service.validate_key("sk-unknown")
    await recorder.flush()

    assert writer.batches == [{token_hash: T0}]
