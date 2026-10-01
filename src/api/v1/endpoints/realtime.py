from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketState

from src.middleware.auth import authenticate_request
from src.models.errors import ProxyError
from src.models.responses import UserAPIKeyAuth
from src.realtime.contracts import RealtimeError, RealtimeRequest, SocketClosed
from src.realtime.lifecycle import RealtimeDrain
from src.realtime.runtime import RealtimeRuntime
from src.realtime.session import relay_session
from src.telemetry.event_identity import get_or_create_billing_event_id

router = APIRouter(prefix="/v1", tags=["realtime"])


class _DownstreamSocket:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def receive_text(self) -> str:
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise SocketClosed
        text = message.get("text")
        if not isinstance(text, str):
            raise RealtimeError(
                "unsupported_frame", "Realtime requires JSON text frames", close_code=1003
            )
        return text

    async def send_text(self, message: str) -> None:
        try:
            await self.websocket.send_text(message)
        except (WebSocketDisconnect, OSError) as exc:
            raise SocketClosed from exc


def _request(websocket: WebSocket, auth: UserAPIKeyAuth) -> RealtimeRequest:
    if len(websocket.scope.get("query_string", b"")) > 2048:
        raise RealtimeError("invalid_query", "Realtime query is too long")
    params = websocket.query_params
    if any(key not in {"model", "intent"} or len(params.getlist(key)) != 1 for key in params):
        raise RealtimeError("invalid_query", "Unsupported Realtime query parameter")
    intent = params.get("intent")
    if intent not in {None, "transcription"}:
        raise RealtimeError("invalid_intent", "Realtime intent is not supported")
    model = params.get("model")
    if model is not None and (not model.strip() or len(model) > 256):
        raise RealtimeError("invalid_model", "Invalid Realtime model")
    if intent is None and model is None:
        raise RealtimeError("missing_model", "Realtime requires a model")
    return RealtimeRequest(
        session_id=get_or_create_billing_event_id(websocket),
        model=model,
        profile="transcription" if intent else "realtime",
        auth=auth,
    )


async def _run(websocket: WebSocket, runtime: RealtimeRuntime, drain: RealtimeDrain) -> None:
    # Each resource exit shares the drain deadline, separate from handshake
    # and session lifetime. An upstream close failure still unwinds admission.
    stack = AsyncExitStack()
    try:
        async with asyncio.timeout(runtime.limits.handshake_seconds):
            if websocket.headers.get("origin") or websocket.scope.get("subprotocols"):
                raise RealtimeError(
                    "unsupported_auth", "Realtime requires server-to-server bearer authentication"
                )
            if websocket.headers.get("openai-beta"):
                raise RealtimeError(
                    "unsupported_protocol", "Only the GA Realtime protocol is supported"
                )
            auth = await authenticate_request(websocket)
            request = _request(websocket, auth)
            admitted = await drain.enter_context(stack, runtime.admission.admit(request))
            if admitted.target.profile != request.profile or (
                request.model is not None and admitted.target.public_model != request.model
            ):
                raise RealtimeError(
                    "invalid_admission",
                    "Realtime admission did not match the request",
                    close_code=1011,
                )
            await admitted.permit.check_health()
            upstream = await drain.enter_context(
                stack, runtime.connector.open(admitted.target, runtime.limits)
            )
            await websocket.accept()
        await relay_session(
            _DownstreamSocket(websocket),
            upstream,
            target=admitted.target,
            permit=admitted.permit,
            limits=runtime.limits,
            drain=drain,
        )
    except BaseException:
        import sys

        await stack.__aexit__(*sys.exc_info())
        raise
    else:
        await stack.aclose()


async def _error(websocket: WebSocket, error: RealtimeError, status: int) -> None:
    if websocket.application_state is WebSocketState.CONNECTED:
        await websocket.send_json(error.event())
        await websocket.close(code=error.close_code)
    elif "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(JSONResponse(error.event(), status_code=status))
    else:
        # ASGI servers without the denial extension translate this to HTTP 403.
        await websocket.close(code=1008)


async def _serve_reserved(
    websocket: WebSocket, runtime: RealtimeRuntime, drain: RealtimeDrain
) -> None:
    error = None
    status = 503
    close_code = 1000
    try:
        await _run(websocket, runtime, drain)
    except HTTPException as exc:
        status = exc.status_code
        error = RealtimeError("authentication_failed", "Realtime authentication failed")
    except ProxyError as exc:
        status = exc.status_code
        error = RealtimeError.admission_denied()
    except RealtimeError as exc:
        error = exc
        status = 429 if exc.code == "capacity_exceeded" else 400
    except (WebSocketDisconnect, SocketClosed):
        pass
    except asyncio.CancelledError:
        close_code = 1012
        raise
    except Exception:
        error = RealtimeError(
            "realtime_unavailable", "Realtime connection could not continue", close_code=1011
        )
    finally:
        with suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            async with drain.close_socket():
                if error is not None:
                    await _error(websocket, error, status)
                elif websocket.application_state is WebSocketState.CONNECTED:
                    await websocket.close(code=close_code)


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    runtime = getattr(websocket.app.state, "realtime_runtime", None)
    # No bootstrap owner is installed until durable admission is qualified.
    # A model declaration or provider credentials cannot enable this route.
    if not isinstance(runtime, RealtimeRuntime):
        async with asyncio.timeout(5):
            await _error(
                websocket, RealtimeError("realtime_unavailable", "Realtime is not enabled"), 503
            )
        return
    try:
        with runtime.reserve() as drain:
            await _serve_reserved(websocket, runtime, drain)
    except RealtimeError as exc:
        with suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            async with asyncio.timeout(runtime.limits.write_seconds):
                await _error(websocket, exc, 429)
