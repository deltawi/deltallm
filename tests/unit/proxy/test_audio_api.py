"""Tests for Audio API routes.

This module tests:
- Text-to-Speech (TTS): /v1/audio/speech
- Speech-to-Text (STT): /v1/audio/transcriptions
- Audio Translation: /v1/audio/translations

All tests verify cost tracking and response headers.
"""

import io
import json
import pytest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock, patch, MagicMock

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from deltallm.pricing import PricingManager, CostCalculator, PricingConfig
from deltallm.proxy.routes.audio import router as audio_router
from deltallm.proxy.schemas_audio import (
    AudioSpeechRequest,
    AudioTranscriptionResponse,
    AudioTranslationResponse,
)


# Mock authentication dependency
def mock_require_auth():
    """Mock auth context."""
    key_info = Mock()
    key_info.key_hash = "test_key_hash"
    key_info.models = None  # Allow all models
    key_info.max_budget = None  # No budget limit
    key_info.spend = 0.0
    key_info.user_id = str(uuid4())
    key_info.org_id = None
    key_info.team_id = None
    
    auth_context = Mock()
    auth_context.key_info = key_info
    auth_context.user = None
    auth_context.is_master_key = False
    
    return auth_context


@pytest.fixture
def app():
    """Create test app with audio routes."""
    app = FastAPI()
    
    # Set up pricing manager and calculator
    pricing_manager = PricingManager(enable_hot_reload=False)
    cost_calculator = CostCalculator(pricing_manager)
    
    # Set up mock key manager
    key_manager = Mock()
    key_manager.update_spend = Mock()
    
    app.state.pricing_manager = pricing_manager
    app.state.cost_calculator = cost_calculator
    app.state.key_manager = key_manager
    
    # Override the auth dependency
    app.dependency_overrides = {}
    
    app.include_router(audio_router, prefix="/v1")
    
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def authenticated_client(app):
    """Create authenticated test client."""
    from deltallm.proxy.dependencies import require_auth
    
    # Mock the require_auth dependency
    app.dependency_overrides[require_auth] = mock_require_auth
    
    return TestClient(app)


# ========== TTS Tests ==========


class TestTTSEndpoint:
    """Tests for POST /v1/audio/speech (Text-to-Speech)."""
    
    def test_tts_success_default_params(self, authenticated_client):
        """Test TTS with default parameters."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "input": "Hello, world!",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "audio/mpeg"
        
        # Check cost headers
        assert "X-ProxyLLM-Response-Cost" in response.headers
        assert "X-ProxyLLM-Model-Used" in response.headers
        assert response.headers["X-ProxyLLM-Model-Used"] == "tts-1"
        assert response.headers["X-ProxyLLM-Endpoint-Type"] == "tts"
        
        # Verify cost calculation (13 characters * $0.000015)
        cost = Decimal(response.headers["X-ProxyLLM-Response-Cost"])
        assert cost > 0
    
    def test_tts_success_hd_model(self, authenticated_client):
        """Test TTS with tts-1-hd model."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1-hd",
                "input": "Hello, world!",
                "voice": "nova",
                "response_format": "mp3",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["X-ProxyLLM-Model-Used"] == "tts-1-hd"
        
        # HD model costs more (13 characters * $0.000030)
        cost = Decimal(response.headers["X-ProxyLLM-Response-Cost"])
        assert cost > 0
    
    def test_tts_all_voices(self, authenticated_client):
        """Test TTS with all supported voices."""
        voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        
        for voice in voices:
            response = authenticated_client.post(
                "/v1/audio/speech",
                json={
                    "input": "Test voice.",
                    "voice": voice,
                }
            )
            assert response.status_code == status.HTTP_200_OK, f"Failed for voice: {voice}"
    
    def test_tts_all_formats(self, authenticated_client):
        """Test TTS with all supported response formats."""
        formats = {
            "mp3": "audio/mpeg",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
            "pcm": "audio/pcm",
        }
        
        for fmt, content_type in formats.items():
            response = authenticated_client.post(
                "/v1/audio/speech",
                json={
                    "input": "Test format.",
                    "response_format": fmt,
                }
            )
            assert response.status_code == status.HTTP_200_OK, f"Failed for format: {fmt}"
            assert response.headers["content-type"] == content_type, f"Wrong content type for format: {fmt}"
    
    def test_tts_speed_range(self, authenticated_client):
        """Test TTS with various speed values."""
        speeds = [0.25, 0.5, 1.0, 2.0, 4.0]
        
        for speed in speeds:
            response = authenticated_client.post(
                "/v1/audio/speech",
                json={
                    "input": "Test speed.",
                    "speed": speed,
                }
            )
            assert response.status_code == status.HTTP_200_OK, f"Failed for speed: {speed}"
    
    def test_tts_invalid_speed(self, authenticated_client):
        """Test TTS with invalid speed value."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "input": "Test.",
                "speed": 5.0,  # Too high (max 4.0)
            }
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_tts_invalid_model(self, authenticated_client):
        """Test TTS with unsupported model."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "model": "invalid-model",
                "input": "Test.",
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not support text-to-speech" in response.json()["detail"]
    
    def test_tts_empty_input(self, authenticated_client):
        """Test TTS with empty input."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "input": "",
            }
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_tts_input_too_long(self, authenticated_client):
        """Test TTS with input exceeding max length."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "input": "A" * 5000,  # Max is 4096
            }
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_tts_missing_input(self, authenticated_client):
        """Test TTS without required input field."""
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
            }
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_tts_unauthorized(self, app):
        """Test TTS without authentication."""
        from deltallm.proxy.dependencies import require_auth
        
        # Remove the auth override to test unauthenticated access
        app.dependency_overrides.pop(require_auth, None)
        
        # Mock require_auth to raise 401
        def mock_require_auth_unauthorized():
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )
        
        app.dependency_overrides[require_auth] = mock_require_auth_unauthorized
        
        client = TestClient(app)
        response = client.post(
            "/v1/audio/speech",
            json={
                "input": "Hello.",
            }
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
    
    def test_tts_model_not_allowed(self, authenticated_client, app):
        """Test TTS with model not in allowed list."""
        # Update mock to restrict models
        auth_context = mock_require_auth()
        auth_context.key_info.models = ["gpt-4o"]  # Only allow gpt-4o
        
        from deltallm.proxy.dependencies import require_auth
        app.dependency_overrides[require_auth] = lambda: auth_context
        
        client = TestClient(app)
        response = client.post(
            "/v1/audio/speech",
            json={
                "input": "Hello.",
            }
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "not allowed for this key" in response.json()["detail"]
    
    def test_tts_cost_calculation_accuracy(self, authenticated_client):
        """Test TTS cost calculation is accurate."""
        test_input = "Hello, world!"  # 13 characters
        expected_cost = Decimal("13") * Decimal("0.000015")  # tts-1 rate
        
        response = authenticated_client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": test_input,
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        actual_cost = Decimal(response.headers["X-ProxyLLM-Response-Cost"])
        assert actual_cost == expected_cost.quantize(Decimal("0.000001"))


# ========== STT Tests ==========


class TestSTTEndpoint:
    """Tests for POST /v1/audio/transcriptions (Speech-to-Text)."""
    
    def test_stt_success_default_params(self, authenticated_client):
        """Test STT with default parameters."""
        # Create a fake audio file
        audio_content = b"fake mp3 audio content" * 1000  # Make it big enough
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        
        # Parse response based on format
        data = response.json()
        assert "text" in data
        
        # Check cost headers
        assert "X-ProxyLLM-Response-Cost" in response.headers
        assert "X-ProxyLLM-Model-Used" in response.headers
        assert response.headers["X-ProxyLLM-Model-Used"] == "whisper-1"
        assert response.headers["X-ProxyLLM-Endpoint-Type"] == "stt"
    
    def test_stt_with_language(self, authenticated_client):
        """Test STT with language specification."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "language": "en",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_stt_text_format(self, authenticated_client):
        """Test STT with text response format."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "response_format": "text",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
    
    def test_stt_srt_format(self, authenticated_client):
        """Test STT with SRT subtitle format."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "response_format": "srt",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert "-->" in response.text  # SRT format indicator
    
    def test_stt_vtt_format(self, authenticated_client):
        """Test STT with VTT subtitle format."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "response_format": "vtt",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_stt_verbose_json_format(self, authenticated_client):
        """Test STT with verbose JSON format."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "response_format": "verbose_json",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "task" in data
        assert "language" in data
        assert "duration" in data
        assert "text" in data
    
    def test_stt_with_prompt(self, authenticated_client):
        """Test STT with prompt."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "prompt": "Transcribe this technical discussion.",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_stt_invalid_model(self, authenticated_client):
        """Test STT with unsupported model."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "invalid-model",
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not support speech-to-text" in response.json()["detail"]
    
    def test_stt_no_file(self, authenticated_client):
        """Test STT without file."""
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_stt_empty_file(self, authenticated_client):
        """Test STT with empty file."""
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(b""), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Empty file" in response.json()["detail"]
    
    def test_stt_file_too_large(self, authenticated_client):
        """Test STT with file exceeding size limit."""
        # Create a file larger than 25MB
        large_content = b"x" * (26 * 1024 * 1024)  # 26MB
        
        response = authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(large_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    
    def test_stt_unauthorized(self, app):
        """Test STT without authentication."""
        from deltallm.proxy.dependencies import require_auth
        
        # Remove the auth override to test unauthenticated access
        app.dependency_overrides.pop(require_auth, None)
        
        # Mock require_auth to raise 401
        def mock_require_auth_unauthorized():
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )
        
        app.dependency_overrides[require_auth] = mock_require_auth_unauthorized
        
        client = TestClient(app)
        audio_content = b"fake mp3 audio content" * 1000
        
        response = client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ========== Translation Tests ==========


class TestTranslationEndpoint:
    """Tests for POST /v1/audio/translations (Audio Translation)."""
    
    def test_translation_success_default_params(self, authenticated_client):
        """Test translation with default parameters."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/translations",
            files={
                "file": ("spanish.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        
        data = response.json()
        assert "text" in data
        
        # Check cost headers
        assert "X-ProxyLLM-Response-Cost" in response.headers
        assert "X-ProxyLLM-Model-Used" in response.headers
        assert response.headers["X-ProxyLLM-Endpoint-Type"] == "translation"
    
    def test_translation_text_format(self, authenticated_client):
        """Test translation with text response format."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/translations",
            files={
                "file": ("french.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "response_format": "text",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
    
    def test_translation_with_prompt(self, authenticated_client):
        """Test translation with prompt."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/translations",
            files={
                "file": ("german.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
                "prompt": "Translate to formal English.",
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_translation_invalid_model(self, authenticated_client):
        """Test translation with unsupported model."""
        audio_content = b"fake mp3 audio content" * 1000
        
        response = authenticated_client.post(
            "/v1/audio/translations",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "invalid-model",
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not support audio translation" in response.json()["detail"]
    
    def test_translation_unauthorized(self, app):
        """Test translation without authentication."""
        from deltallm.proxy.dependencies import require_auth
        
        # Remove the auth override to test unauthenticated access
        app.dependency_overrides.pop(require_auth, None)
        
        # Mock require_auth to raise 401
        def mock_require_auth_unauthorized():
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )
        
        app.dependency_overrides[require_auth] = mock_require_auth_unauthorized
        
        client = TestClient(app)
        audio_content = b"fake mp3 audio content" * 1000
        
        response = client.post(
            "/v1/audio/translations",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ========== Cost Tracking Tests ==========


class TestAudioCostTracking:
    """Tests for cost tracking across all audio endpoints."""
    
    def test_tts_spend_tracking(self, authenticated_client, app):
        """Test that TTS updates spend tracking."""
        key_manager = app.state.key_manager
        key_manager.update_spend.reset_mock()
        
        authenticated_client.post(
            "/v1/audio/speech",
            json={
                "input": "Test spend tracking.",
            }
        )
        
        # Verify spend was updated
        key_manager.update_spend.assert_called_once()
        args = key_manager.update_spend.call_args
        assert args[0][0] == "test_key_hash"  # key_hash
        assert args[0][1] > 0  # cost > 0
    
    def test_stt_spend_tracking(self, authenticated_client, app):
        """Test that STT updates spend tracking."""
        key_manager = app.state.key_manager
        key_manager.update_spend.reset_mock()
        
        audio_content = b"fake mp3 audio content" * 1000
        authenticated_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        # Verify spend was updated
        key_manager.update_spend.assert_called_once()
    
    def test_translation_spend_tracking(self, authenticated_client, app):
        """Test that translation updates spend tracking."""
        key_manager = app.state.key_manager
        key_manager.update_spend.reset_mock()
        
        audio_content = b"fake mp3 audio content" * 1000
        authenticated_client.post(
            "/v1/audio/translations",
            files={
                "file": ("test.mp3", io.BytesIO(audio_content), "audio/mpeg"),
            },
            data={
                "model": "whisper-1",
            }
        )
        
        # Verify spend was updated
        key_manager.update_spend.assert_called_once()
    
    def test_tts_budget_enforcement(self, authenticated_client, app):
        """Test that budget limits are enforced for TTS."""
        # Update mock to have a tight budget
        auth_context = mock_require_auth()
        auth_context.key_info.max_budget = 0.000001  # Very small budget
        auth_context.key_info.spend = 0.0
        
        from deltallm.proxy.dependencies import require_auth
        app.dependency_overrides[require_auth] = lambda: auth_context
        
        client = TestClient(app)
        
        # This request should exceed budget
        response = client.post(
            "/v1/audio/speech",
            json={
                "input": "This is a long text that will exceed the tiny budget.",
            }
        )
        
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Budget exceeded" in response.json()["detail"]


# ========== Schema Validation Tests ==========


class TestAudioSchemas:
    """Tests for audio request/response schemas."""
    
    def test_audio_speech_request_validation(self):
        """Test AudioSpeechRequest validation."""
        # Valid request
        request = AudioSpeechRequest(
            model="tts-1",
            input="Hello, world!",
            voice="alloy",
            response_format="mp3",
            speed=1.0,
        )
        assert request.model == "tts-1"
        assert request.input == "Hello, world!"
        
        # Valid with defaults
        request = AudioSpeechRequest(input="Hi")
        assert request.model == "tts-1"  # default
        assert request.voice == "alloy"  # default
    
    def test_audio_transcription_response(self):
        """Test AudioTranscriptionResponse."""
        response = AudioTranscriptionResponse(
            text="Hello, this is a transcription.",
        )
        assert response.text == "Hello, this is a transcription."
    
    def test_audio_translation_response(self):
        """Test AudioTranslationResponse."""
        response = AudioTranslationResponse(
            text="Hello in English.",
        )
        assert response.text == "Hello in English."
