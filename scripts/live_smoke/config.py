"""Settings for one smoke run and the fixed names of its test resources."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets

MASTER_KEY_ENV = "DELTALLM_MASTER_KEY"
DEFAULT_PREFIX = "live-smoke"
SUITE_MARKER = "deltallm-live-smoke"
_PREFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")

# Modes this suite can call. Realtime needs a WebSocket session and is skipped.
PROBED_MODES = frozenset(
    {
        "chat",
        "embedding",
        "audio_speech",
        "audio_transcription",
        "image_generation",
        "rerank",
    }
)


def validate_prefix(prefix: str) -> str:
    value = prefix.strip().lower()
    if not _PREFIX_PATTERN.fullmatch(value):
        raise ValueError("prefix must be 3-40 characters: lowercase letters, digits, and hyphens")
    return value


@dataclass(frozen=True)
class ResourceNames:
    """Fixed names, so a second run finds and reuses the same resources."""

    prefix: str

    @property
    def tier_key(self) -> str:
        return f"{self.prefix}-all-models"

    @property
    def tier_name(self) -> str:
        return f"{self.prefix} all models"

    @property
    def organization_id_base(self) -> str:
        return f"{self.prefix}-org"

    @property
    def organization_name(self) -> str:
        return f"{self.prefix} organization"

    def new_organization_id(self) -> str:
        # A deleted organization ID can never be used again, so each new suite
        # organization gets a short random suffix.
        return f"{self.organization_id_base}-{secrets.token_hex(3)}"

    def is_suite_organization(self, organization_id: str) -> bool:
        base = re.escape(self.organization_id_base)
        return re.fullmatch(rf"{base}(-[0-9a-f]{{6}})?", organization_id) is not None

    @staticmethod
    def team_id_for(organization_id: str) -> str:
        return f"{organization_id}-team"

    @property
    def team_alias(self) -> str:
        return f"{self.prefix} team"

    @property
    def user_email(self) -> str:
        return f"{self.prefix}-user@example.com"

    @property
    def key_name(self) -> str:
        return f"{self.prefix}-key"


@dataclass(frozen=True)
class SmokeConfig:
    base_url: str
    master_key: str
    names: ResourceNames
    request_timeout: float = 60.0
    max_tokens: int = 32
    parallel: int = 4
    models: frozenset[str] = field(default_factory=frozenset)
    skip_modes: frozenset[str] = field(default_factory=frozenset)
    run_batch: bool = True
    batch_model: str | None = None
    batch_timeout: float = 300.0
    batch_poll_interval: float = 3.0
    tts_voice: str = "alloy"
    image_size: str | None = None
    openai_sdk: bool = False
    cleanup: bool = False
