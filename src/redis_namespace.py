from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import quote


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


def build_redis_key(
    *,
    application: str,
    environment: str,
    schema_version: int,
    capability: str,
    identifiers: Sequence[str] = (),
) -> str:
    """Build a namespaced Redis key while preserving identifier boundaries."""

    namespace = build_redis_channel(
        application=application,
        environment=environment,
        schema_version=schema_version,
        capability=capability,
    )
    encoded_identifiers = ((quote(str(value), safe="-_.~") or "_") for value in identifiers)
    return ":".join((namespace, *encoded_identifiers))


def _segment(value: str, *, fallback: str) -> str:
    normalized = _NAMESPACE_SEGMENT_PATTERN.sub("-", str(value).strip().lower()).strip("-")
    return normalized or fallback
