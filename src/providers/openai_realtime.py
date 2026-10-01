from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlencode, urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.realtime.contracts import RealtimeError, RealtimeLimits, RealtimeProfile, SocketClosed
from src.upstream_auth import build_openai_compatible_auth_headers


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeTarget:
    public_model: str
    upstream_model: str
    profile: RealtimeProfile
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


def resolve_realtime_target(
    params: Mapping[str, object], *, public_model: str, profile: RealtimeProfile
) -> OpenAIRealtimeTarget:
    """Resolve a pinned server-owned deployment, never client connection parameters.

    Only the public OpenAI origin is supported by this runtime.
    Alternate origins need the gateway's egress policy before being enabled.
    HTTP compatibility alone is not proof of Realtime compatibility.
    """
    if resolve_provider(params) != "openai":
        raise RealtimeError("unsupported_provider", "Realtime requires an OpenAI deployment")
    base = urlsplit(str(params.get("api_base") or "https://api.openai.com/v1"))
    if (
        base.scheme != "https"
        or base.netloc != "api.openai.com"
        or base.path.rstrip("/") != "/v1"
        or base.query
        or base.fragment
    ):
        raise RealtimeError("unsupported_endpoint", "Realtime endpoint is not qualified")
    model = resolve_upstream_model(params)
    key = params.get("api_key")
    if not model or not isinstance(key, str) or not key.strip():
        raise RealtimeError("invalid_deployment", "Realtime deployment is not configured")
    if profile not in {"realtime", "transcription"}:
        raise RealtimeError("unsupported_profile", "Realtime profile is not supported")
    query = {"model": model} if profile == "realtime" else {"intent": "transcription"}
    return OpenAIRealtimeTarget(
        public_model=public_model,
        upstream_model=model,
        profile=profile,
        url="wss://api.openai.com/v1/realtime?" + urlencode(query),
        headers=build_openai_compatible_auth_headers(provider="openai", api_key=key),
    )


class OpenAIRealtimeSocket:
    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    async def receive_text(self) -> str:
        try:
            message = await self._connection.recv()
        except ConnectionClosedError as exc:
            raise RealtimeError(
                "upstream_disconnected",
                "The upstream connection ended unexpectedly",
                close_code=1011,
            ) from exc
        except ConnectionClosed as exc:
            raise SocketClosed from exc
        if not isinstance(message, str):
            raise RealtimeError("invalid_upstream_event", "Invalid upstream event", close_code=1011)
        return message

    async def send_text(self, message: str) -> None:
        try:
            await self._connection.send(message)
        except ConnectionClosedError as exc:
            raise RealtimeError(
                "upstream_disconnected",
                "The upstream connection ended unexpectedly",
                close_code=1011,
            ) from exc
        except ConnectionClosed as exc:
            raise SocketClosed from exc


class OpenAIRealtimeConnector:
    @asynccontextmanager
    async def open(
        self, target: OpenAIRealtimeTarget, limits: RealtimeLimits
    ) -> AsyncIterator[OpenAIRealtimeSocket]:
        # connect's async iterator retries; use its single-attempt context
        # manager instead. Redirects must not carry provider credentials.
        class NoRedirectConnect(connect):
            def process_redirect(self, exc: Exception) -> Exception:
                return exc

        async with NoRedirectConnect(
            target.url,
            additional_headers=dict(target.headers),
            open_timeout=limits.handshake_seconds,
            close_timeout=limits.cleanup_seconds,
            max_size=limits.max_message_bytes,
            max_queue=4,
            write_limit=32 * 1024,
            compression=None,
            proxy=None,
            ping_interval=20,
            ping_timeout=20,
        ) as connection:
            yield OpenAIRealtimeSocket(connection)
