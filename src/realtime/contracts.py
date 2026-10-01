from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncContextManager, Literal, Protocol

from src.models.responses import UserAPIKeyAuth
from src.realtime.errors import RealtimeError as RealtimeError

RealtimeProfile = Literal["realtime", "transcription"]


class SocketClosed(Exception):
    """A transport ended; never expose its peer-supplied close reason."""


@dataclass(frozen=True, slots=True)
class RealtimeLimits:
    max_connections: int = 64
    max_message_bytes: int = 1024 * 1024
    max_input_bytes: int = 64 * 1024 * 1024
    max_client_events: int = 10_000
    max_server_events: int = 100_000
    handshake_seconds: float = 10
    session_seconds: float = 300
    idle_seconds: float = 60
    write_seconds: float = 10
    health_seconds: float = 5
    cleanup_seconds: float = 5

    def __post_init__(self) -> None:
        from dataclasses import fields
        from math import isfinite

        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Realtime limits must be positive finite numbers")
            if not isfinite(value) or value <= 0:
                raise ValueError("Realtime limits must be positive finite numbers")
            if not item.name.endswith("_seconds") and not isinstance(value, int):
                raise ValueError("Realtime byte, event and connection limits must be integers")


@dataclass(frozen=True, slots=True)
class RealtimeRequest:
    session_id: str
    model: str | None
    profile: RealtimeProfile
    auth: UserAPIKeyAuth = field(repr=False)


class TextSocket(Protocol):
    async def receive_text(self) -> str: ...
    async def send_text(self, message: str) -> None: ...


class RealtimePermit(Protocol):
    """Owned by admission, never a permissive default in the transport.

    Admission pins authorized routing/credentials/prices, reserves capacity and
    budget before connect, and finalizes/reconciles on context exit. The local
    client check covers automatic VAD work as well as explicit responses. It
    must not do network I/O per audio frame. Health checks renew owned leases
    and recheck revocation; loss of ownership must raise, not just log.
    """

    def authorize_client_event(self, event: Mapping[str, object]) -> None: ...

    async def prepare_upstream(self, upstream: TextSocket) -> TextSocket: ...

    async def before_client_event(self, event: Mapping[str, object]) -> None:
        """Boundary I/O only: durably record new billable turns before forwarding."""
        ...

    async def accept_usage(self, event: Mapping[str, object]) -> None:
        """Durably accept an idempotent receipt or pending usage before delivery."""
        ...

    async def check_health(self) -> None: ...


class RealtimeAdmission(Protocol):
    @property
    def ready(self) -> bool: ...

    def admit(self, request: RealtimeRequest) -> AsyncContextManager["AdmittedRealtime"]: ...


@dataclass(frozen=True, slots=True)
class AdmittedRealtime:
    # Importing the provider lazily avoids coupling the transport contracts to
    # provider SDKs and lets deterministic tests exercise the same interface.
    target: "OpenAIRealtimeTarget"
    permit: RealtimePermit


if TYPE_CHECKING:
    from src.providers.openai_realtime import OpenAIRealtimeTarget
