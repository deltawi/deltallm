from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.realtime.contracts import RealtimeLimits


class RealtimeSettings(BaseModel):
    """Startup-only bounds for the shared Realtime transport."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    default_transcription_model: str | None = Field(default=None, min_length=1, max_length=256)
    max_turns: int = Field(default=100, ge=1, le=1000)
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)
    global_max_connections: int = Field(default=256, ge=1, le=100_000)
    organization_max_connections: int = Field(default=64, ge=1, le=10_000)
    max_connections: int = Field(default=64, ge=1, le=4096)
    max_message_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_input_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_client_events: int = Field(default=10_000, ge=1, le=100_000)
    max_server_events: int = Field(default=100_000, ge=1, le=1_000_000)
    handshake_seconds: float = Field(default=10, gt=0, le=30)
    session_seconds: float = Field(default=300, gt=0, le=3600)
    idle_seconds: float = Field(default=60, gt=0, le=300)
    write_seconds: float = Field(default=10, gt=0, le=30)
    health_seconds: float = Field(default=5, ge=1, le=30)
    cleanup_seconds: float = Field(default=5, gt=0, le=30)

    @model_validator(mode="after")
    def validate_bounds(self) -> RealtimeSettings:
        if self.max_message_bytes > self.max_input_bytes:
            raise ValueError("Realtime message limit exceeds session input limit")
        if self.health_seconds > self.idle_seconds:
            raise ValueError("Realtime health interval exceeds idle timeout")
        self.transport_limits()
        return self

    def transport_limits(self) -> RealtimeLimits:
        return RealtimeLimits(
            **self.model_dump(
                exclude={
                    "enabled",
                    "default_transcription_model",
                    "max_turns",
                    "max_output_tokens",
                    "global_max_connections",
                    "organization_max_connections",
                }
            )
        )
