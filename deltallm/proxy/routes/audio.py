"""Audio API routes.

This module provides endpoints for:
- Text-to-Speech (TTS): /v1/audio/speech
- Speech-to-Text (STT): /v1/audio/transcriptions
- Audio Translation: /v1/audio/translations

All endpoints include cost tracking and return spend information in headers.
"""

import io
import logging
import time
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import ValidationError

from deltallm.pricing.calculator import CostCalculator
from deltallm.proxy.dependencies import require_auth, AuthContext
from deltallm.proxy.spend_tracking import record_spend_from_endpoint

from ..schemas_audio import (
    AudioCostInfo,
    AudioSpeechRequest,
    AudioTranscriptionResponse,
    AudioTranscriptionVerboseResponse,
    AudioTranslationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audio"])


# ========== Dependencies ==========


def get_cost_calculator(request: Request) -> CostCalculator:
    """Get the cost calculator from app state."""
    return request.app.state.cost_calculator


# ========== Helper Functions ==========


def _get_audio_duration_seconds(audio_bytes: bytes, format_hint: Optional[str] = None) -> float:
    """Estimate audio duration from audio bytes.
    
    This is a simple estimation. In production, you'd want to use
    a library like ffmpeg-python or librosa for accurate duration.
    
    Args:
        audio_bytes: Raw audio file bytes
        format_hint: Audio format hint (mp3, wav, etc.)
        
    Returns:
        Estimated duration in seconds
    """
    # Simple heuristic-based estimation
    # MP3: roughly 16KB per second at 128kbps
    # WAV: roughly 176KB per second at 44.1kHz/16bit/mono
    
    if format_hint == "mp3":
        # Rough estimate: ~16KB per second at 128kbps
        return len(audio_bytes) / 16000
    elif format_hint in ("wav", "pcm"):
        # Rough estimate: ~176KB per second at CD quality mono
        return len(audio_bytes) / 176400
    elif format_hint == "opus":
        # Opus is variable bitrate, ~10KB per second average
        return len(audio_bytes) / 10000
    else:
        # Default fallback: assume MP3-like compression
        return len(audio_bytes) / 16000


def _add_cost_headers(
    response: Response,
    cost_info: AudioCostInfo,
    request_id: Optional[str] = None,
) -> Response:
    """Add cost tracking headers to response.
    
    Args:
        response: FastAPI response object
        cost_info: Cost information
        request_id: Optional request ID
        
    Returns:
        Response with headers added
    """
    response.headers["X-ProxyLLM-Response-Cost"] = cost_info.cost
    response.headers["X-ProxyLLM-Model-Used"] = cost_info.model
    response.headers["X-ProxyLLM-Endpoint-Type"] = cost_info.operation
    if request_id:
        response.headers["X-ProxyLLM-Request-ID"] = request_id
    return response


# ========== Text-to-Speech Endpoint ==========


async def _record_audio_spend(
    request: Request,
    auth_context: AuthContext,
    model: str,
    endpoint_type: str,
    cost: Decimal,
    start_time: Optional[float] = None,
    **kwargs
) -> None:
    """Record spend for audio operations in background.
    
    This function runs as a background task to avoid delaying the response.
    """
    logger.info(f"_record_audio_spend START: model={model}, endpoint_type={endpoint_type}, cost=${float(cost):.12f}")
    try:
        # Calculate latency if start_time provided
        latency_ms = None
        if start_time:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"Calculated latency: {latency_ms:.2f}ms")
        
        logger.info(f"Calling record_spend_from_endpoint for model={model}")
        await record_spend_from_endpoint(
            request=request,
            auth_context=auth_context,
            model=model,
            endpoint_type=endpoint_type,
            cost=cost,
            latency_ms=latency_ms,
            **kwargs
        )
        logger.info(f"Background spend recorded SUCCESS: model={model}, cost=${float(cost):.12f}, latency={latency_ms:.2f}ms")
    except Exception as e:
        logger.exception(f"Failed to record spend in background: {e}")


@router.post(
    "/audio/speech",
    response_class=Response,
    responses={
        200: {
            "description": "Audio file generated successfully",
            "content": {
                "audio/mpeg": {},
                "audio/opus": {},
                "audio/aac": {},
                "audio/flac": {},
                "audio/wav": {},
                "audio/pcm": {},
            },
        },
        400: {"description": "Bad request - invalid parameters"},
        401: {"description": "Authentication required"},
        403: {"description": "Model not allowed for this key"},
        429: {"description": "Budget exceeded"},
    },
)
async def create_speech(
    request: Request,
    body: AudioSpeechRequest,
    background_tasks: BackgroundTasks,
    auth_context: Annotated[AuthContext, Depends(require_auth)],
    calculator: Annotated[CostCalculator, Depends(get_cost_calculator)],
) -> Response:
    """Generate speech from text (Text-to-Speech).
    
    This endpoint is compatible with OpenAI's /v1/audio/speech API.
    Supports tts-1 and tts-1-hd models with multiple voices.
    
    **Pricing**: Charged per character of input text.
    - tts-1: $0.000015 per character
    - tts-1-hd: $0.000030 per character
    
    **Example Request:**
    ```json
    {
        "model": "tts-1",
        "input": "Hello, world!",
        "voice": "alloy",
        "response_format": "mp3",
        "speed": 1.0
    }
    ```
    
    **Response Headers:**
    - `X-ProxyLLM-Response-Cost`: Total cost in USD
    - `X-ProxyLLM-Model-Used`: Model used for generation
    - `X-ProxyLLM-Endpoint-Type`: "tts"
    """
    key_info = auth_context.key_info
    
    # Check model permissions
    if key_info and key_info.models:
        requested_model = body.model
        allowed = any(
            requested_model == allowed_model or requested_model.endswith(allowed_model)
            for allowed_model in key_info.models
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Model '{requested_model}' not allowed for this key",
            )
    
    # Validate model supports TTS
    supported_tts_models = ["tts-1", "tts-1-hd"]
    if body.model not in supported_tts_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{body.model}' does not support text-to-speech. "
                   f"Supported models: {', '.join(supported_tts_models)}",
        )
    
    # Calculate cost before processing
    character_count = len(body.input)
    cost_breakdown = calculator.calculate_audio_speech_cost(
        model=body.model,
        character_count=character_count,
    )
    
    # Check budget
    if key_info and key_info.max_budget is not None:
        estimated_spend = key_info.spend + float(cost_breakdown.total_cost)
        if estimated_spend >= key_info.max_budget:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Budget exceeded",
            )
    
    try:
        # For now, return a placeholder audio response
        # In production, this would call the actual TTS provider
        # TODO: Integrate with OpenAI, ElevenLabs, etc.
        start_time = time.time()
        
        # Create a minimal valid MP3 (silent frame) as placeholder
        # This is a 1-second silent MP3 file
        placeholder_audio = bytes([
            0xFF, 0xFB, 0x90, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ])
        
        # Determine content type based on response_format
        content_type_map = {
            "mp3": "audio/mpeg",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
            "pcm": "audio/pcm",
        }
        content_type = content_type_map.get(body.response_format, "audio/mpeg")
        
        # Create response
        response = Response(
            content=placeholder_audio,
            media_type=content_type,
        )
        
        # Add cost headers
        cost_info = AudioCostInfo(
            model=body.model,
            operation="tts",
            units=character_count,
            cost=str(cost_breakdown.total_cost),
            cost_breakdown={
                "audio_cost": str(cost_breakdown.audio_cost) if cost_breakdown.audio_cost else "0",
            },
        )
        _add_cost_headers(response, cost_info)
        
        # Record spend in background to not delay response
        background_tasks.add_task(
            _record_audio_spend,
            request,
            auth_context,
            body.model,
            "audio_speech",
            cost_breakdown.total_cost,
            start_time,  # Pass start_time for latency calculation
            audio_characters=character_count,
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error generating speech")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating speech: {str(e)}",
        )


# ========== Speech-to-Text Endpoint ==========


@router.post(
    "/audio/transcriptions",
    response_model=AudioTranscriptionResponse,
    responses={
        200: {
            "description": "Transcription successful",
            "model": AudioTranscriptionResponse,
        },
        400: {"description": "Bad request - invalid parameters"},
        401: {"description": "Authentication required"},
        403: {"description": "Model not allowed for this key"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported media type"},
        429: {"description": "Budget exceeded"},
    },
)
async def create_transcription(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_context: Annotated[AuthContext, Depends(require_auth)],
    calculator: Annotated[CostCalculator, Depends(get_cost_calculator)],
    file: Annotated[UploadFile, File(...)],
    model: Annotated[str, Form()] = "whisper-1",
    language: Annotated[Optional[str], Form()] = None,
    prompt: Annotated[Optional[str], Form()] = None,
    response_format: Annotated[str, Form()] = "json",
    temperature: Annotated[float, Form()] = 0.0,
    timestamp_granularities: Annotated[Optional[str], Form()] = None,
) -> Response:
    """Transcribe audio to text (Speech-to-Text).
    
    This endpoint is compatible with OpenAI's /v1/audio/transcriptions API.
    Supports whisper-1 model.
    
    **Pricing**: Charged per minute of audio.
    - whisper-1: $0.006 per minute (rounded to nearest second)
    
    **Supported Formats:** mp3, mp4, mpeg, mpga, m4a, wav, webm
    
    **Example Request:**
    ```bash
    curl -X POST http://localhost:8000/v1/audio/transcriptions \\
        -H "Authorization: Bearer $API_KEY" \\
        -F "file=@audio.mp3" \\
        -F "model=whisper-1" \\
        -F "language=en"
    ```
    
    **Response Headers:**
    - `X-ProxyLLM-Response-Cost`: Total cost in USD
    - `X-ProxyLLM-Model-Used`: Model used for transcription
    - `X-ProxyLLM-Endpoint-Type`: "stt"
    """
    key_info = auth_context.key_info
    
    # Check model permissions
    if key_info and key_info.models:
        allowed = any(
            model == allowed_model or model.endswith(allowed_model)
            for allowed_model in key_info.models
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Model '{model}' not allowed for this key",
            )
    
    # Validate model supports STT
    supported_stt_models = ["whisper-1"]
    if model not in supported_stt_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{model}' does not support speech-to-text. "
                   f"Supported models: {', '.join(supported_stt_models)}",
        )
    
    # Validate file
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided",
        )
    
    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading file: {str(e)}",
        )
    
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided",
        )
    
    # Check file size (max 25MB like OpenAI)
    max_size = 25 * 1024 * 1024  # 25MB
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is 25MB.",
        )
    
    # Estimate audio duration
    file_extension = file.filename.split(".")[-1].lower() if "." in file.filename else ""
    duration_seconds = _get_audio_duration_seconds(content, file_extension)
    
    # Calculate cost
    cost_breakdown = calculator.calculate_audio_transcription_cost(
        model=model,
        duration_seconds=duration_seconds,
    )
    
    # Check budget
    if key_info and key_info.max_budget is not None:
        estimated_spend = key_info.spend + float(cost_breakdown.total_cost)
        if estimated_spend >= key_info.max_budget:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Budget exceeded",
            )
    
    try:
        # For now, return a placeholder transcription
        # In production, this would call the actual STT provider
        # TODO: Integrate with OpenAI Whisper, AssemblyAI, etc.
        start_time = time.time()
        
        # Placeholder response
        transcription_text = f"[Transcription placeholder for {file.filename}]"
        
        # Build response based on format
        if response_format == "text":
            response = Response(
                content=transcription_text,
                media_type="text/plain",
            )
        elif response_format in ("srt", "vtt"):
            # Simple placeholder for subtitle formats
            subtitle_content = f"1\n00:00:00,000 --> 00:00:05,000\n{transcription_text}\n"
            response = Response(
                content=subtitle_content,
                media_type="text/plain",
            )
        elif response_format == "verbose_json":
            verbose_response = AudioTranscriptionVerboseResponse(
                task="transcribe",
                language=language or "en",
                duration=duration_seconds,
                text=transcription_text,
            )
            response = Response(
                content=verbose_response.model_dump_json(),
                media_type="application/json",
            )
        else:
            # Default JSON format
            json_response = AudioTranscriptionResponse(text=transcription_text)
            response = Response(
                content=json_response.model_dump_json(),
                media_type="application/json",
            )
        
        # Add cost headers
        cost_info = AudioCostInfo(
            model=model,
            operation="stt",
            units=duration_seconds,
            cost=str(cost_breakdown.total_cost),
            cost_breakdown={
                "audio_cost": str(cost_breakdown.audio_cost) if cost_breakdown.audio_cost else "0",
            },
        )
        _add_cost_headers(response, cost_info)
        
        # Record spend in background to not delay response
        background_tasks.add_task(
            _record_audio_spend,
            request,
            auth_context,
            model,
            "audio_transcription",
            cost_breakdown.total_cost,
            start_time,  # Pass start_time for latency calculation
            audio_seconds=duration_seconds,
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error transcribing audio")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error transcribing audio: {str(e)}",
        )


# ========== Translation Endpoint ==========


@router.post(
    "/audio/translations",
    response_model=AudioTranslationResponse,
    responses={
        200: {
            "description": "Translation successful",
            "model": AudioTranslationResponse,
        },
        400: {"description": "Bad request - invalid parameters"},
        401: {"description": "Authentication required"},
        403: {"description": "Model not allowed for this key"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported media type"},
        429: {"description": "Budget exceeded"},
    },
)
async def create_translation(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_context: Annotated[AuthContext, Depends(require_auth)],
    calculator: Annotated[CostCalculator, Depends(get_cost_calculator)],
    file: Annotated[UploadFile, File(...)],
    model: Annotated[str, Form()] = "whisper-1",
    prompt: Annotated[Optional[str], Form()] = None,
    response_format: Annotated[str, Form()] = "json",
    temperature: Annotated[float, Form()] = 0.0,
) -> Response:
    """Translate audio to English.
    
    This endpoint is compatible with OpenAI's /v1/audio/translations API.
    Supports whisper-1 model. Automatically translates any language to English.
    
    **Pricing**: Charged per minute of audio (same as transcription).
    - whisper-1: $0.006 per minute (rounded to nearest second)
    
    **Supported Formats:** mp3, mp4, mpeg, mpga, m4a, wav, webm
    
    **Example Request:**
    ```bash
    curl -X POST http://localhost:8000/v1/audio/translations \\
        -H "Authorization: Bearer $API_KEY" \\
        -F "file=@spanish_audio.mp3" \\
        -F "model=whisper-1"
    ```
    
    **Response Headers:**
    - `X-ProxyLLM-Response-Cost`: Total cost in USD
    - `X-ProxyLLM-Model-Used`: Model used for translation
    - `X-ProxyLLM-Endpoint-Type`: "translation"
    """
    key_info = auth_context.key_info
    
    # Check model permissions
    if key_info and key_info.models:
        allowed = any(
            model == allowed_model or model.endswith(allowed_model)
            for allowed_model in key_info.models
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Model '{model}' not allowed for this key",
            )
    
    # Validate model supports translation
    supported_translation_models = ["whisper-1"]
    if model not in supported_translation_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{model}' does not support audio translation. "
                   f"Supported models: {', '.join(supported_translation_models)}",
        )
    
    # Validate file
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided",
        )
    
    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading file: {str(e)}",
        )
    
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided",
        )
    
    # Check file size (max 25MB like OpenAI)
    max_size = 25 * 1024 * 1024  # 25MB
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is 25MB.",
        )
    
    # Estimate audio duration
    file_extension = file.filename.split(".")[-1].lower() if "." in file.filename else ""
    duration_seconds = _get_audio_duration_seconds(content, file_extension)
    
    # Calculate cost (same as transcription)
    cost_breakdown = calculator.calculate_audio_transcription_cost(
        model=model,
        duration_seconds=duration_seconds,
    )
    
    # Check budget
    if key_info and key_info.max_budget is not None:
        estimated_spend = key_info.spend + float(cost_breakdown.total_cost)
        if estimated_spend >= key_info.max_budget:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Budget exceeded",
            )
    
    try:
        # For now, return a placeholder translation
        # In production, this would call the actual translation provider
        # TODO: Integrate with OpenAI Whisper, etc.
        start_time = time.time()
        
        # Placeholder response
        translation_text = f"[English translation placeholder for {file.filename}]"
        
        # Build response based on format
        if response_format == "text":
            response = Response(
                content=translation_text,
                media_type="text/plain",
            )
        elif response_format in ("srt", "vtt"):
            subtitle_content = f"1\n00:00:00,000 --> 00:00:05,000\n{translation_text}\n"
            response = Response(
                content=subtitle_content,
                media_type="text/plain",
            )
        elif response_format == "verbose_json":
            verbose_response = {
                "task": "translate",
                "language": "en",
                "duration": duration_seconds,
                "text": translation_text,
            }
            response = Response(
                content=__import__("json").dumps(verbose_response),
                media_type="application/json",
            )
        else:
            # Default JSON format
            json_response = AudioTranslationResponse(text=translation_text)
            response = Response(
                content=json_response.model_dump_json(),
                media_type="application/json",
            )
        
        # Add cost headers
        cost_info = AudioCostInfo(
            model=model,
            operation="translation",
            units=duration_seconds,
            cost=str(cost_breakdown.total_cost),
            cost_breakdown={
                "audio_cost": str(cost_breakdown.audio_cost) if cost_breakdown.audio_cost else "0",
            },
        )
        _add_cost_headers(response, cost_info)
        
        # Record spend in background to not delay response
        background_tasks.add_task(
            _record_audio_spend,
            request,
            auth_context,
            model,
            "audio_transcription",  # Translation uses same endpoint type as transcription
            cost_breakdown.total_cost,
            start_time,  # Pass start_time for latency calculation
            audio_seconds=duration_seconds,
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error translating audio")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error translating audio: {str(e)}",
        )
