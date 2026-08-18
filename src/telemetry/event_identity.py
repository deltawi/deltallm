from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from starlette.requests import Request


@dataclass(frozen=True, slots=True)
class BillingEventIdentity:
    event_id: str


def get_or_create_billing_event_identity(request: Request) -> BillingEventIdentity:
    """Return the server-owned billing identity for one accepted request."""

    try:
        identity = request.state._billing_event_identity
    except AttributeError:
        identity = BillingEventIdentity(event_id=str(uuid4()))
        request.state._billing_event_identity = identity
    if not isinstance(identity, BillingEventIdentity):
        raise RuntimeError("request billing identity has an invalid type")
    return identity


def get_or_create_billing_event_id(request: Request) -> str:
    return get_or_create_billing_event_identity(request).event_id
