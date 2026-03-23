from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import grpc
    import grpc.aio

    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False
    grpc = None  # type: ignore[assignment]


DEFAULT_KEEPALIVE_TIME_MS = 30_000
DEFAULT_KEEPALIVE_TIMEOUT_MS = 10_000
DEFAULT_MAX_MESSAGE_LENGTH = 64 * 1024 * 1024
DEFAULT_MAX_POOL_SIZE = 8


def _build_channel_options(
    keepalive_time_ms: int = DEFAULT_KEEPALIVE_TIME_MS,
    keepalive_timeout_ms: int = DEFAULT_KEEPALIVE_TIMEOUT_MS,
    max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH,
) -> list[tuple[str, Any]]:
    return [
        ("grpc.keepalive_time_ms", keepalive_time_ms),
        ("grpc.keepalive_timeout_ms", keepalive_timeout_ms),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.max_receive_message_length", max_message_length),
        ("grpc.max_send_message_length", max_message_length),
    ]


class GrpcChannelManager:
    def __init__(
        self,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        keepalive_time_ms: int = DEFAULT_KEEPALIVE_TIME_MS,
        keepalive_timeout_ms: int = DEFAULT_KEEPALIVE_TIMEOUT_MS,
        max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH,
    ) -> None:
        self._channels: dict[str, Any] = {}
        self._max_pool_size = max_pool_size
        self._keepalive_time_ms = keepalive_time_ms
        self._keepalive_timeout_ms = keepalive_timeout_ms
        self._max_message_length = max_message_length
        self._lock = asyncio.Lock()

    def _ensure_available(self) -> None:
        if not GRPC_AVAILABLE:
            raise RuntimeError(
                "grpcio is not installed. Install it with: pip install grpcio grpcio-tools"
            )

    async def get_channel(
        self,
        address: str,
        *,
        use_tls: bool = False,
        extra_options: list[tuple[str, Any]] | None = None,
    ) -> Any:
        self._ensure_available()
        if address in self._channels:
            return self._channels[address]

        async with self._lock:
            if address in self._channels:
                return self._channels[address]

            if len(self._channels) >= self._max_pool_size:
                oldest_key = next(iter(self._channels))
                old_channel = self._channels.pop(oldest_key)
                await old_channel.close()
                logger.info("Evicted gRPC channel for %s (pool full)", oldest_key)

            options = _build_channel_options(
                keepalive_time_ms=self._keepalive_time_ms,
                keepalive_timeout_ms=self._keepalive_timeout_ms,
                max_message_length=self._max_message_length,
            )
            if extra_options:
                options.extend(extra_options)

            if use_tls:
                credentials = grpc.ssl_channel_credentials()
                channel = grpc.aio.secure_channel(address, credentials, options=options)
            else:
                channel = grpc.aio.insecure_channel(address, options=options)

            self._channels[address] = channel
            logger.info("Created gRPC channel for %s (tls=%s)", address, use_tls)
            return channel

    async def close_channel(self, address: str) -> None:
        async with self._lock:
            channel = self._channels.pop(address, None)
            if channel is not None:
                await channel.close()
                logger.info("Closed gRPC channel for %s", address)

    async def close_all(self) -> None:
        async with self._lock:
            for address, channel in self._channels.items():
                try:
                    await channel.close()
                except Exception:
                    logger.warning("Error closing gRPC channel for %s", address, exc_info=True)
            self._channels.clear()
            logger.info("All gRPC channels closed")

    @property
    def active_channels(self) -> int:
        return len(self._channels)

    async def check_connectivity(self, address: str, timeout: float = 5.0) -> bool:
        self._ensure_available()
        try:
            channel = await self.get_channel(address)
            state = channel.get_state(try_to_connect=True)
            if state == grpc.ChannelConnectivity.READY:
                return True
            await asyncio.wait_for(
                channel.channel_ready(),
                timeout=timeout,
            )
            return True
        except (asyncio.TimeoutError, Exception):
            return False
