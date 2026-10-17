from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from src.router.selection.contracts import SelectorCause, SelectorPolicyIdentity
from src.router.selection.session import SelectorSessionPin, pinned_decision
from src.router.selector_session_store import MemorySelectorSessionStore
from src.routers.selector_edge import _observe_decision
from tests.router.selection.provider_fixtures import response_body
from tests.router.selection.test_realtime import configure
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app

HEADERS = {"Authorization": "Bearer sk-test", "x-deltallm-session-id": "conversation-1"}
FIRST = [{"role": "user", "content": "hello"}]
TOOL_TURN = FIRST + [
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "42"},
]
NEW_TURN = TOOL_TURN + [
    {"role": "assistant", "content": "done"},
    {"role": "user", "content": "thanks, now something else"},
]


class _Upstream:
    def __init__(self, lanes):
        self.lanes = list(lanes)
        self.models: list[str] = []

    def __call__(self, request):
        data = json.loads(request.content)
        self.models.append(data["model"])
        if data["model"] == "classifier":
            return httpx.Response(
                200, json=response_body(text=json.dumps({"lane": self.lanes.pop(0)}))
            )
        return httpx.Response(200, json=response_body(text="answer"))


async def _chat(client, messages, headers=HEADERS):
    # Unique temperatures keep the whole-response cache from answering the turn.
    body = {"model": "gpt-4o-mini", "messages": messages, "temperature": len(messages) / 10}
    response = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response


def _setup(test_app, upstream, **session):
    billing, policy = configure(test_app, upstream, independent=True)
    if session:
        policy["selector"]["session"] = session
        _publish(test_app, [policy])
    test_app.state.selector_session_store = MemorySelectorSessionStore()
    return billing


async def test_continuations_reuse_the_session_lane_without_a_selector_call(client, test_app):
    upstream = _Upstream(["economy"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        billing = _setup(test_app, http)
        await _chat(client, FIRST)
        await _chat(client, TOOL_TURN)
        assert upstream.models == ["classifier", "economy", "economy"]
        billing.accept_selector.assert_awaited_once()
        billing.reserve.assert_awaited_once()


async def test_a_new_user_turn_classifies_again_and_never_moves_down(client, test_app):
    upstream = _Upstream(["quality", "economy"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        billing = _setup(test_app, http, ttl_seconds=120)
        await _chat(client, FIRST)
        await _chat(client, TOOL_TURN)
        await _chat(client, NEW_TURN)
        # Quality is the top lane, so the later turn also skips classification.
        assert upstream.models == ["classifier", "quality", "quality", "quality"]
        billing.accept_selector.assert_awaited_once()


async def test_new_turn_below_the_top_lane_is_floored_at_the_session_lane(client, test_app):
    upstream = _Upstream(["standard", "economy"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        billing, policy = configure(test_app, http, independent=True)
        backing = test_app.state.model_registry["backing"]
        premium = deepcopy(next(item for item in backing if item["deployment_id"] == "quality"))
        premium["deployment_id"] = "premium"
        premium["deltallm_params"]["model"] = "openai/premium"
        backing.append(premium)
        policy["selector"]["lanes"] = [
            {"id": "economy", "rank": 0, "description": "Routine"},
            {"id": "standard", "rank": 1, "description": "Normal"},
            {"id": "premium", "rank": 2, "description": "Hard"},
        ]
        policy["members"] = [
            {"deployment_id": "economy", "lane": "economy"},
            {"deployment_id": "quality", "lane": "standard"},
            {"deployment_id": "premium", "lane": "premium"},
        ]
        _publish(test_app, [policy])
        test_app.state.selector_session_store = MemorySelectorSessionStore()
        await _chat(client, FIRST)
        await _chat(client, NEW_TURN)
        assert upstream.models == ["classifier", "quality", "classifier", "quality"]
        assert billing.accept_selector.await_count == 2


@pytest.mark.parametrize(
    "headers,session",
    [
        ({**HEADERS, "x-deltallm-session-affinity": "off"}, {}),
        (HEADERS, {"enabled": False}),
    ],
)
async def test_opt_out_classifies_every_request(client, test_app, headers, session):
    upstream = _Upstream(["economy", "economy"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        _setup(test_app, http, **session)
        await _chat(client, FIRST, headers)
        await _chat(client, TOOL_TURN, headers)
        assert upstream.models == ["classifier", "economy", "classifier", "economy"]


async def test_sessions_are_scoped_to_their_id(client, test_app):
    upstream = _Upstream(["economy", "quality"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        _setup(test_app, http)
        await _chat(client, FIRST)
        other = {**HEADERS, "x-deltallm-session-id": "conversation-2"}
        await _chat(client, TOOL_TURN, other)
        assert upstream.models == ["classifier", "economy", "classifier", "quality"]


def test_a_reused_lane_is_recorded_without_a_selector_charge_reference(selector_policy):
    request = SimpleNamespace(state=SimpleNamespace())
    identity = SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "a" * 64)
    decision = pinned_decision(
        SelectorSessionPin(rank=1, deployment_id="quality"), selector_policy, identity
    )
    assert decision.cause is SelectorCause.SESSION_PINNED
    _observe_decision(request, decision)
    # The answer reservation adds selector_event_id only for a "selector" entry.
    assert "selector" not in request.state.route_decision
    assert request.state.route_decision["selector_session"] == {
        "lane": "quality",
        "minimum_rank": 1,
        "reused": True,
    }
