"""Tests for callbacks system."""

import pytest
import json
import tempfile
import os
from datetime import datetime
from unittest.mock import Mock, patch

from deltallm.callbacks import Callback, CallbackManager, LoggingCallback
from deltallm.callbacks.base import RequestLog, RequestStatus
from deltallm.types import CompletionResponse, Usage
from datetime import timezone


class MockCallback(Callback):
    """Mock callback for testing."""
    
    def __init__(self):
        self.start_calls = []
        self.end_calls = []
        self.error_calls = []
    
    async def on_request_start(self, log: RequestLog) -> None:
        self.start_calls.append(log)
    
    async def on_request_end(self, log: RequestLog, response=None) -> None:
        self.end_calls.append((log, response))
    
    async def on_request_error(self, log: RequestLog, error: Exception) -> None:
        self.error_calls.append((log, error))


class TestRequestLog:
    """Test RequestLog dataclass."""

    def test_request_log_creation(self):
        """Test creating a request log."""
        log = RequestLog(
            request_id="req-123",
            timestamp=datetime.now(timezone.utc),
            api_key="hashed-key",
            model="gpt-4o",
        )
        
        assert log.request_id == "req-123"
        assert log.model == "gpt-4o"
        assert log.status == RequestStatus.STARTED
        assert log.prompt_tokens == 0

    def test_request_log_with_optional_fields(self):
        """Test creating a request log with optional fields."""
        log = RequestLog(
            request_id="req-123",
            timestamp=datetime.now(timezone.utc),
            api_key="hashed-key",
            model="gpt-4o",
            user_id="user-456",
            team_id="team-789",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            spend=0.002,
            latency_ms=500.0,
            ttft_ms=100.0,
            status=RequestStatus.SUCCESS,
            cache_hit=True,
        )
        
        assert log.user_id == "user-456"
        assert log.team_id == "team-789"
        assert log.total_tokens == 150
        assert log.spend == 0.002
        assert log.ttft_ms == 100.0
        assert log.cache_hit is True


class TestCallbackManager:
    """Test CallbackManager."""

    @pytest.fixture
    def manager(self):
        return CallbackManager()

    @pytest.fixture
    def sample_log(self):
        return RequestLog(
            request_id="req-123",
            timestamp=datetime.now(timezone.utc),
            api_key="key",
            model="gpt-4o",
        )

    async def test_register_callback(self, manager):
        """Test registering a callback."""
        callback = MockCallback()
        manager.register(callback)
        
        assert callback in manager._callbacks

    async def test_unregister_callback(self, manager):
        """Test unregistering a callback."""
        callback = MockCallback()
        manager.register(callback)
        manager.unregister(callback)
        
        assert callback not in manager._callbacks

    async def test_on_request_start(self, manager, sample_log):
        """Test on_request_start notifies callbacks."""
        callback1 = MockCallback()
        callback2 = MockCallback()
        
        manager.register(callback1)
        manager.register(callback2)
        
        await manager.on_request_start(sample_log)
        
        assert len(callback1.start_calls) == 1
        assert len(callback2.start_calls) == 1
        assert callback1.start_calls[0].request_id == "req-123"

    async def test_on_request_end(self, manager, sample_log):
        """Test on_request_end notifies callbacks."""
        callback = MockCallback()
        manager.register(callback)
        
        response = CompletionResponse(
            id="resp-123",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[],
        )
        
        await manager.on_request_end(sample_log, response)
        
        assert len(callback.end_calls) == 1
        assert callback.end_calls[0][1].id == "resp-123"

    async def test_on_request_error(self, manager, sample_log):
        """Test on_request_error notifies callbacks."""
        callback = MockCallback()
        manager.register(callback)
        
        error = ValueError("Test error")
        
        await manager.on_request_error(sample_log, error)
        
        assert len(callback.error_calls) == 1
        assert str(callback.error_calls[0][1]) == "Test error"

    async def test_callback_error_isolated(self, manager, sample_log):
        """Test that one callback's error doesn't affect others."""
        class ErrorCallback(Callback):
            async def on_request_start(self, log):
                raise ValueError("Callback error")
            async def on_request_end(self, log, response=None):
                pass
            async def on_request_error(self, log, error):
                pass
        
        error_callback = ErrorCallback()
        good_callback = MockCallback()
        
        manager.register(error_callback)
        manager.register(good_callback)
        
        # Should not raise
        await manager.on_request_start(sample_log)
        
        # Good callback should still be called
        assert len(good_callback.start_calls) == 1


class TestLoggingCallback:
    """Test LoggingCallback."""

    @pytest.fixture
    def sample_log(self):
        return RequestLog(
            request_id="req-123",
            timestamp=datetime.now(timezone.utc),
            api_key="hashed-api-key",
            model="gpt-4o",
            user_id="user-456",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            spend=0.002,
            latency_ms=500.0,
            status=RequestStatus.SUCCESS,
        )

    async def test_console_logging(self, sample_log, caplog):
        """Test console logging."""
        import structlog
        
        callback = LoggingCallback(console=True, file_path=None)
        
        with patch("deltallm.callbacks.logging_callback.logger") as mock_logger:
            await callback.on_request_start(sample_log)
            
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args.kwargs
            assert call_kwargs["request_id"] == "req-123"
            assert call_kwargs["model"] == "gpt-4o"

    async def test_file_logging(self, sample_log):
        """Test file logging."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_path = f.name
        
        try:
            callback = LoggingCallback(console=False, file_path=temp_path)
            
            await callback.on_request_start(sample_log)
            await callback.on_request_end(sample_log)
            
            callback.close()
            
            # Read log file
            with open(temp_path, "r") as f:
                lines = f.readlines()
            
            assert len(lines) == 2
            
            # Parse first line (request_start)
            data = json.loads(lines[0])
            assert data["event"] == "request_start"
            assert data["request_id"] == "req-123"
            assert data["model"] == "gpt-4o"
            
            # Parse second line (request_end)
            data = json.loads(lines[1])
            assert data["event"] == "request_end"
            assert data["tokens"] == 150
            assert data["spend"] == 0.002
        finally:
            os.unlink(temp_path)

    async def test_error_logging(self, sample_log):
        """Test error logging."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_path = f.name
        
        try:
            callback = LoggingCallback(console=False, file_path=temp_path)
            
            error = ValueError("Something went wrong")
            await callback.on_request_error(sample_log, error)
            
            callback.close()
            
            with open(temp_path, "r") as f:
                lines = f.readlines()
            
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["event"] == "request_error"
            assert data["error_type"] == "ValueError"
            assert data["error_message"] == "Something went wrong"
        finally:
            os.unlink(temp_path)

    async def test_message_truncation(self, sample_log):
        """Test message truncation."""
        callback = LoggingCallback(
            console=False,
            truncate_messages=True,
            max_message_length=10,
        )
        
        long_message = "a" * 1000
        truncated = callback._truncate_messages([{"content": long_message}])
        
        assert len(truncated[0]["content"]) < 1000
        assert "... [truncated]" in truncated[0]["content"]

    async def test_no_truncation_when_disabled(self, sample_log):
        """Test no truncation when disabled."""
        callback = LoggingCallback(
            console=False,
            truncate_messages=False,
            max_message_length=10,
        )
        
        long_message = "a" * 1000
        truncated = callback._truncate_messages([{"content": long_message}])
        
        assert truncated[0]["content"] == long_message

    def test_log_to_dict(self, sample_log):
        """Test conversion to dictionary."""
        callback = LoggingCallback(console=False)
        
        data = callback._log_to_dict(sample_log)
        
        assert data["request_id"] == "req-123"
        assert data["api_key"] == "hashed-api-key"
        assert data["model"] == "gpt-4o"
        assert data["user_id"] == "user-456"
        assert data["tokens"] == 150
        assert data["spend"] == 0.002
        assert data["latency_ms"] == 500.0

    def test_log_to_dict_minimal(self):
        """Test conversion with minimal fields."""
        log = RequestLog(
            request_id="req-456",
            timestamp=datetime.now(timezone.utc),
            api_key="key",
            model="gpt-4o",
        )
        
        callback = LoggingCallback(console=False)
        data = callback._log_to_dict(log)
        
        # Required fields
        assert data["request_id"] == "req-456"
        assert data["api_key"] == "key"
        assert data["model"] == "gpt-4o"
        
        # Optional fields should not be present if None
        assert "user_id" not in data
        assert "team_id" not in data
        assert "error_type" not in data
