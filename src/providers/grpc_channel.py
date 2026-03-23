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


_DEFAULT_KEEPALIVE_OPTIONS = [
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
    ("grpc.max_receive_message_length", 64 * 1024 * 1024),
    ("grpc.max_send_message_length", 64 * 1024 * 1024),
]


class GrpcChannelManager:
    def __init__(self, max_pool_size: int = 8) -> None:
        self._channels: dict[str, Any] = {}
        self._max_pool_size = max_pool_size
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

            options = list(_DEFAULT_KEEPALIVE_OPTIONS)
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
