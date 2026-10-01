import asyncio
import json
from dataclasses import replace

import pytest

from src.realtime.contracts import RealtimeError, RealtimeLimits, SocketClosed
from src.realtime.runtime import RealtimeRuntime
from src.realtime.session import relay_session
from tests.realtime.fakes import Admission, Permit, Socket, target


def start(downstream, upstream, permit=None, **limits):
    return asyncio.create_task(
        relay_session(
            downstream,
            upstream,
            target=target(),
            permit=permit or Permit(),
            limits=replace(RealtimeLimits(), **limits),
        )
    )


async def received(socket):
    return json.loads(await asyncio.wait_for(socket.outgoing.get(), timeout=1))


def assert_no_pumps():
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("realtime:")]


async def test_bidirectional_order_and_usage_before_terminal_delivery():
    down, up, permit = Socket(), Socket(), Permit()
    task = start(down, up, permit)
    down.incoming.put_nowait('{"type":"input_audio_buffer.append","audio":"AA=="}')
    down.incoming.put_nowait('{"type":"input_audio_buffer.commit","event_id":"commit1"}')
    assert (await received(up))["audio"] == "AA=="
    assert (await received(up))["event_id"] == "commit1"
    for event in [
        {"type": "response.output_audio.delta", "response_id": "resp1", "delta": "AA=="},
        {"type": "response.done", "response": {"id": "resp1", "usage": None}},
    ]:
        up.incoming.put_nowait(json.dumps(event))
        assert await received(down) == event
    assert permit.receipts[0]["response"]["id"] == "resp1"
    assert len(permit.authorized) == 2
    down.incoming.put_nowait(SocketClosed())
    stats = await asyncio.wait_for(task, 1)
    assert stats.client_events == 2 and stats.server_events == 2
    assert_no_pumps()


async def test_accounting_failure_blocks_terminal_delivery_and_cancels_peer():
    down, up, permit = (
        Socket(),
        Socket(),
        Permit(error=RealtimeError("billing_unavailable", "Billing unavailable")),
    )
    up.incoming.put_nowait('{"type":"response.done","response":{"id":"resp1"}}')
    with pytest.raises(RealtimeError, match="Billing unavailable"):
        await start(down, up, permit)
    assert down.outgoing.empty()
    assert_no_pumps()


async def test_admission_denial_prevents_audio_forwarding():
    down, up = Socket(), Socket()
    down.incoming.put_nowait('{"type":"input_audio_buffer.append","audio":"AA=="}')
    with pytest.raises(RealtimeError, match="Budget"):
        await start(down, up, Permit(error=RealtimeError("budget", "Budget")))
    assert up.outgoing.empty()
    assert_no_pumps()


@pytest.mark.parametrize("event_id", ["client-456", "provider-secret", "bad\nID"])
async def test_gateway_rejection_correlates_safe_client_commands(event_id):
    down, up = Socket(), Socket()
    down.incoming.put_nowait(json.dumps({"type": "unsupported.operation", "event_id": event_id}))
    with pytest.raises(RealtimeError) as caught:
        await start(down, up)
    event = caught.value.event()
    assert event["event_id"].startswith("event_")
    if event_id == "client-456":
        assert event["error"]["event_id"] == event_id
    else:
        assert "event_id" not in event["error"]
    assert up.outgoing.empty()
    assert_no_pumps()


async def test_transcription_failure_is_sanitized_on_the_relay():
    down, up = Socket(), Socket()
    task = start(down, up)
    up.incoming.put_nowait(
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "event_id": "server-123",
                "item_id": "item-123",
                "content_index": 0,
                "error": {"message": "provider-secret https://internal.example"},
            }
        )
    )
    event = await received(down)
    assert event["item_id"] == "item-123" and event["content_index"] == 0
    assert "provider-secret" not in json.dumps(event)
    assert "internal.example" not in json.dumps(event)
    down.incoming.put_nowait(SocketClosed())
    await task
    assert_no_pumps()


@pytest.mark.parametrize(
    "limits", [{"max_input_bytes": 10}, {"max_message_bytes": 10}, {"max_client_events": 1}]
)
async def test_input_allowances_bound_forwarded_work(limits):
    down, up = Socket(), Socket()
    for _ in range(2):
        down.incoming.put_nowait('{"type":"response.create"}')
    with pytest.raises(RealtimeError):
        await start(down, up, **limits)
    assert up.outgoing.qsize() <= (1 if "max_client_events" in limits else 0)
    assert_no_pumps()


async def test_invalid_upstream_json_is_a_sanitized_failure():
    down, up = Socket(), Socket()
    up.incoming.put_nowait("secret invalid data")
    with pytest.raises(RealtimeError) as caught:
        await start(down, up)
    assert caught.value.code == "invalid_upstream_event"
    assert "secret" not in str(caught.value)
    assert down.outgoing.empty()


@pytest.mark.parametrize(
    "limits,code",
    [
        ({"session_seconds": 0.02}, "session_timeout"),
        ({"idle_seconds": 0.02}, "idle_timeout"),
    ],
)
async def test_session_and_idle_deadlines_cancel_all_pumps(limits, code):
    with pytest.raises(RealtimeError) as caught:
        await asyncio.wait_for(start(Socket(), Socket(), **limits), 1)
    assert caught.value.code == code
    assert_no_pumps()


async def test_lost_lease_closes_session():
    permit = Permit(error=RealtimeError("lease_lost", "Lease lost"))
    with pytest.raises(RealtimeError, match="Lease lost"):
        await start(Socket(), Socket(), permit, health_seconds=0.01)
    assert permit.health_calls == 1
    assert_no_pumps()


async def test_slow_reader_applies_backpressure_and_write_deadline():
    class SlowReader(Socket):
        async def send_text(self, message):
            await asyncio.Event().wait()

    down, up = SlowReader(), Socket()
    for _ in range(20):
        up.incoming.put_nowait('{"type":"response.output_audio.delta","delta":"AA=="}')
    with pytest.raises(RealtimeError, match="timed out"):
        await start(down, up, write_seconds=0.02)
    assert up.receives == 1  # no drain into another unbounded gateway queue
    assert_no_pumps()


async def test_caller_cancellation_joins_both_directions():
    task = start(Socket(), Socket())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_no_pumps()


async def test_pump_join_has_a_cleanup_deadline():
    entered = asyncio.Event()

    class SlowExitSocket(Socket):
        async def receive_text(self):
            entered.set()
            try:
                return await super().receive_text()
            finally:
                await asyncio.Event().wait()

    task = start(SlowExitSocket(), Socket(), cleanup_seconds=0.02)
    await entered.wait()
    task.cancel()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, 1)
    assert_no_pumps()


async def test_local_gate_has_no_waiters_and_shutdown_drains_owned_tasks():
    runtime = RealtimeRuntime(admission=Admission(), limits=RealtimeLimits(max_connections=1))
    entered = asyncio.Event()

    async def owner():
        with runtime.reserve():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    with pytest.raises(RealtimeError, match="capacity"):
        with runtime.reserve():
            pytest.fail("capacity gate admitted an excess connection")
    await runtime.close()
    assert task.cancelled()
    assert runtime.active_sessions == 0
    with pytest.raises(RealtimeError):
        with runtime.reserve():
            pytest.fail("shutdown admitted a connection")


@pytest.mark.parametrize(
    "limits",
    [
        {"session_seconds": float("inf")},
        {"max_connections": True},
        {"max_message_bytes": 1.5},
        {"idle_seconds": 0},
    ],
)
def test_limits_cannot_be_unbounded_or_invalid(limits):
    with pytest.raises(ValueError):
        RealtimeLimits(**limits)
