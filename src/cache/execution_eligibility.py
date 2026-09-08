from dataclasses import dataclass
from enum import StrEnum


class ResponseCacheOutcome(StrEnum):
    UNRESOLVED = "unresolved"
    HIT = "hit"
    MISS = "miss"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class ResponseCacheEligibility:
    """Cache-owned evidence, not caller metadata or authorization evidence.

    The future execution edge must also supply completed authenticated preflight.
    A missing signal is unresolved, never an implicit miss.
    """

    outcome: ResponseCacheOutcome = ResponseCacheOutcome.UNRESOLVED

    def require_provider_execution(self) -> None:
        if self.outcome not in (ResponseCacheOutcome.MISS, ResponseCacheOutcome.BYPASS):
            raise RuntimeError("Response cache has not authorized provider execution")
