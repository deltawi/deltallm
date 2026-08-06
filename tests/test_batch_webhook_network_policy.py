from __future__ import annotations

import asyncio

import pytest

from src.batch.webhooks.models import parse_batch_webhook_request
from src.batch.webhooks.network_policy import (
    BatchWebhookNetworkPolicy,
    BatchWebhookNetworkPolicyError,
)


SECRET = "s" * 32


def _config(url: str, *, ports: list[int] | None = None):  # noqa: ANN202
    return parse_batch_webhook_request(
        {"url": url, "signing_secret": SECRET},
        allow_http=True,
        allowed_ports=ports,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "fec0::1",
        "ff02::1",
        "::",
    ],
)
async def test_network_policy_denies_non_public_ipv4_and_ipv6(address: str) -> None:
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return (address,)

    policy = BatchWebhookNetworkPolicy(resolver=resolver)
    with pytest.raises(BatchWebhookNetworkPolicyError, match="resolved_address_not_allowed"):
        await policy.resolve(_config("https://customer.example/hook"), attempt_count=1)


@pytest.mark.asyncio
async def test_network_policy_rejects_mixed_public_private_answers() -> None:
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("93.184.216.34", "10.0.0.1")

    policy = BatchWebhookNetworkPolicy(resolver=resolver)
    with pytest.raises(BatchWebhookNetworkPolicyError, match="resolved_address_not_allowed"):
        await policy.resolve(_config("https://customer.example/hook"), attempt_count=1)


@pytest.mark.asyncio
async def test_network_policy_pins_ip_and_preserves_host_and_sni() -> None:
    calls = 0

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        assert (hostname, port) == ("customer.example", 8443)
        return ("2001:4860:4860::8888", "93.184.216.34")

    policy = BatchWebhookNetworkPolicy(
        allowed_ports=[8443],
        resolver=resolver,
    )
    target = await policy.resolve(
        _config("https://customer.example:8443/hook?source=batch", ports=[8443]),
        attempt_count=2,
    )

    assert calls == 1
    assert target.connection_url == "https://[2001:4860:4860::8888]:8443/hook?source=batch"
    assert target.host_header == "customer.example:8443"
    assert target.sni_hostname == "customer.example"


@pytest.mark.asyncio
async def test_network_policy_re_resolves_and_blocks_dns_rebinding() -> None:
    answers = iter((("93.184.216.34",), ("10.0.0.1",)))

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return next(answers)

    policy = BatchWebhookNetworkPolicy(resolver=resolver)
    config = _config("https://customer.example/hook")

    assert (await policy.resolve(config, attempt_count=1)).address.compressed == "93.184.216.34"
    with pytest.raises(BatchWebhookNetworkPolicyError, match="resolved_address_not_allowed"):
        await policy.resolve(config, attempt_count=2)


@pytest.mark.asyncio
async def test_network_policy_supports_explicit_private_allowlist_but_not_metadata() -> None:
    async def private_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("10.20.30.40",)

    allowed = BatchWebhookNetworkPolicy(
        allow_http=True,
        allowed_ports=[8080],
        allowed_private_cidrs=["10.0.0.0/8"],
        resolver=private_resolver,
    )
    target = await allowed.resolve(
        _config("http://internal.example:8080/hook", ports=[8080]),
        attempt_count=1,
    )
    assert target.connection_url == "http://10.20.30.40:8080/hook"

    async def metadata_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("169.254.169.254",)

    metadata_policy = BatchWebhookNetworkPolicy(
        allowed_private_cidrs=["0.0.0.0/0"],
        resolver=metadata_resolver,
    )
    with pytest.raises(BatchWebhookNetworkPolicyError, match="resolved_address_not_allowed"):
        await metadata_policy.resolve(_config("https://metadata.example/hook"), attempt_count=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "169.254.170.2",
        "169.254.170.23",
        "100.100.100.200",
        "fd00:ec2::254",
        "fd00:ec2::23",
    ],
)
async def test_network_policy_never_allows_metadata_endpoints(address: str) -> None:
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return (address,)

    policy = BatchWebhookNetworkPolicy(
        allowed_private_cidrs=["0.0.0.0/0", "::/0"],
        resolver=resolver,
    )
    with pytest.raises(BatchWebhookNetworkPolicyError, match="resolved_address_not_allowed"):
        await policy.resolve(_config("https://metadata.example/hook"), attempt_count=1)


@pytest.mark.asyncio
async def test_network_policy_rechecks_scheme_and_port_at_delivery() -> None:
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("93.184.216.34",)

    policy = BatchWebhookNetworkPolicy(allowed_ports=[443], resolver=resolver)
    with pytest.raises(BatchWebhookNetworkPolicyError, match="http_not_allowed"):
        await policy.resolve(
            _config("http://customer.example:8080/hook", ports=[8080]),
            attempt_count=1,
        )
    with pytest.raises(BatchWebhookNetworkPolicyError, match="port_not_allowed"):
        await policy.resolve(
            _config("https://customer.example:8443/hook", ports=[8443]),
            attempt_count=1,
        )


@pytest.mark.asyncio
async def test_network_policy_bounds_dns_resolution_time() -> None:
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        await asyncio.sleep(1)
        return ("93.184.216.34",)

    policy = BatchWebhookNetworkPolicy(
        resolver=resolver,
        resolution_timeout_seconds=0.01,
    )
    with pytest.raises(OSError, match="dns_resolution_timeout"):
        await policy.resolve(_config("https://customer.example/hook"), attempt_count=1)
