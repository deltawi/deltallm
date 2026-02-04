"""Pydantic schemas for audio API endpoints.

This module defines request and response models for:
- Text-to-Speech (TTS): /v1/audio/speech
- Speech-to-Text (STT): /v1/audio/transcriptions
- Translations: /v1/audio/translations
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ========== Text-to-Speech (TTS) Schemas ==========


class AudioSpeechRequest(BaseModel):
    """Request to generate speech from text.
    
    Compatible with OpenAI's /v1/audio/speech endpoint.
    """
    
    model: str = Field(
        default="tts-1",
        description="TTS model to use (tts-1, tts-1-hd)",
    )
    input: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Text to generate audio for (max 4096 characters)",
    )
    voice: Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"] = Field(
        default="alloy",
        description="Voice to use for generation",
    )
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = Field(
        default="mp3",
        description="Audio format to output",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Audio speed from 0.25x to 4.0x",
    )


# ========== Speech-to-Text (STT) Schemas ==========


class AudioTranscriptionRequest(BaseModel):
    """Request to transcribe audio to text.
    
    Compatible with OpenAI's /v1/audio/transcriptions endpoint.
    """
    
    model: str = Field(
        default="whisper-1",
        description="STT model to use (whisper-1)",
    )
    language: Optional[str] = Field(
        default=None,
        description="Language code (ISO-639-1 format, e.g., 'en', 'fr'). Improves accuracy.",
    )
    prompt: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional prompt to guide transcription style",
    )
    response_format: Literal["json", "text", "srt", "verbose_json", "vtt"] = Field(
        default="json",
        description="Format of the transcription output",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for generation",
    )
    timestamp_granularities: Optional[list[Literal["word", "segment"]]] = Field(
        default=None,
        description="Granularity of timestamps (verbose_json only)",
    )


class AudioTranscriptionResponse(BaseModel):
    """Response from audio transcription.
    
    Standard JSON response format.
    """
    
    text: str = Field(
        ...,
        description="Transcribed text",
    )


class AudioTranscriptionVerboseResponse(BaseModel):
    """Verbose response from audio transcription.
    
    Includes detailed timing and segment information.
    """
    
    task: str = Field(default="transcribe")
    language: str = Field(..., description="Detected or specified language")
    duration: float = Field(..., description="Audio duration in seconds")
    text: str = Field(..., description="Full transcribed text")
    words: Optional[list[dict]] = Field(
        default=None,
        description="Word-level timestamps (if requested)",
    )
    segments: Optional[list[dict]] = Field(
        default=None,
        description="Segment-level timestamps (if requested)",
    )


# ========== Translation Schemas ==========


class AudioTranslationRequest(BaseModel):
    """Request to translate audio to English.
    
    Compatible with OpenAI's /v1/audio/translations endpoint.
    """
    
    model: str = Field(
        default="whisper-1",
        description="STT model to use (whisper-1)",
    )
    prompt: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional prompt to guide translation style",
    )
    response_format: Literal["json", "text", "srt", "verbose_json", "vtt"] = Field(
        default="json",
        description="Format of the translation output",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for generation",
    )


class AudioTranslationResponse(BaseModel):
    """Response from audio translation.
    
    Standard JSON response format.
    """
    
    text: str = Field(
        ...,
        description="Translated text in English",
    )


# ========== Cost Tracking Schemas ==========


class AudioCostInfo(BaseModel):
    """Cost information for audio operations.
    
    Returned in response headers and used for spend tracking.
    """
    
    model: str = Field(..., description="Model used")
    operation: Literal["tts", "stt", "translation"] = Field(..., description="Operation type")
    units: float = Field(..., description="Units consumed (characters or seconds)")
    cost: str = Field(..., description="Total cost in USD (as string for precision)")
    cost_breakdown: Optional[dict] = Field(
        default=None,
        description="Detailed cost breakdown",
    )
