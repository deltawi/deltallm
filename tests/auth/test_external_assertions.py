from __future__ import annotations

import base64
import json
import secrets

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import jwt
from pydantic import ValidationError
import pytest

from src.auth.external_assertions import ExternalAssertionVerifier
from src.auth.external_config import ExternalAuthSettings, ExternalVerificationKey
from src.auth.external_contracts import ExternalPurpose
from src.auth.external_errors import InvalidExternalAssertion
from src.config import GeneralSettings


NOW = 1800000000


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings_for(private_key: rsa.RSAPrivateKey) -> ExternalAuthSettings:
    pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return ExternalAuthSettings.model_validate(
        {
            "enabled": True,
            "integrations": [
                {
                    "integration_id": "console",
                    "issuer": "https://console.example.com",
                    "identity_issuer": "https://clerk.example.com",
                    "audience": "deltallm",
                    "keys": [{"kid": "rotation-1", "public_key": pem}],
                }
            ],
        }
    )


def valid_claims() -> dict[str, object]:
    return {
        "iss": "https://console.example.com",
        "aud": "deltallm",
        "sub": "user_customer",
        "identity_issuer": "https://clerk.example.com",
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + 60,
        "jti": secrets.token_urlsafe(16),
        "purpose": str(ExternalPurpose.EXCHANGE),
        "binding_id": "binding-123",
        "external_customer_id": "customer-123",
        "email": "Customer@Example.com",
        "email_verified": True,
        "external_session_id": "session_parent",
        "auth_time": NOW - 30,
        "external_session_expires_at": NOW + 50000,
    }


def sign(private_key: rsa.RSAPrivateKey, claims: dict[str, object], **headers: object) -> str:
    return jwt.encode(
        claims, private_key, algorithm="RS256", headers={"kid": "rotation-1", **headers}
    )


def verify(private_key: rsa.RSAPrivateKey, token: str):
    return ExternalAssertionVerifier(settings_for(private_key)).verify(
        token, purposes=[ExternalPurpose.EXCHANGE], now=NOW
    )


def test_verified_identity_uses_exact_issuer_and_redacted_representation(private_key):
    claims = valid_claims()
    result = verify(private_key, sign(private_key, claims))
    assert result.integration_id == "console"
    assert result.claims.email == "customer@example.com"
    assert result.provider.startswith("external:")
    assert len(result.nonce_hash) == 64
    assert result.nonce_hash != result.parent_hash
    assert "Customer" not in repr(result)
    assert str(claims["external_session_id"]) not in repr(result)
    assert str(claims["jti"]) not in repr(result.claims)


@pytest.mark.parametrize(
    "name,value",
    [
        ("iss", "https://console.example.com/"),
        ("aud", ["deltallm"]),
        ("identity_issuer", "https://clerk.example.com/"),
        ("purpose", str(ExternalPurpose.LINK)),
        ("email_verified", False),
        ("email_verified", "true"),
        ("email_verified", 1),
        ("email", "invalid"),
        ("email", "a" * 321 + "@example.com"),
        ("sub", "é" * 101),
        ("sub", " user"),
        ("sub", "user\n"),
        ("binding_id", ""),
        ("jti", "a" * 21),
        ("jti", "!" * 22),
        ("jti", "a" * 201),
        ("iat", True),
        ("iat", str(NOW)),
        ("iat", NOW + 11),
        ("iat", NOW - 71),
        ("nbf", NOW + 11),
        ("nbf", NOW - 1),
        ("exp", NOW),
        ("exp", NOW + 61),
        ("exp", NOW - 11),
        ("auth_time", NOW + 11),
        ("external_session_expires_at", NOW - 30),
    ],
)
def test_invalid_claims_are_rejected_without_decoder_details(private_key, name, value):
    claims = {**valid_claims(), name: value}
    with pytest.raises(InvalidExternalAssertion) as error:
        verify(private_key, sign(private_key, claims))
    assert error.value.code == "invalid_external_assertion"
    assert "Customer" not in str(error.value)


@pytest.mark.parametrize("name", list(valid_claims()))
def test_every_claim_is_required(private_key, name):
    claims = valid_claims()
    del claims[name]
    with pytest.raises(InvalidExternalAssertion):
        verify(private_key, sign(private_key, claims))


@pytest.mark.parametrize(
    "name",
    [
        "role",
        "permissions",
        "account_id",
        "user_id",
        "team_id",
        "organization_id",
        "paid",
        "budget",
        "metadata",
        "unknown",
    ],
)
def test_unsigned_policy_surface_cannot_be_added_to_signed_assertion(private_key, name):
    with pytest.raises(InvalidExternalAssertion):
        verify(private_key, sign(private_key, {**valid_claims(), name: "platform_admin"}))


@pytest.mark.parametrize(
    "headers",
    [
        {"kid": "unknown"},
        {"jku": "https://attacker.example/key"},
        {"x5u": "https://attacker.example/key"},
        {"crit": []},
        {"typ": "at+jwt"},
        {"kid": 1},
    ],
)
def test_untrusted_headers_are_rejected(private_key, headers):
    if headers == {"kid": 1}:
        with pytest.raises(jwt.InvalidTokenError):
            sign(private_key, valid_claims(), **headers)
        return
    with pytest.raises(InvalidExternalAssertion):
        verify(private_key, sign(private_key, valid_claims(), **headers))


def test_hmac_none_wrong_key_and_oversized_assertions_are_rejected(private_key):
    claims = valid_claims()
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [
        jwt.encode(
            claims,
            "hmac-secret-with-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "rotation-1"},
        ),
        jwt.encode(claims, None, algorithm="none", headers={"kid": "rotation-1"}),
        sign(other_key, claims),
        "a" * 8193,
        "bad.encoding",
        "☃",
    ]
    for token in tokens:
        with pytest.raises(InvalidExternalAssertion):
            verify(private_key, token)


def test_duplicate_claims_are_rejected_before_ambiguous_parsing(private_key):
    token = sign(private_key, valid_claims())
    header, payload, signature = token.split(".")
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    ambiguous = decoded[:-1] + ',"email_verified":false}'
    payload = base64.urlsafe_b64encode(ambiguous.encode()).decode().rstrip("=")
    with pytest.raises(InvalidExternalAssertion):
        verify(private_key, f"{header}.{payload}.{signature}")


def test_key_rotation_accepts_only_the_two_startup_keys(private_key):
    configured = settings_for(private_key).model_dump()
    configured["integrations"][0]["keys"] += (
        {**configured["integrations"][0]["keys"][0], "kid": "rotation-2"},
    )
    verifier = ExternalAssertionVerifier(ExternalAuthSettings.model_validate(configured))
    for kid in ("rotation-1", "rotation-2"):
        result = verifier.verify(
            sign(private_key, valid_claims(), kid=kid), purposes=[ExternalPurpose.EXCHANGE], now=NOW
        )
        assert result.integration_id == "console"


def test_config_has_strict_types_and_default_disabled_feature(private_key):
    assert GeneralSettings().external_auth.enabled is False
    configured = settings_for(private_key).model_dump()
    configured.update(
        deployment_protocol="external_customer_v1", allowed_origins=("https://console.example.com",)
    )
    assert (
        GeneralSettings(
            external_auth=configured,
            audit_ingestion_mode="outbox",
            api_key_auth_cache_ttl_seconds=60,
        ).external_auth.enabled
        is True
    )
    with pytest.raises(ValidationError):
        GeneralSettings(external_auth=configured)
    for invalid in (
        {"enabled": True},
        {"enabled": "false"},
        {"unknown": True},
        {"child_lifetime_seconds": 301},
        {"clock_allowance_seconds": 11},
    ):
        with pytest.raises(ValidationError):
            ExternalAuthSettings.model_validate(invalid)
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = (
        weak.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    with pytest.raises(ValidationError):
        ExternalVerificationKey(kid="weak", public_key=pem)


def test_invalid_json_and_array_claims_are_rejected(private_key):
    token = sign(private_key, valid_claims())
    header, _, signature = token.split(".")
    for value in ([], "invalid", {"unknown": True}):
        payload = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
        with pytest.raises(InvalidExternalAssertion):
            verify(private_key, f"{header}.{payload}.{signature}")
