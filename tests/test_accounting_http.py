"""Signed calls have one deadline, fixed endpoints, and finite payload bytes."""

import asyncio

import httpx
import pytest

from src.billing.accounting.transport.accounting_auth import (
    ACCOUNTING_SIGNATURE_HEADER,
    ACCOUNTING_TIMESTAMP_HEADER,
    accounting_signature,
    verify_accounting_signature,
)
from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.telemetry_acceptance import AcceptanceFailure
from src.outbound.network_policy import OutboundNetworkPolicy


def transport(handler):
    async def resolve(hostname, port):
        return ("8.8.8.8",)

    return AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="test-secret",
        transport=httpx.MockTransport(handler),
        network_policy=OutboundNetworkPolicy(
            allow_http=True, allowed_ports=(80,), resolver=resolve
        ),
    )


async def test_transport_signs_exact_bytes_and_never_retries_an_uncertain_admission():
    calls = []

    async def handler(request):
        calls.append(request)
        assert verify_accounting_signature(
            "test-secret",
            timestamp=request.headers[ACCOUNTING_TIMESTAMP_HEADER],
            signature=request.headers[ACCOUNTING_SIGNATURE_HEADER],
            path=request.url.path,
            body=request.content,
        )
        assert request.headers["accept-encoding"] == "identity"
        raise httpx.ReadTimeout("uncertain reply")

    client = transport(handler)
    try:
        with pytest.raises(AccountingProtocolUnavailable) as raised:
            await client.request(
                "/reserve/compact/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
        assert raised.value.reason == AcceptanceFailure.DEADLINE.value and len(calls) == 1
    finally:
        await client.close()


async def test_blocked_transport_obeys_one_deadline_and_cancellation():
    entered, blocked = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await blocked.wait()
        return httpx.Response(200, content=b"[]")

    client = transport(handler)
    try:
        with pytest.raises(AccountingProtocolUnavailable) as raised:
            await client.request(
                "/reserve/compact/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 0.02
            )
        assert raised.value.reason == AcceptanceFailure.DEADLINE.value
        entered.clear()
        task = asyncio.create_task(
            client.request(
                "/reserve/compact/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await client.close()


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"content-length": "1048577"}, b"[]"),
        ({"content-length": "unknown"}, b"[]"),
        ({"content-encoding": "gzip"}, b"[]"),
        ({}, b"x" * 1048577),
    ],
)
async def test_oversized_or_encoded_reply_is_rejected(headers, body):
    async def handler(request):
        return httpx.Response(200, headers=headers, content=body)

    client = transport(handler)
    try:
        with pytest.raises(AccountingProtocolUnavailable):
            await client.request(
                "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
    finally:
        await client.close()


async def test_streamed_response_cannot_hide_an_oversize_body():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(17):
                yield b"x" * 65536

    client = transport(lambda request: httpx.Response(200, stream=Stream()))
    try:
        with pytest.raises(AccountingProtocolUnavailable):
            await client.request(
                "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
    finally:
        await client.close()


async def test_redirect_cannot_change_the_accounting_authority():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "http://other.test"})

    client = transport(handler)
    try:
        with pytest.raises(AccountingProtocolUnavailable):
            await client.request(
                "/reserve/compact/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.parametrize(
    "body,deadline,endpoint",
    [
        (b"x" * 1048577, 1, "/reserve/compact/batch"),
        (b"[]", float("nan"), "/reserve/compact/batch"),
        (b"[]", 1, "/health"),
        (b"[]", 1, "/different"),
    ],
)
async def test_bad_request_never_calls_transport(body, deadline, endpoint):
    calls = []
    client = transport(lambda request: calls.append(request))
    try:
        with pytest.raises((ValueError, AccountingProtocolUnavailable)):
            await client.request(endpoint, body, expires_at=deadline)
        assert calls == []
    finally:
        await client.close()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://accounting.test",
        "http://user:pass@accounting.test",
        "http://accounting.test/path",
        "http://accounting.test?q=1",
        "http://accounting.test#fragment",
    ],
)
def test_transport_requires_one_explicit_origin(url):
    with pytest.raises(ValueError):
        AccountingHttpTransport(service_url=url, signing_secret="secret")


@pytest.mark.parametrize(
    "failure", ["old", "body", "path", "unicode", "huge_timestamp", "missing_secret"]
)
def test_authentication_rejects_changed_and_unbounded_fields(failure):
    timestamp, path, body, secret = "1000", "/internal/accounting/v1/health", b"", "secret"
    signature = accounting_signature(secret, timestamp=timestamp, path=path, body=body)
    if failure == "body":
        body = b"changed"
    elif failure == "path":
        path += "/changed"
    elif failure == "unicode":
        signature = "😀" * 64
    elif failure == "huge_timestamp":
        timestamp = "1" * 100000
    elif failure == "missing_secret":
        secret = ""
    assert not verify_accounting_signature(
        secret,
        timestamp=timestamp,
        signature=signature,
        path=path,
        body=body,
        now=1031 if failure == "old" else 1000,
    )


async def test_private_destination_needs_an_explicit_allowlist_and_pins_the_checked_address():
    async def resolve(hostname, port):
        assert hostname == "accounting.test" and port == 8443
        return ("10.96.1.2",)

    calls = []

    async def handler(request):
        calls.append(request)
        assert request.url.host == "10.96.1.2"
        assert request.headers["host"] == "accounting.test:8443"
        assert request.extensions["sni_hostname"] == "accounting.test"
        return httpx.Response(200, content=b"[]")

    for allowed in (False, True):
        client = AccountingHttpTransport(
            service_url="https://accounting.test:8443",
            signing_secret="test-secret",
            transport=httpx.MockTransport(handler),
            network_policy=OutboundNetworkPolicy(
                allowed_ports=(8443,),
                allowed_private_cidrs=("10.96.0.0/12",) if allowed else (),
                resolver=resolve,
            ),
        )
        try:
            if allowed:
                assert (
                    await client.request(
                        "/finalize/local/batch",
                        b"[]",
                        expires_at=asyncio.get_running_loop().time() + 1,
                    )
                    == b"[]"
                )
            else:
                with pytest.raises(AccountingProtocolUnavailable):
                    await client.request(
                        "/finalize/local/batch",
                        b"[]",
                        expires_at=asyncio.get_running_loop().time() + 1,
                    )
                assert calls == []
        finally:
            await client.close()
    assert len(calls) == 1


async def test_dns_rebinding_to_metadata_is_rejected_before_the_second_request():
    resolutions, calls = [], []

    async def resolve(hostname, port):
        resolutions.append(hostname)
        return ("8.8.8.8",) if len(resolutions) == 1 else ("169.254.169.254",)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"[]")

    client = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="test-secret",
        transport=httpx.MockTransport(handler),
        network_policy=OutboundNetworkPolicy(
            allow_http=True,
            allowed_ports=(80,),
            allowed_private_cidrs=("0.0.0.0/0",),
            resolver=resolve,
        ),
    )
    try:
        await client.request(
            "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
        )
        with pytest.raises(AccountingProtocolUnavailable):
            await client.request(
                "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
        assert len(resolutions) == 2 and len(calls) == 1
    finally:
        await client.close()


async def test_dns_resolution_has_the_same_caller_deadline_as_the_http_call():
    calls = []

    async def resolve(hostname, port):
        await asyncio.Event().wait()

    client = AccountingHttpTransport(
        service_url="https://accounting.test",
        signing_secret="test-secret",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        network_policy=OutboundNetworkPolicy(resolver=resolve),
    )
    try:
        with pytest.raises(AccountingProtocolUnavailable) as raised:
            await client.request(
                "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 0.02
            )
        assert raised.value.reason == AcceptanceFailure.DEADLINE.value and calls == []
    finally:
        await client.close()


async def test_plain_http_needs_explicit_network_policy_permission():
    calls = []
    client = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="test-secret",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    try:
        with pytest.raises(AccountingProtocolUnavailable):
            await client.request(
                "/finalize/local/batch", b"[]", expires_at=asyncio.get_running_loop().time() + 1
            )
        assert calls == []
    finally:
        await client.close()
