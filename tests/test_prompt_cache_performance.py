from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from src.db.prompt_registry import (
    PromptBindingRecord,
    PromptRegistryRepository,
    PromptResolvedRecord,
)
from src.services.prompt_registry import PromptReference, PromptRegistryService
from src.services.prompt_singleflight import (
    PromptSingleflight,
    PromptSingleflightOverloadedError,
    PromptSingleflightTimeoutError,
)
from src.telemetry.prompt_render import PromptRenderEvent


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.mget_calls = 0

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.store[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls += 1
        return [self.store.get(key) for key in keys]

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)


class _CountingRepository:
    def __init__(self, *, prompt: PromptResolvedRecord | None = None, delay: float = 0.0) -> None:
        self.prompt = prompt
        self.delay = delay
        self.binding_queries = 0
        self.prompt_queries = 0
        self.render_logs = 0
        self.render_log_payloads: list[dict[str, Any]] = []

    async def resolve_binding_chain(
        self,
        *,
        scopes: list[tuple[str, str]],
    ) -> list[PromptBindingRecord]:
        del scopes
        self.binding_queries += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return []

    async def resolve_prompt(
        self,
        *,
        template_key: str,
        label: str | None = None,
        version: int | None = None,
    ) -> PromptResolvedRecord | None:
        del template_key, label, version
        self.prompt_queries += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.prompt

    async def create_render_log(self, **kwargs: Any) -> None:
        self.render_logs += 1
        self.render_log_payloads.append(kwargs)


class _RenderLogSink:
    def __init__(self) -> None:
        self.events: list[PromptRenderEvent] = []

    async def enqueue_prompt_render(self, event: PromptRenderEvent) -> None:
        self.events.append(event)


async def _resolve_no_prompt(service: PromptRegistryService, api_key: str) -> None:
    result = await service.resolve_and_render(
        explicit_reference=None,
        variables={},
        api_key=api_key,
        user_id=None,
        team_id=None,
        organization_id=None,
        route_group_key=None,
        model="direct-model",
        request_id=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_negative_binding_cache_avoids_repeated_repository_queries() -> None:
    repository = _CountingRepository()
    service = PromptRegistryService(repository=repository)

    await _resolve_no_prompt(service, "key-1")
    await _resolve_no_prompt(service, "key-1")

    assert repository.binding_queries == 1


@pytest.mark.asyncio
async def test_binding_singleflight_collapses_simultaneous_cold_wave() -> None:
    repository = _CountingRepository(delay=0.01)
    service = PromptRegistryService(repository=repository)

    await asyncio.gather(*(_resolve_no_prompt(service, "same-key") for _ in range(300)))

    assert repository.binding_queries == 1
    assert service._inflight == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_singleflight_bounds_distinct_keys_but_allows_existing_key_join() -> None:
    singleflight = PromptSingleflight(max_keys=2, timeout_seconds=1)
    release = asyncio.Event()
    both_started = asyncio.Event()
    started = 0

    async def loader(value: str) -> str:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return value

    first = asyncio.create_task(singleflight.run("one", lambda: loader("one")))
    second = asyncio.create_task(singleflight.run("two", lambda: loader("two")))
    await both_started.wait()

    with pytest.raises(PromptSingleflightOverloadedError):
        await singleflight.run("three", lambda: loader("three"))

    joined = asyncio.create_task(singleflight.run("one", lambda: loader("duplicate")))
    release.set()
    assert await asyncio.gather(first, second, joined) == ["one", "two", "one"]
    assert started == 2
    assert singleflight.size == 0


@pytest.mark.asyncio
async def test_singleflight_timeout_and_shutdown_release_owned_tasks() -> None:
    singleflight = PromptSingleflight(max_keys=1, timeout_seconds=0.01)
    never = asyncio.Event()

    with pytest.raises(PromptSingleflightTimeoutError):
        await singleflight.run("timeout", never.wait)
    assert singleflight.size == 0

    started = asyncio.Event()

    async def blocked() -> None:
        started.set()
        await never.wait()

    task = asyncio.create_task(singleflight.run("blocked", blocked))
    await started.wait()
    await singleflight.shutdown(timeout_seconds=0)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert singleflight.size == 0


@pytest.mark.asyncio
async def test_negative_binding_cache_is_shared_through_redis() -> None:
    redis = _Redis()
    first_repository = _CountingRepository()
    first = PromptRegistryService(repository=first_repository, redis_client=redis)
    await _resolve_no_prompt(first, "key-1")

    second_repository = _CountingRepository()
    second = PromptRegistryService(repository=second_repository, redis_client=redis)
    await _resolve_no_prompt(second, "key-1")

    assert first_repository.binding_queries == 1
    assert second_repository.binding_queries == 0
    assert redis.mget_calls == 2


@pytest.mark.asyncio
async def test_prompt_singleflight_collapses_explicit_prompt_wave() -> None:
    prompt = PromptResolvedRecord(
        prompt_template_id="template-1",
        template_key="support.prompt",
        prompt_version_id="version-1",
        version=1,
        status="published",
        label="production",
        template_body={"text": "hello"},
        variables_schema=None,
    )
    repository = _CountingRepository(prompt=prompt, delay=0.01)
    service = PromptRegistryService(repository=repository)

    async def resolve() -> None:
        result = await service.resolve_and_render(
            explicit_reference=PromptReference("support.prompt", label="production"),
            variables={},
            api_key="key-1",
            user_id=None,
            team_id=None,
            organization_id=None,
            route_group_key=None,
            model="direct-model",
            request_id=None,
        )
        assert result is not None

    await asyncio.gather(*(resolve() for _ in range(100)))

    assert repository.prompt_queries == 1


@pytest.mark.asyncio
async def test_successful_prompt_uses_render_ingress_not_destination_repository() -> None:
    prompt = PromptResolvedRecord(
        prompt_template_id="template-1",
        template_key="support.prompt",
        prompt_version_id="version-1",
        version=1,
        status="published",
        label="production",
        template_body={"text": "hello {name}"},
        variables_schema=None,
    )
    repository = _CountingRepository(prompt=prompt)
    sink = _RenderLogSink()
    service = PromptRegistryService(repository=repository, render_log_sink=sink)

    result = await service.resolve_and_render(
        explicit_reference=PromptReference("support.prompt", label="production"),
        variables={"name": "Ada"},
        api_key="key-1",
        user_id=None,
        team_id=None,
        organization_id="org-1",
        route_group_key=None,
        model="direct-model",
        request_id="request-1",
    )
    assert result is not None
    assert repository.render_logs == 0
    assert sink.events[0].prompt_key == "support.prompt"
    assert sink.events[0].variables == {"name": "Ada"}
    assert str(UUID(sink.events[0].prompt_render_log_id)) == sink.events[0].prompt_render_log_id
    assert str(UUID(str(sink.events[0].audit_event_id))) == sink.events[0].audit_event_id


@pytest.mark.asyncio
async def test_prompt_render_persistence_failure_is_not_downgraded() -> None:
    prompt = PromptResolvedRecord(
        prompt_template_id="template-1",
        template_key="support.prompt",
        prompt_version_id="version-1",
        version=1,
        status="published",
        label="production",
        template_body={"text": "hello"},
        variables_schema=None,
    )
    repository = _CountingRepository(prompt=prompt)

    class _FailingSink:
        def __init__(self) -> None:
            self.event_ids: list[str] = []

        async def enqueue_prompt_render(self, event: PromptRenderEvent) -> None:
            self.event_ids.append(event.prompt_render_log_id)
            raise RuntimeError("temporary database error")

    sink = _FailingSink()
    service = PromptRegistryService(repository=repository, render_log_sink=sink)

    with pytest.raises(RuntimeError, match="temporary database error"):
        await service.resolve_and_render(
            explicit_reference=PromptReference("support.prompt", label="production"),
            variables={},
            api_key="key-1",
            user_id=None,
            team_id=None,
            organization_id="org-1",
            route_group_key=None,
            model="direct-model",
            request_id="client-request-id",
        )

    assert len(sink.event_ids) == 1
    assert sink.event_ids[0] != "client-request-id"


@pytest.mark.asyncio
async def test_prompt_render_without_policy_aware_sink_fails_closed() -> None:
    prompt = PromptResolvedRecord(
        prompt_template_id="template-1",
        template_key="support.prompt",
        prompt_version_id="version-1",
        version=1,
        status="published",
        label="production",
        template_body={"text": "hello {name}"},
        variables_schema=None,
    )
    repository = _CountingRepository(prompt=prompt)
    service = PromptRegistryService(repository=repository)

    await service.resolve_and_render(
        explicit_reference=PromptReference("support.prompt", label="production"),
        variables={"name": "Ada"},
        api_key="key-1",
        user_id=None,
        team_id=None,
        organization_id="org-1",
        route_group_key=None,
        model="direct-model",
        request_id="request-1",
    )
    assert repository.render_log_payloads[0]["variables"] is None
    assert repository.render_log_payloads[0]["variables_redacted"] is True


@pytest.mark.asyncio
async def test_prompt_resolution_waits_for_required_render_acceptance() -> None:
    prompt = PromptResolvedRecord(
        prompt_template_id="template-1",
        template_key="support.prompt",
        prompt_version_id="version-1",
        version=1,
        status="published",
        label="production",
        template_body={"text": "hello"},
        variables_schema=None,
    )
    repository = _CountingRepository(prompt=prompt)
    persistence_started = asyncio.Event()
    release_persistence = asyncio.Event()

    class _BlockingSink:
        async def enqueue_prompt_render(self, event: PromptRenderEvent) -> None:
            del event
            persistence_started.set()
            await release_persistence.wait()

    service = PromptRegistryService(repository=repository, render_log_sink=_BlockingSink())
    resolution = asyncio.create_task(
        service.resolve_and_render(
            explicit_reference=PromptReference("support.prompt", label="production"),
            variables={},
            api_key="key-1",
            user_id=None,
            team_id=None,
            organization_id="org-1",
            route_group_key=None,
            model="direct-model",
            request_id="request-1",
        )
    )

    await asyncio.wait_for(persistence_started.wait(), timeout=0.1)
    assert not resolution.done()
    release_persistence.set()
    result = await asyncio.wait_for(resolution, timeout=0.1)
    assert result is not None


@pytest.mark.asyncio
async def test_l1_cache_remains_bounded_under_identity_churn() -> None:
    repository = _CountingRepository()
    service = PromptRegistryService(repository=repository, l1_max_entries=10)

    for index in range(100):
        await _resolve_no_prompt(service, f"key-{index}")

    assert len(service._binding_l1) == 10  # noqa: SLF001


class _Prisma:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, query: str, *parameters: Any) -> list[dict[str, Any]]:
        self.calls.append((query, parameters))
        return [
            {
                "prompt_binding_id": "binding-1",
                "scope_type": "key",
                "scope_id": "key-1",
                "prompt_template_id": "template-1",
                "template_key": "support.prompt",
                "label": "production",
                "priority": 1,
                "enabled": True,
                "metadata": json.dumps({}),
            }
        ]


@pytest.mark.asyncio
async def test_repository_batches_aliases_and_scopes_into_one_query() -> None:
    prisma = _Prisma()
    repository = PromptRegistryRepository(prisma)

    result = await repository.resolve_binding_chain(
        scopes=[("user", "user-1"), ("api_key", "key-1"), ("organization", "org-1")]
    )

    assert len(result) == 1
    assert result[0].scope_type == "api_key"
    assert len(prisma.calls) == 1
    query, parameters = prisma.calls[0]
    assert "ROW_NUMBER() OVER" in query
    assert parameters == (
        "user",
        "user",
        "user-1",
        0,
        0,
        "api_key",
        "api_key",
        "key-1",
        1,
        0,
        "api_key",
        "key",
        "key-1",
        1,
        1,
        "organization",
        "organization",
        "org-1",
        2,
        0,
        "organization",
        "org",
        "org-1",
        2,
        1,
    )
