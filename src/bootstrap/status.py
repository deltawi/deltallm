from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


BootstrapState = Literal["ready", "disabled", "degraded"]


@dataclass(frozen=True)
class BootstrapStatus:
    name: str
    state: BootstrapState
    detail: str | None = None


def format_bootstrap_summary(scope: str, statuses: tuple[BootstrapStatus, ...]) -> str:
    parts: list[str] = []
    for status in statuses:
        segment = f"{status.name}={status.state}"
        if status.detail:
            segment = f"{segment}({status.detail})"
        parts.append(segment)
    return f"{scope}: " + ", ".join(parts)
