from __future__ import annotations

import asyncio
import json

import pytest

from src.db.prompt_registry import PromptBindingRecord
from src.services.prompt_registry import PromptRegistryService
from tests.test_prompt_cache_performance import _CountingRepository, _Redis

SCOPES = [
    ("user", "user"),
    ("api_key", "key"),
    ("team", "team"),
    ("organization", "org"),
    ("group", "model"),
]


@pytest.mark.asyncio
async def test_cold_five_scope_chain_has_one_pipeline_and_warm_chain_no_io():
    redis, repo = _Redis(), _CountingRepository()
    service = PromptRegistryService(repository=repo, redis_client=redis)
    assert await service._resolve_binding_chain(SCOPES) == [None] * 5
    assert (repo.binding_queries, redis.mget_calls, redis.pipeline_calls) == (1, 1, 1)
    assert len(redis.pipeline_commands) == 5
    assert await service._resolve_binding_chain(SCOPES) == [None] * 5
    assert (repo.binding_queries, redis.mget_calls, redis.pipeline_calls) == (1, 1, 1)
    second_repo = _CountingRepository()
    second = PromptRegistryService(repository=second_repo, redis_client=redis)
    assert await second._resolve_binding_chain(SCOPES) == [None] * 5
    assert second_repo.binding_queries == 0


@pytest.mark.asyncio
async def test_disabled_negative_cache_does_not_write_absent_scopes():
    redis, repo = _Redis(), _CountingRepository()
    service = PromptRegistryService(
        repository=repo, redis_client=redis, negative_cache_enabled=False
    )
    await service._resolve_binding_chain(SCOPES)
    assert redis.pipeline_calls == 0
    assert len(service._binding_l1) == 0


@pytest.mark.asyncio
async def test_pipeline_failure_preserves_durable_result_without_sql_retry():
    class FailedRedis(_Redis):
        def pipeline(self, **kwargs):
            raise RuntimeError("unavailable")

    repo = _CountingRepository()
    service = PromptRegistryService(repository=repo, redis_client=FailedRedis())
    assert await service._resolve_binding_chain(SCOPES) == [None] * 5
    assert repo.binding_queries == 1


@pytest.mark.asyncio
async def test_invalidation_during_cold_load_fences_cache_and_new_joiners():
    started, release = asyncio.Event(), asyncio.Event()

    class Repository(_CountingRepository):
        async def resolve_binding_chain(self, **kwargs):
            self.binding_queries += 1
            if self.binding_queries == 1:
                started.set()
                await release.wait()
            return []

    redis, repo = _Redis(), Repository()
    service = PromptRegistryService(repository=repo, redis_client=redis)
    await service._ensure_namespace_epoch()
    first = asyncio.create_task(service._resolve_binding_chain(SCOPES))
    await started.wait()
    await service.invalidate_all()
    assert await service._resolve_binding_chain(SCOPES) == [None] * 5
    assert repo.binding_queries == 2
    release.set()
    assert await first == [None] * 5
    assert all(":e2:" in key for key, _, _ in redis.pipeline_commands)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"_deltallm_cache_state": "miss", "version": "bad"},
        {"cache_version": 2, "scope_type": "organization", "scope_id": "another-tenant"},
    ],
)
async def test_malformed_and_cross_tenant_cache_payloads_are_misses(payload):
    redis, repo = _Redis(), _CountingRepository()
    service = PromptRegistryService(repository=repo, redis_client=redis)
    redis.store[service._binding_cache_key("organization", "org")] = json.dumps(payload)
    assert await service._resolve_binding_chain([("organization", "org")]) == [None]
    assert repo.binding_queries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["organization", "team"])
async def test_implicit_tenant_binding_survives_fill_and_shared_cache(scope):
    binding = PromptBindingRecord(
        prompt_binding_id="binding",
        scope_type=scope,
        scope_id="tenant",
        prompt_template_id="template",
        template_key="prompt",
        label="production",
        priority=0,
        enabled=True,
        metadata=None,
        created_at=None,
        updated_at=None,
    )

    class Repository(_CountingRepository):
        async def resolve_binding_chain(self, *, scopes):
            self.binding_queries += 1
            return [binding] if (scope, "tenant") in scopes else []

    redis, repo = _Redis(), Repository()
    service = PromptRegistryService(repository=repo, redis_client=redis)
    chain = [("api_key", "key"), (scope, "tenant")]
    assert await service._resolve_binding_chain(chain) == [None, binding]
    second_repo = _CountingRepository()
    second = PromptRegistryService(repository=second_repo, redis_client=redis)
    assert await second._resolve_binding_chain(chain) == [None, binding]
    assert second_repo.binding_queries == 0
    assert await second._resolve_binding_chain([(scope, "another-tenant")]) == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["[" * 2000 + "0" + "]" * 2000, "0" * 70000])
async def test_pathological_binding_cache_json_is_a_miss(raw):
    redis, repo = _Redis(), _CountingRepository()
    service = PromptRegistryService(repository=repo, redis_client=redis)
    redis.store[service._binding_cache_key("organization", "org")] = raw
    assert await service._resolve_binding_chain([("organization", "org")]) == [None]
    assert repo.binding_queries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["team", "organization"])
async def test_omitted_prompt_ref_renders_tenant_binding_across_cache_owners(scope):
    from tests.test_prompt_preflight import _PromptRepoForDefaults

    class Repository(_PromptRepoForDefaults):
        def __init__(self):
            self.binding_queries = 0

        async def resolve_binding_chain(self, *, scopes):
            self.binding_queries += 1
            if (scope, "tenant") not in scopes:
                return []
            return [
                PromptBindingRecord(
                    prompt_binding_id="binding",
                    scope_type=scope,
                    scope_id="tenant",
                    prompt_template_id="tmpl-support",
                    template_key="support.prompt",
                    label="production",
                    priority=1,
                    enabled=True,
                    metadata=None,
                    created_at=None,
                    updated_at=None,
                )
            ]

    redis = _Redis()
    args = dict(
        explicit_reference=None,
        variables={},
        api_key="key",
        user_id=None,
        team_id="tenant" if scope == "team" else None,
        organization_id="tenant" if scope == "organization" else None,
        route_group_key=None,
        model="model",
        request_id=None,
    )
    first_repo, next_repo = Repository(), Repository()
    first = PromptRegistryService(repository=first_repo, redis_client=redis)
    second = PromptRegistryService(repository=next_repo, redis_client=redis)
    try:
        for service in (first, first, second):
            result = await service.resolve_and_render(**args)
            assert result.messages == [{"role": "system", "content": "Support prompt active."}]
            assert result.provenance.binding_scope == scope
        assert first_repo.binding_queries == 1
        assert next_repo.binding_queries == 0
    finally:
        await first.shutdown()
        await second.shutdown()
