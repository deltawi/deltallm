"""Egress checks for upstream URLs that a non-admin user chose.

Creator-owned model credentials and MCP servers carry URLs that their owner typed. Those
calls must not reach the gateway's private network or cloud metadata. The guard applies
the shared `OutboundNetworkPolicy` (`provider_discovery_*` settings) in three places:

* when the URL is saved (`check_url`),
* before each request to such a URL (`check_url` again, through the transports), and
* when a connection is opened (`GuardedNetworkBackend`), which connects only to the
  address that passed the check, so a DNS answer cannot change between check and use.

A target is restricted when its origin belongs to a creator deployment, or when the call
runs inside `restricted_egress()`. Platform-governed targets keep their current behavior.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from ipaddress import ip_address
import logging
from typing import Any
from urllib.parse import urlsplit

from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError,
    OutboundResolutionError,
)
from src.outbound.urls import normalize_outbound_url

logger = logging.getLogger(__name__)

Origin = tuple[str, int]

_RESTRICTED_CALL: ContextVar[bool] = ContextVar("deltallm_restricted_egress", default=False)


class UserEgressDenied(ValueError):
    """The URL is outside what a user-owned upstream may reach."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@contextmanager
def restricted_egress(enabled: bool = True) -> Iterator[None]:
    """Check every connection opened inside this block, whatever its origin."""

    if not enabled:
        yield
        return
    token = _RESTRICTED_CALL.set(True)
    try:
        yield
    finally:
        _RESTRICTED_CALL.reset(token)


def egress_restricted_call() -> bool:
    return _RESTRICTED_CALL.get()


def url_origin(url: str) -> Origin | None:
    try:
        parsed = urlsplit(normalize_outbound_url(url))
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return (str(parsed.hostname or "").lower(), port)


class UserEgressGuard:
    def __init__(self, policy: OutboundNetworkPolicy) -> None:
        self.policy = policy
        self._origins: dict[str, frozenset[Origin]] = {}
        self._trusted: dict[str, frozenset[Origin]] = {}
        self._restricted: frozenset[Origin] = frozenset()

    def replace_origins(
        self,
        source: str,
        *,
        restricted_urls: Iterable[str],
        trusted_urls: Iterable[str] = (),
    ) -> None:
        """Publish the user-owned origins of one source; trusted origins are never restricted."""

        self._origins[source] = frozenset(
            origin for origin in map(url_origin, restricted_urls) if origin is not None
        )
        self._trusted[source] = frozenset(
            origin for origin in map(url_origin, trusted_urls) if origin is not None
        )
        trusted = frozenset().union(*self._trusted.values())
        self._restricted = frozenset().union(*self._origins.values()) - trusted

    def is_restricted(self, host: str, port: int) -> bool:
        return egress_restricted_call() or (host.lower(), port) in self._restricted

    def static_violation(self, url: str) -> str | None:
        """Reasons a URL fails without DNS: scheme, port, or a literal private address."""

        try:
            parsed = urlsplit(normalize_outbound_url(url))
        except ValueError:
            return "url_invalid"
        scheme = parsed.scheme.lower()
        if scheme == "http" and not self.policy.allow_http:
            return "http_not_allowed"
        port = parsed.port or (443 if scheme == "https" else 80)
        if port not in self.policy.allowed_ports:
            return "port_not_allowed"
        hostname = str(parsed.hostname or "")
        try:
            literal = ip_address(hostname.split("%", 1)[0])
        except ValueError:
            return None
        if not self.policy._address_is_allowed(literal):
            return "resolved_address_not_allowed"
        return None

    async def check_url(self, url: str) -> None:
        try:
            await self.policy.resolve(url)
        except (OutboundPolicyError, OutboundResolutionError) as exc:
            raise UserEgressDenied(getattr(exc, "reason", "url_not_allowed")) from None

    async def connect_address(self, host: str, port: int) -> str:
        try:
            address = await self.policy.resolve_host(host, port)
        except (OutboundPolicyError, OutboundResolutionError) as exc:
            reason = getattr(exc, "reason", "url_not_allowed")
            logger.warning("blocked user-owned upstream connection reason=%s", reason)
            raise UserEgressDenied(reason) from None
        return str(address)


def user_egress_denied_message(reason: str) -> str:
    return (
        "This URL is not allowed for user-owned connections "
        f"({reason}). Ask a platform admin to allow it."
    )


class GuardedNetworkBackend:
    """Wrap an httpcore or httpcore2 network backend; pin restricted connections."""

    def __init__(self, inner: Any, guard: UserEgressGuard, *, connect_error: type[Exception]) -> None:
        self._inner = inner
        self._guard = guard
        self._connect_error = connect_error

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        target = host
        if self._guard.is_restricted(host, port):
            try:
                target = await self._guard.connect_address(host, port)
            except UserEgressDenied as exc:
                # Surface as a connect failure so callers treat it like an unreachable host.
                raise self._connect_error(f"user egress denied: {exc.reason}") from None
        # TLS keeps the original hostname: the pool passes it separately as server_hostname.
        return await self._inner.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> Any:
        return await self._inner.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
