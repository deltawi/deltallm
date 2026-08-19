from __future__ import annotations

from enum import StrEnum


class AuditDeliveryClass(StrEnum):
    REQUIRED = "required"
    BEST_EFFORT = "best_effort"


def parse_audit_delivery_class(value: object) -> AuditDeliveryClass:
    try:
        return AuditDeliveryClass(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported audit delivery class: {value}") from exc
