"""Integration tests for spend tracking in API endpoints.

These tests verify that spend logs are properly created when API requests are made.
"""

import pytest
from decimal import Decimal
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, MagicMock, patch
import time


class TestSpendTrackingIntegration:
    """Test spend tracking integration in API endpoints."""
    
    @pytest.fixture
    async def mock_db_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.add = AsyncMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock()
        return session
    
    @pytest.fixture
    def mock_budget_tracker(self):
        """Create a mock budget tracker."""
        tracker = AsyncMock()
        tracker.record_spend = AsyncMock(return_value=MagicMock(id=uuid4()))
        return tracker
    
    @pytest.mark.asyncio
    async def test_spend_tracking_context_success(self, mock_db_session):
        """Test SpendTrackingContext records successful request."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        # Create mock request and auth context
        mock_request = MagicMock()
        mock_request.app.state.db_session = mock_db_session
        
        key_info = MagicMock()
        key_info.id = uuid4()
        key_info.team_id = uuid4()
        key_info.org_id = uuid4()
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=key_info.org_id,
            team_id=key_info.team_id,
            user_id=None,
        )
        
        # Use the context manager
        async with SpendTrackingContext(
            request=mock_request,
            auth_context=auth_context,
            model="gpt-4o",
            endpoint_type="chat",
            db_session=mock_db_session,
        ) as tracker:
            await tracker.record_success(
                cost=Decimal("0.015"),
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                provider="openai",
            )
        
        # Verify BudgetTracker.record_spend was called
        # Note: Since we're using a real BudgetTracker, we verify the db session was used
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called()
    
    @pytest.mark.asyncio
    async def test_spend_tracking_context_failure(self, mock_db_session):
        """Test SpendTrackingContext records failed request."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        # Create mock request and auth context
        mock_request = MagicMock()
        mock_request.app.state.db_session = mock_db_session
        
        key_info = MagicMock()
        key_info.id = uuid4()
        key_info.team_id = None
        key_info.org_id = None
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=None,
            team_id=None,
            user_id=None,
        )
        
        # Use the context manager and record a failure
        async with SpendTrackingContext(
            request=mock_request,
            auth_context=auth_context,
            model="gpt-4o",
            endpoint_type="chat",
            db_session=mock_db_session,
        ) as tracker:
            await tracker.record_failure(
                error_message="Rate limit exceeded",
                cost=Decimal("0"),
            )
        
        # Verify db session was used
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called()
    
    @pytest.mark.asyncio
    async def test_spend_tracking_context_exception(self, mock_db_session):
        """Test SpendTrackingContext handles exceptions in context."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        mock_request = MagicMock()
        mock_request.app.state.db_session = mock_db_session
        
        key_info = MagicMock()
        key_info.id = uuid4()
        key_info.team_id = None
        key_info.org_id = None
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=None,
            team_id=None,
            user_id=None,
        )
        
        # Test that exception is raised but failure is recorded
        with pytest.raises(ValueError, match="Test error"):
            async with SpendTrackingContext(
                request=mock_request,
                auth_context=auth_context,
                model="gpt-4o",
                endpoint_type="chat",
                db_session=mock_db_session,
            ) as tracker:
                raise ValueError("Test error")
        
        # Verify failure was recorded
        mock_db_session.add.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_spend_tracking_no_db_session(self):
        """Test SpendTrackingContext handles missing DB session gracefully."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        # Create mock request without db_session
        mock_request = MagicMock()
        mock_key_manager = MagicMock()
        mock_key_manager.update_spend = MagicMock()
        mock_request.app.state.key_manager = mock_key_manager
        
        key_info = MagicMock()
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=None,
            team_id=None,
            user_id=None,
        )
        
        # Should not raise even without db session
        async with SpendTrackingContext(
            request=mock_request,
            auth_context=auth_context,
            model="gpt-4o",
            endpoint_type="chat",
        ) as tracker:
            await tracker.record_success(
                cost=Decimal("0.015"),
                prompt_tokens=100,
                completion_tokens=50,
            )
        
        # Should fall back to key_manager
        mock_key_manager.update_spend.assert_called_once_with("test-hash", 0.015)
    
    @pytest.mark.asyncio
    async def test_spend_tracking_audio_speech(self, mock_db_session):
        """Test spend tracking for TTS/audio speech endpoint."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        mock_request = MagicMock()
        mock_request.app.state.db_session = mock_db_session
        
        key_info = MagicMock()
        key_info.id = uuid4()
        key_info.team_id = uuid4()
        key_info.org_id = uuid4()
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=key_info.org_id,
            team_id=key_info.team_id,
            user_id=None,
        )
        
        async with SpendTrackingContext(
            request=mock_request,
            auth_context=auth_context,
            model="tts-1",
            endpoint_type="audio_speech",
            db_session=mock_db_session,
        ) as tracker:
            await tracker.record_success(
                cost=Decimal("0.00018"),  # 12 chars * $0.000015
                audio_characters=12,
                latency_ms=150.0,
            )
        
        # Verify db session was used
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called()
    
    @pytest.mark.asyncio
    async def test_spend_tracking_audio_transcription(self, mock_db_session):
        """Test spend tracking for STT/audio transcription endpoint."""
        from deltallm.proxy.spend_tracking import SpendTrackingContext
        from deltallm.proxy.dependencies import AuthContext
        
        mock_request = MagicMock()
        mock_request.app.state.db_session = mock_db_session
        
        key_info = MagicMock()
        key_info.id = uuid4()
        key_info.team_id = uuid4()
        key_info.org_id = uuid4()
        key_info.key_hash = "test-hash"
        
        auth_context = AuthContext(
            api_key="test-key",
            key_info=key_info,
            org_id=key_info.org_id,
            team_id=key_info.team_id,
            user_id=None,
        )
        
        async with SpendTrackingContext(
            request=mock_request,
            auth_context=auth_context,
            model="whisper-1",
            endpoint_type="audio_transcription",
            db_session=mock_db_session,
        ) as tracker:
            await tracker.record_success(
                cost=Decimal("0.006"),  # 1 minute * $0.006
                audio_seconds=60.0,
                latency_ms=500.0,
            )
        
        # Verify db session was used
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called()


class TestSpendTrackingCallback:
    """Test SpendTrackingCallback."""
    
    @pytest.fixture
    def mock_budget_tracker(self):
        """Create a mock budget tracker."""
        tracker = AsyncMock()
        tracker.record_spend = AsyncMock(return_value=MagicMock(id=uuid4()))
        return tracker
    
    @pytest.mark.asyncio
    async def test_callback_on_request_end_chat(self, mock_budget_tracker):
        """Test callback records chat request."""
        from deltallm.callbacks.spend_tracking_callback import SpendTrackingCallback
        from deltallm.callbacks.base import RequestLog, RequestStatus
        from datetime import datetime
        
        callback = SpendTrackingCallback(mock_budget_tracker)
        
        log = RequestLog(
            request_id="req-123",
            timestamp=datetime.utcnow(),
            api_key="test-hash",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            spend=0.015,
            latency_ms=500.0,
            status=RequestStatus.SUCCESS,
            metadata={"api_key_id": str(uuid4()), "org_id": str(uuid4())},
        )
        
        await callback.on_request_end(log)
        
        # Verify record_spend was called
        mock_budget_tracker.record_spend.assert_called_once()
        call_args = mock_budget_tracker.record_spend.call_args
        assert call_args.kwargs["model"] == "gpt-4o"
        assert call_args.kwargs["endpoint_type"] == "chat"
        assert call_args.kwargs["prompt_tokens"] == 100
        assert call_args.kwargs["completion_tokens"] == 50
    
    @pytest.mark.asyncio
    async def test_callback_on_request_end_tts(self, mock_budget_tracker):
        """Test callback records TTS request."""
        from deltallm.callbacks.spend_tracking_callback import SpendTrackingCallback
        from deltallm.callbacks.base import RequestLog, RequestStatus
        from datetime import datetime
        
        callback = SpendTrackingCallback(mock_budget_tracker)
        
        log = RequestLog(
            request_id="req-456",
            timestamp=datetime.utcnow(),
            api_key="test-hash",
            model="tts-1",
            spend=0.00018,
            latency_ms=150.0,
            status=RequestStatus.SUCCESS,
            metadata={"audio_characters": 12},
        )
        
        await callback.on_request_end(log)
        
        call_args = mock_budget_tracker.record_spend.call_args
        assert call_args.kwargs["model"] == "tts-1"
        assert call_args.kwargs["endpoint_type"] == "audio_speech"
        assert call_args.kwargs["audio_characters"] == 12
    
    @pytest.mark.asyncio
    async def test_callback_on_request_error(self, mock_budget_tracker):
        """Test callback records failed request."""
        from deltallm.callbacks.spend_tracking_callback import SpendTrackingCallback
        from deltallm.callbacks.base import RequestLog, RequestStatus
        from datetime import datetime
        
        callback = SpendTrackingCallback(mock_budget_tracker)
        
        log = RequestLog(
            request_id="req-789",
            timestamp=datetime.utcnow(),
            api_key="test-hash",
            model="gpt-4o",
            spend=0.0,
            latency_ms=100.0,
            status=RequestStatus.ERROR,
            error_message="Rate limit exceeded",
            metadata={},
        )
        
        error = Exception("Rate limit exceeded")
        await callback.on_request_error(log, error)
        
        call_args = mock_budget_tracker.record_spend.call_args
        assert call_args.kwargs["status"] == "failure"
        assert call_args.kwargs["error_message"] == "Rate limit exceeded"
    
    @pytest.mark.asyncio
    async def test_callback_infer_endpoint_type_from_model(self, mock_budget_tracker):
        """Test callback infers endpoint type from model name."""
        from deltallm.callbacks.spend_tracking_callback import SpendTrackingCallback
        from deltallm.callbacks.base import RequestLog, RequestStatus
        from datetime import datetime
        
        callback = SpendTrackingCallback(mock_budget_tracker)
        
        test_cases = [
            ("tts-1", "audio_speech"),
            ("tts-1-hd", "audio_speech"),
            ("whisper-1", "audio_transcription"),
            ("text-embedding-3-small", "embedding"),
            ("dall-e-3", "image"),
            ("gpt-4o", "chat"),
            ("claude-3-opus", "chat"),
        ]
        
        for model, expected_endpoint in test_cases:
            mock_budget_tracker.reset_mock()
            
            log = RequestLog(
                request_id=f"req-{model}",
                timestamp=datetime.utcnow(),
                api_key="test-hash",
                model=model,
                spend=0.01,
                latency_ms=100.0,
                status=RequestStatus.SUCCESS,
                metadata={},
            )
            
            await callback.on_request_end(log)
            
            call_args = mock_budget_tracker.record_spend.call_args
            assert call_args.kwargs["endpoint_type"] == expected_endpoint, f"Failed for model {model}"


class TestBudgetTrackerExtended:
    """Extended tests for BudgetTracker with new fields."""
    
    @pytest.mark.asyncio
    async def test_record_spend_with_endpoint_type(self, db_session):
        """Test recording spend with endpoint_type."""
        from deltallm.budget.tracker import BudgetTracker
        from deltallm.db.models import APIKey
        from decimal import Decimal
        from uuid import uuid4
        
        # Create test API key
        key = APIKey(
            id=uuid4(),
            key_hash="test-endpoint-type",
            spend=Decimal("0"),
        )
        db_session.add(key)
        await db_session.commit()
        
        tracker = BudgetTracker(db_session)
        
        spend_log = await tracker.record_spend(
            request_id="req-endpoint-001",
            api_key_id=key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="tts-1",
            endpoint_type="audio_speech",
            cost=Decimal("0.00018"),
            audio_characters=12,
            status="success",
        )
        
        assert spend_log.endpoint_type == "audio_speech"
        assert spend_log.audio_characters == 12
        assert spend_log.model == "tts-1"
    
    @pytest.mark.asyncio
    async def test_record_spend_with_image_fields(self, db_session):
        """Test recording spend with image generation fields."""
        from deltallm.budget.tracker import BudgetTracker
        from deltallm.db.models import APIKey
        from decimal import Decimal
        from uuid import uuid4
        
        key = APIKey(
            id=uuid4(),
            key_hash="test-image",
            spend=Decimal("0"),
        )
        db_session.add(key)
        await db_session.commit()
        
        tracker = BudgetTracker(db_session)
        
        spend_log = await tracker.record_spend(
            request_id="req-image-001",
            api_key_id=key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="dall-e-3",
            endpoint_type="image",
            cost=Decimal("0.04"),
            image_count=1,
            image_size="1024x1024",
            status="success",
        )
        
        assert spend_log.endpoint_type == "image"
        assert spend_log.image_count == 1
        assert spend_log.image_size == "1024x1024"
    
    @pytest.mark.asyncio
    async def test_record_spend_with_audio_seconds(self, db_session):
        """Test recording spend with audio duration."""
        from deltallm.budget.tracker import BudgetTracker
        from deltallm.db.models import APIKey
        from decimal import Decimal
        from uuid import uuid4
        
        key = APIKey(
            id=uuid4(),
            key_hash="test-audio",
            spend=Decimal("0"),
        )
        db_session.add(key)
        await db_session.commit()
        
        tracker = BudgetTracker(db_session)
        
        spend_log = await tracker.record_spend(
            request_id="req-audio-001",
            api_key_id=key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="whisper-1",
            endpoint_type="audio_transcription",
            cost=Decimal("0.006"),
            audio_seconds=60.0,
            status="success",
        )
        
        assert spend_log.endpoint_type == "audio_transcription"
        assert spend_log.audio_seconds == 60.0
