from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.providers.grpc_channel import GrpcChannelManager, GRPC_AVAILABLE


class TestGrpcChannelManager:
    def test_init(self):
        manager = GrpcChannelManager(max_pool_size=4)
        assert manager.active_channels == 0
        assert manager._max_pool_size == 4

    @pytest.mark.asyncio
    async def test_close_all_empty(self):
        manager = GrpcChannelManager()
        await manager.close_all()
        assert manager.active_channels == 0

    @pytest.mark.asyncio
    async def test_close_channel_nonexistent(self):
        manager = GrpcChannelManager()
        await manager.close_channel("nonexistent:50051")
        assert manager.active_channels == 0

    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    @pytest.mark.asyncio
    async def test_get_channel_creates_new(self):
        manager = GrpcChannelManager()
        channel = await manager.get_channel("localhost:50051")
        assert channel is not None
        assert manager.active_channels == 1
        await manager.close_all()

    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    @pytest.mark.asyncio
    async def test_get_channel_reuses_existing(self):
        manager = GrpcChannelManager()
        ch1 = await manager.get_channel("localhost:50051")
        ch2 = await manager.get_channel("localhost:50051")
        assert ch1 is ch2
        assert manager.active_channels == 1
        await manager.close_all()

    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    @pytest.mark.asyncio
    async def test_pool_eviction(self):
        manager = GrpcChannelManager(max_pool_size=2)
        await manager.get_channel("host1:50051")
        await manager.get_channel("host2:50051")
        assert manager.active_channels == 2
        await manager.get_channel("host3:50051")
        assert manager.active_channels == 2
        await manager.close_all()

    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    @pytest.mark.asyncio
    async def test_close_specific_channel(self):
        manager = GrpcChannelManager()
        await manager.get_channel("localhost:50051")
        assert manager.active_channels == 1
        await manager.close_channel("localhost:50051")
        assert manager.active_channels == 0

    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    @pytest.mark.asyncio
    async def test_connectivity_check_timeout(self):
        manager = GrpcChannelManager()
        result = await manager.check_connectivity("192.0.2.1:50051", timeout=0.5)
        assert result is False
        await manager.close_all()
