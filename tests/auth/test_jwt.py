from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.auth.jwt import JWTAuthHandler


class MockJWKSHTTPClient:
    def __init__(self, jwks: dict[str, object]) -> None:
        self._jwks = jwks
        self.calls = 0

    async def get(self, url: str):
        self.calls += 1
        assert url == "https://auth.example.com/.well-known/jwks.json"
        return httpx.Response(200, json=self._jwks)


def _build_token_and_jwks() -> tuple[str, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    kid = "kid-1"

    token = jwt.encode(
        {
            "sub": "user-123",
            "email": "user@example.com",
            "team_id": "team-42",
            "role": "internal_user",
            "iss": "https://auth.example.com",
            "aud": "deltallm",
            "exp": int(time.time()) + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )

    jwk_json = jwt.algorithms.RSAAlgorithm.to_jwk(public_key)
    jwk: dict[str, object] = json.loads(jwk_json)
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwks = {"keys": [jwk]}
    return token, jwks


@pytest.mark.asyncio
async def test_jwt_validation_maps_claims_and_uses_jwks_cache():
    token, jwks = _build_token_and_jwks()
    http_client = MockJWKSHTTPClient(jwks)
    handler = JWTAuthHandler(
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        audience="deltallm",
        issuer="https://auth.example.com",
        http_client=http_client,
    )

    first = await handler.validate_token(token)
    second = await handler.validate_token(token)

    assert first["user_id"] == "user-123"
    assert first["email"] == "user@example.com"
    assert first["team_id"] == "team-42"
    assert second["user_id"] == "user-123"
    assert http_client.calls == 1


@pytest.mark.asyncio
async def test_jwt_validation_rejects_invalid_issuer():
    token, jwks = _build_token_and_jwks()
    handler = JWTAuthHandler(
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        audience="deltallm",
        issuer="https://wrong-issuer.example.com",
        http_client=MockJWKSHTTPClient(jwks),
    )

    with pytest.raises(Exception) as exc:
        await handler.validate_token(token)

    assert "Invalid token" in str(exc.value)
