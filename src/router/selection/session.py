from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from src.models.requests import ChatCompletionRequest
from src.route_policy_contract import LLMTierSelectorPolicy
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorInvariantError,
    SelectorPolicyIdentity,
    UnattemptedSelectorUsage,
)

_PREFERRED_DEPLOYMENT_KEY = "_deltallm_selector_preferred_deployment"


class SelectorSessionSource(StrEnum):
    HEADER = "header"
    AGENT = "agent"
    METADATA = "metadata"
    FINGERPRINT = "fingerprint"


class SelectorSessionLookup(StrEnum):
    REUSED = "reused"
    RECLASSIFIED = "reclassified"
    MISS = "miss"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class SelectorSessionPin:
    """The lane rank and deployment that last answered for one session."""

    rank: int
    deployment_id: str


class SelectorSessionStore(Protocol):
    """Best-effort affinity hints. Failures behave as a miss and never fail a request."""

    async def lookup(self, key: str) -> SelectorSessionPin | None: ...

    def remember(self, key: str, pin: SelectorSessionPin, *, ttl_seconds: int) -> None: ...


@dataclass(frozen=True, slots=True)
class SelectorSessionIdentity:
    """A hashed, principal-scoped session reference. The raw session ID is not kept."""

    source: SelectorSessionSource
    digest: str

    def key(self, group: str, identity: SelectorPolicyIdentity) -> str:
        # A published policy change produces a new fingerprint, so old pins stop matching.
        material = f"{self.digest}\0{group}\0{identity.fingerprint}".encode()
        return sha256(material).hexdigest()


class SelectorSession:
    """One request's session pin: at most one lookup and one remembered answer."""

    def __init__(
        self,
        identity: SelectorSessionIdentity,
        store: SelectorSessionStore,
        *,
        new_user_turn: bool,
    ) -> None:
        self.identity = identity
        self._store = store
        self._new_user_turn = new_user_turn
        self._looked_up = False
        self._group: str | None = None
        self._key: str | None = None
        self._ttl_seconds = 0
        self._pin: SelectorSessionPin | None = None

    async def lookup(
        self,
        group: str,
        policy: LLMTierSelectorPolicy,
        identity: SelectorPolicyIdentity,
    ) -> SelectorSessionPin | None:
        if self._looked_up:
            raise SelectorInvariantError()
        self._looked_up = True
        settings = policy.effective_session
        if not settings.enabled:
            return None
        self._group = group
        self._key = self.identity.key(group, identity)
        self._ttl_seconds = settings.ttl_seconds
        pin = await self._store.lookup(self._key)
        if pin is None or not 0 <= pin.rank < len(policy.lanes):
            return None
        self._pin = pin
        return pin

    def reuses(self, pin: SelectorSessionPin, policy: LLMTierSelectorPolicy) -> bool:
        """Classify again only for new user text, and never once at the top lane."""
        return not self._new_user_turn or pin.rank >= policy.lanes[-1].rank

    def remember(self, group: str, deployment_id: str, rank: int) -> None:
        if self._key is None or group != self._group:
            return
        if self._pin is not None and rank < self._pin.rank:
            # Planning excludes lower lanes, so this is a defensive no-downgrade check.
            return
        self._store.remember(
            self._key,
            SelectorSessionPin(rank=rank, deployment_id=deployment_id),
            ttl_seconds=self._ttl_seconds,
        )


def has_new_user_text(payload: ChatCompletionRequest) -> bool:
    """False for continuations such as tool results or an assistant prefill."""
    if not payload.messages:
        return False
    last = payload.messages[-1]
    if last.role != "user":
        return False
    content = last.content
    if isinstance(content, str):
        return bool(content.strip())
    return any(
        isinstance(part, dict)
        and part.get("type") in ("text", "input_text")
        and isinstance(part.get("text"), str)
        and bool(part["text"].strip())
        for part in content
    )


def pinned_decision(
    pin: SelectorSessionPin,
    policy: LLMTierSelectorPolicy,
    identity: SelectorPolicyIdentity,
) -> SelectorDecision:
    lane = policy.lanes[pin.rank]
    return SelectorDecision(
        lane=lane.id,
        minimum_rank=lane.rank,
        cause=SelectorCause.SESSION_PINNED,
        latency_ms=0.0,
        policy_identity=identity,
        usage=UnattemptedSelectorUsage(),
    )


def apply_session_floor(
    decision: SelectorDecision,
    policy: LLMTierSelectorPolicy,
    floor_rank: int | None,
) -> SelectorDecision:
    """A session can move up a lane, never down."""
    if floor_rank is None or decision.minimum_rank >= floor_rank:
        return decision
    lane = policy.lanes[floor_rank]
    cause = (
        SelectorCause.SESSION_FLOOR
        if decision.cause is SelectorCause.CLASSIFIED
        else decision.cause
    )
    return decision.model_copy(update={"lane": lane.id, "minimum_rank": lane.rank, "cause": cause})


def set_preferred_deployment(context: dict[str, object], deployment_id: str) -> None:
    context[_PREFERRED_DEPLOYMENT_KEY] = deployment_id


def preferred_deployment(context: dict[str, object]) -> str | None:
    value = context.get(_PREFERRED_DEPLOYMENT_KEY)
    return value if isinstance(value, str) else None
