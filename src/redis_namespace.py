from __future__ import annotations

import re


_NAMESPACE_SEGMENT_PATTERN = re.compile(r"[^a-z0-9_-]+")


def build_redis_channel(
    *,
    application: str,
    environment: str,
    schema_version: int,
    capability: str,
) -> str:
    return ":".join(
        (
            _segment(application, fallback="deltallm"),
            _segment(environment, fallback="dev"),
            f"v{max(1, int(schema_version))}",
            _segment(capability, fallback="events"),
        )
    )


def _segment(value: str, *, fallback: str) -> str:
    normalized = _NAMESPACE_SEGMENT_PATTERN.sub("-", str(value).strip().lower()).strip("-")
    return normalized or fallback
