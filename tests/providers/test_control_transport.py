from __future__ import annotations

import asyncio
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.upstream_http import build_control_http_client


@pytest.fixture
def tls_contexts(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "a.example")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("a.example")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, str(cert_path)


@pytest.mark.parametrize("reuse", [False, True])
async def test_shared_control_pool_verifies_each_hostname(tls_contexts, monkeypatch, reuse):
    server_context, cert_path = tls_contexts
    monkeypatch.setenv("SSL_CERT_FILE", cert_path)
    received = []
    handshakes = []
    server_context.set_servername_callback(lambda socket, name, context: handshakes.append(name))
    tasks = set()

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            while True:
                received.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    try:
        server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_context)
    except PermissionError:
        pytest.skip(
            "Local TCP sockets are unavailable; run the loopback TLS lane outside the sandbox"
        )
    port = server.sockets[0].getsockname()[1]
    try:
        # Retain the unsafe old-policy reproduction alongside the fixed factory.
        client = (
            httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=20))
            if reuse
            else build_control_http_client()
        )
        async with client:
            # Exercise the actual client pool; a mock request handler cannot prove
            # certificate verification or detect reuse under an IP origin key.
            response = await client.get(
                f"https://127.0.0.1:{port}/models",
                headers={"Host": "a.example"},
                extensions={"sni_hostname": "a.example"},
            )
            assert response.status_code == 200

            async def request_b():
                return await client.get(
                    f"https://127.0.0.1:{port}/models",
                    headers={"Host": "b.example", "Authorization": "Bearer b-secret"},
                    extensions={"sni_hostname": "b.example"},
                )

            if reuse:
                assert (await request_b()).status_code == 200
            else:
                with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                    await request_b()
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.gather(*tasks)
    assert handshakes == (["a.example"] if reuse else ["a.example", "b.example"])
    assert len(received) == (2 if reuse else 1)
    assert (b"b-secret" in b"".join(received)) is reuse
