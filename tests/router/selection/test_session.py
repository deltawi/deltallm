import asyncio
import json
from types import SimpleNamespace

import pytest

from src.models.requests import ChatCompletionRequest
from src.route_policy_contract import LLMTierSelectorPolicy
from src.router.redis_keys import RouterRedisKeyspace
from src.router.selection.contracts import SelectorCause, SelectorDecision, UnknownSelectorUsage
from src.router.selection.planning import _prefer
from src.router.selection.policy import build_routing_fingerprint
from src.router.selection.session import (
    SelectorSession,
    SelectorSessionPin,
    SelectorSessionSource,
    apply_session_floor,
    has_new_user_text,
    pinned_decision,
    preferred_deployment,
    set_preferred_deployment,
)
from src.router.selection.session_identity import (
    MAX_SESSION_ID_LENGTH,
    selector_session_identity,
)
from src.router.selector_session_store import (
    MemorySelectorSessionStore,
    RedisSelectorSessionStore,
    decode_session_pin,
    encode_session_pin,
)


def _payload(*messages, **extra):
    return ChatCompletionRequest.model_validate(
        {"model": "group", "messages": list(messages), **extra}
    )


def _three_lanes(**session):
    return LLMTierSelectorPolicy.model_validate(
        {
            "kind": "llm-tier",
            "classifier_deployment_id": "tiny",
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine"},
                {"id": "standard", "rank": 1, "description": "Normal"},
                {"id": "quality", "rank": 2, "description": "Hard"},
            ],
            **({"session": session} if session else {}),
        }
    )


USER = {"role": "user", "content": "hello"}
SYSTEM = {"role": "system", "content": "You are helpful"}
ASSISTANT_TOOL_CALL = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
}
TOOL_RESULT = {"role": "tool", "tool_call_id": "c1", "content": "42"}


@pytest.mark.parametrize(
    "messages,expected",
    [
        ([USER], True),
        ([USER, ASSISTANT_TOOL_CALL, TOOL_RESULT], False),
        ([USER, {"role": "assistant", "content": "prefill"}], False),
        ([{"role": "user", "content": "   "}], False),
        ([{"role": "user", "content": [{"type": "text", "text": "look"}]}], True),
        (
            [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "https://x.test/a"}}],
                }
            ],
            False,
        ),
    ],
)
def test_only_new_user_text_starts_a_new_turn(messages, expected):
    assert has_new_user_text(_payload(*messages)) is expected


def test_session_header_precedence_and_principal_scope():
    payload = _payload(USER)
    explicit = selector_session_identity(
        headers={"x-deltallm-session-id": "s1", "x-claude-code-session-id": "c1"},
        payload=payload,
        api_key="key-a",
    )
    assert explicit.source is SelectorSessionSource.HEADER
    agent = selector_session_identity(
        headers={"x-claude-code-session-id": "s1"}, payload=payload, api_key="key-a"
    )
    assert agent.source is SelectorSessionSource.AGENT
    other_key = selector_session_identity(
        headers={"x-deltallm-session-id": "s1"}, payload=payload, api_key="key-b"
    )
    assert explicit.digest != other_key.digest
    assert "s1" not in explicit.digest


def test_codex_metadata_and_request_metadata_name_a_session():
    payload = _payload(USER)
    codex = selector_session_identity(
        headers={"x-codex-turn-metadata": json.dumps({"session_id": "abc"})},
        payload=payload,
        api_key="k",
    )
    assert codex.source is SelectorSessionSource.AGENT
    metadata = selector_session_identity(
        headers={}, payload=_payload(USER, metadata={"session_id": "abc"}), api_key="k"
    )
    assert metadata.source is SelectorSessionSource.METADATA


@pytest.mark.parametrize(
    "headers",
    [
        {"x-deltallm-session-affinity": "off", "x-deltallm-session-id": "s1"},
        {"x-deltallm-session-id": "x" * (MAX_SESSION_ID_LENGTH + 1)},
        {"x-deltallm-session-id": "  "},
        {"x-codex-turn-metadata": "not json"},
        {"x-codex-turn-metadata": json.dumps({"session_id": "a" * 5000})},
    ],
)
def test_invalid_or_disabled_session_ids_never_fall_back(headers):
    assert selector_session_identity(headers=headers, payload=_payload(USER), api_key="k") is None


def test_fingerprint_is_stable_across_turns_and_separates_end_users():
    first = selector_session_identity(headers={}, payload=_payload(SYSTEM, USER), api_key="k")
    later = selector_session_identity(
        headers={},
        payload=_payload(SYSTEM, USER, ASSISTANT_TOOL_CALL, TOOL_RESULT),
        api_key="k",
    )
    assert first.source is SelectorSessionSource.FINGERPRINT
    assert first == later
    other_user = selector_session_identity(
        headers={}, payload=_payload(SYSTEM, USER, user="someone-else"), api_key="k"
    )
    assert other_user != first
    assert selector_session_identity(headers={}, payload=_payload(SYSTEM), api_key="k") is None


def test_session_policy_is_omitted_by_default_and_keeps_fingerprints(selector_policy):
    assert "session" not in selector_policy.model_dump(mode="json")
    assert selector_policy.effective_session.enabled is True
    assert selector_policy.effective_session.ttl_seconds == 3600
    custom = LLMTierSelectorPolicy.model_validate(
        {**selector_policy.model_dump(mode="json"), "session": {"enabled": False}}
    )
    assert custom.model_dump(mode="json")["session"] == {"enabled": False, "ttl_seconds": 3600}

    def fingerprint(selector):
        return build_routing_fingerprint(
            workload_mode="chat",
            strategy="simple-shuffle",
            semantics_version=3,
            selector=selector,
            effective_members=[],
        )

    legacy = selector_policy.model_dump(mode="json")
    assert fingerprint(selector_policy) == fingerprint(legacy)
    assert fingerprint(custom) != fingerprint(selector_policy)


@pytest.mark.parametrize("ttl", [59, 86_401])
def test_session_ttl_is_bounded(selector_policy, ttl):
    with pytest.raises(ValueError):
        LLMTierSelectorPolicy.model_validate(
            {**selector_policy.model_dump(mode="json"), "session": {"ttl_seconds": ttl}}
        )


def _decision(policy_identity, rank, cause=SelectorCause.CLASSIFIED):
    return SelectorDecision(
        lane=["economy", "standard", "quality"][rank],
        minimum_rank=rank,
        cause=cause,
        latency_ms=3.0,
        policy_identity=policy_identity,
        usage=UnknownSelectorUsage(),
    )


def test_floor_keeps_the_session_lane_and_records_why(policy_identity):
    policy = _three_lanes()
    floored = apply_session_floor(_decision(policy_identity, 0), policy, 1)
    assert (floored.lane, floored.minimum_rank, floored.cause) == (
        "standard",
        1,
        SelectorCause.SESSION_FLOOR,
    )
    assert not floored.used_default
    timeout = apply_session_floor(
        _decision(policy_identity, 0, SelectorCause.SELECTOR_TIMEOUT), policy, 1
    )
    assert timeout.cause is SelectorCause.SELECTOR_TIMEOUT and timeout.minimum_rank == 1
    higher = _decision(policy_identity, 2)
    assert apply_session_floor(higher, policy, 1) is higher
    assert apply_session_floor(higher, policy, None) is higher


def test_pinned_decision_has_no_selector_usage(policy_identity):
    decision = pinned_decision(
        SelectorSessionPin(rank=1, deployment_id="dep"), _three_lanes(), policy_identity
    )
    assert decision.cause is SelectorCause.SESSION_PINNED
    assert decision.usage.kind == "not_attempted"
    assert decision.lane == "standard" and not decision.used_default


class _RecordingStore:
    def __init__(self, pin=None):
        self.pin = pin
        self.lookups: list[str] = []
        self.writes: list[tuple[str, SelectorSessionPin, int]] = []

    async def lookup(self, key):
        self.lookups.append(key)
        return self.pin

    def remember(self, key, pin, *, ttl_seconds):
        self.writes.append((key, pin, ttl_seconds))


def _session(store, *, new_user_turn=True, digest="d"):
    identity = selector_session_identity(
        headers={"x-deltallm-session-id": digest}, payload=_payload(USER), api_key="k"
    )
    return SelectorSession(identity, store, new_user_turn=new_user_turn)


async def test_session_reuses_on_continuations_and_at_the_top_lane(policy_identity):
    policy = _three_lanes()
    store = _RecordingStore(SelectorSessionPin(rank=1, deployment_id="dep"))
    session = _session(store, new_user_turn=False)
    pin = await session.lookup("group", policy, policy_identity)
    assert session.reuses(pin, policy)
    assert not _session(store).reuses(pin, policy)
    top = SelectorSessionPin(rank=2, deployment_id="dep")
    assert _session(store).reuses(top, policy)
    with pytest.raises(Exception):
        await session.lookup("group", policy, policy_identity)


async def test_session_remembers_once_scoped_and_never_downgrades(policy_identity):
    policy = _three_lanes(ttl_seconds=120)
    store = _RecordingStore(SelectorSessionPin(rank=1, deployment_id="old"))
    session = _session(store)
    await session.lookup("group", policy, policy_identity)
    session.remember("other-group", "dep", 2)
    session.remember("group", "lower", 0)
    session.remember("group", "dep", 2)
    assert store.writes == [
        (store.lookups[0], SelectorSessionPin(rank=2, deployment_id="dep"), 120)
    ]


async def test_disabled_session_or_unknown_rank_is_a_miss(policy_identity):
    store = _RecordingStore(SelectorSessionPin(rank=1, deployment_id="dep"))
    disabled = _session(store)
    assert await disabled.lookup("group", _three_lanes(enabled=False), policy_identity) is None
    disabled.remember("group", "dep", 1)
    assert store.lookups == [] and store.writes == []
    store.pin = SelectorSessionPin(rank=7, deployment_id="dep")
    assert await _session(store).lookup("group", _three_lanes(), policy_identity) is None


async def test_policy_change_moves_sessions_to_a_new_key(policy_identity):
    store = _RecordingStore()
    await _session(store).lookup("group", _three_lanes(), policy_identity)
    changed = policy_identity.model_copy(update={"fingerprint": "route-policy-v1:" + "b" * 64})
    await _session(store).lookup("group", _three_lanes(), changed)
    await _session(store).lookup("another", _three_lanes(), policy_identity)
    assert len(set(store.lookups)) == 3


def test_preferred_deployment_moves_first_and_keeps_failover_order():
    context: dict[str, object] = {}
    assert preferred_deployment(context) is None
    set_preferred_deployment(context, "b")
    deployments = [SimpleNamespace(deployment_id=key) for key in ("a", "b", "c")]
    ordered = _prefer(deployments, preferred_deployment(context))
    assert [d.deployment_id for d in ordered] == ["b", "a", "c"]
    assert _prefer(deployments, "missing") == deployments
    assert _prefer(deployments, None) == deployments


def test_pin_entries_are_versioned_and_validated():
    pin = SelectorSessionPin(rank=1, deployment_id="dep")
    assert decode_session_pin(encode_session_pin(pin)) == pin
    assert decode_session_pin(encode_session_pin(pin).encode()) == pin
    for raw in (
        "not json",
        json.dumps({"v": 2, "r": 1, "d": "dep"}),
        json.dumps({"v": 1, "r": 9, "d": "dep"}),
        json.dumps({"v": 1, "r": True, "d": "dep"}),
        json.dumps({"v": 1, "r": 1, "d": ""}),
        json.dumps({"v": 1, "r": 1, "d": "x" * 600}),
    ):
        assert decode_session_pin(raw) is None


async def test_memory_store_expires_and_bounds_entries(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.router.selector_session_store.time.monotonic", lambda: now[0])
    store = MemorySelectorSessionStore(max_entries=2)
    pin = SelectorSessionPin(rank=0, deployment_id="dep")
    store.remember("a", pin, ttl_seconds=60)
    store.remember("b", pin, ttl_seconds=60)
    assert await store.lookup("a") == pin
    store.remember("c", pin, ttl_seconds=60)
    assert await store.lookup("b") is None  # least recently used
    now[0] += 61
    assert await store.lookup("a") is None


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False
        self.delay = 0.0
        self.release = asyncio.Event()
        self.block_writes = False

    async def get(self, name):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("down")
        return self.values.get(name)

    async def set(self, name, value, *, ex):
        if self.block_writes:
            await self.release.wait()
        if self.fail:
            raise ConnectionError("down")
        self.values[name], self.ttls[name] = value, ex


async def test_redis_store_round_trip_uses_namespaced_keys_and_ttl():
    redis = _FakeRedis()
    keyspace = RouterRedisKeyspace(environment="test")
    store = RedisSelectorSessionStore(redis, keyspace=keyspace)
    pin = SelectorSessionPin(rank=1, deployment_id="dep")
    store.remember("k" * 64, pin, ttl_seconds=90)
    await store.close()
    name = keyspace.selector_session("k" * 64)
    assert name.startswith("deltallm:test:v1:selector-session")
    assert redis.ttls[name] == 90
    assert await RedisSelectorSessionStore(redis, keyspace=keyspace).lookup("k" * 64) == pin


async def test_redis_failures_and_slow_reads_are_misses():
    redis = _FakeRedis()
    keyspace = RouterRedisKeyspace()
    store = RedisSelectorSessionStore(redis, keyspace=keyspace, lookup_timeout_seconds=0.01)
    redis.values[keyspace.selector_session("k")] = "garbage"
    assert await store.lookup("k") is None
    redis.fail = True
    assert await store.lookup("k") is None
    redis.fail, redis.delay = False, 0.05
    assert await store.lookup("k") is None
    redis.fail = True
    store.remember("k", SelectorSessionPin(rank=0, deployment_id="d"), ttl_seconds=60)
    await store.close()


async def test_redis_writes_are_bounded_and_drained_on_close():
    redis = _FakeRedis()
    redis.block_writes = True
    store = RedisSelectorSessionStore(redis, keyspace=RouterRedisKeyspace(), max_pending_writes=2)
    pin = SelectorSessionPin(rank=0, deployment_id="d")
    for key in ("a", "b", "c"):
        store.remember(key, pin, ttl_seconds=60)
    await asyncio.sleep(0)
    assert len(store._pending) == 2
    await store.close(timeout_seconds=0.01)
    assert not store._pending
    assert redis.values == {}
    store.remember("d", pin, ttl_seconds=60)
    assert not store._pending
