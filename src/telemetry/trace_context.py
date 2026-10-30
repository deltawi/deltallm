"""Carry a caller's W3C trace context from the HTTP edge to optional exporters."""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass

# W3C Trace Context level 1: version 00, non-zero trace and parent IDs.
_TRACEPARENT = re.compile(r"00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}")
_TRACESTATE_MAX_LENGTH = 512


@dataclass(frozen=True, slots=True)
class TraceContext:
    traceparent: str
    tracestate: str | None = None

    def carrier(self) -> dict[str, str]:
        carrier = {"traceparent": self.traceparent}
        if self.tracestate:
            carrier["tracestate"] = self.tracestate
        return carrier


_current: ContextVar[TraceContext | None] = ContextVar("deltallm_trace_context", default=None)


def parse_trace_context(
    traceparent: object, tracestate: object | None = None
) -> TraceContext | None:
    """Return a valid context, or None. Never raise on caller-controlled input."""

    if not isinstance(traceparent, str) or not _TRACEPARENT.fullmatch(traceparent):
        return None
    state = tracestate if isinstance(tracestate, str) else None
    if state is not None and (
        len(state) > _TRACESTATE_MAX_LENGTH or not all(" " <= c <= "~" for c in state)
    ):
        state = None
    return TraceContext(traceparent=traceparent, tracestate=state or None)


def trace_context_from_headers(headers: list[tuple[bytes, bytes]]) -> TraceContext | None:
    traceparent: list[bytes] = []
    tracestate: list[bytes] = []
    for key, value in headers:
        lowered = key.lower()
        if lowered == b"traceparent":
            traceparent.append(value)
        elif lowered == b"tracestate":
            tracestate.append(value)
    # A repeated traceparent is ambiguous; ignore it rather than pick one.
    if len(traceparent) != 1:
        return None
    state = tracestate[0].decode("latin-1") if len(tracestate) == 1 else None
    return parse_trace_context(traceparent[0].decode("latin-1"), state)


def current_trace_context() -> TraceContext | None:
    return _current.get()


def bind_trace_context(context: TraceContext | None):  # noqa: ANN201
    return _current.set(context)


def reset_trace_context(token) -> None:  # noqa: ANN001
    _current.reset(token)
