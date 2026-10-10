from __future__ import annotations

import base64
from collections.abc import Sequence
import json
from types import MappingProxyType

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
import jwt
from pydantic import ValidationError

from src.auth.external_config import ExternalAuthSettings
from src.auth.external_contracts import (
    ExternalAssertionClaims,
    ExternalPurpose,
    VerifiedExternalAssertion,
)
from src.auth.external_errors import InvalidExternalAssertion


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate assertion field")
        result[key] = value
    return result


def _decode_object(segment: str) -> dict[str, object]:
    raw = base64.b64decode(segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True)
    result = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValueError("Assertion must be an object")
    return result


class ExternalAssertionVerifier:
    """Fixed startup keys. Token headers never cause network access."""

    def __init__(self, settings: ExternalAuthSettings) -> None:
        self.settings = settings
        self.integrations = MappingProxyType({item.issuer: item for item in settings.integrations})
        keys: dict[tuple[str, str], RSAPublicKey] = {}
        for item in settings.integrations:
            for configured in item.keys:
                key = load_pem_public_key(configured.public_key.encode("ascii"))
                if not isinstance(key, RSAPublicKey):
                    raise ValueError("External auth key is not RSA")
                keys[(item.integration_id, configured.kid)] = key
        self.keys = MappingProxyType(keys)

    def verify(
        self, token: str, *, purposes: Sequence[ExternalPurpose], now: int
    ) -> VerifiedExternalAssertion:
        try:
            if not self.settings.enabled or len(token.encode("utf-8")) > 8192:
                raise ValueError("External assertion is unavailable or too large")
            header_segment, payload_segment, signature = token.split(".")
            if not signature or len(header_segment) > 1024:
                raise ValueError("Invalid assertion encoding")
            header = _decode_object(header_segment)
            if (
                set(header) - {"alg", "typ", "kid"}
                or header.get("alg") != "RS256"
                or header.get("typ") != "JWT"
                or not isinstance(header.get("kid"), str)
            ):
                raise ValueError("Invalid assertion header")
            claims = ExternalAssertionClaims.model_validate(_decode_object(payload_segment))
            integration = self.integrations.get(claims.iss)
            if integration is None or claims.identity_issuer != integration.identity_issuer:
                raise ValueError("Untrusted assertion issuer")
            key = self.keys.get((integration.integration_id, header["kid"]))
            if key is None or claims.aud != integration.audience:
                raise ValueError("Unknown assertion key or audience")
            # Time/type checks use the explicit clock below, after the fixed-key signature check.
            jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=integration.issuer,
                audience=integration.audience,
                options={"verify_exp": False, "verify_nbf": False, "verify_iat": False},
            )
            self._validate_claims(claims, purposes=purposes, now=now)
            return VerifiedExternalAssertion(integration.integration_id, claims)
        except (
            RecursionError,
            ValueError,
            TypeError,
            UnicodeError,
            jwt.InvalidTokenError,
            ValidationError,
        ) as exc:
            raise InvalidExternalAssertion() from exc

    def _validate_claims(
        self, claims: ExternalAssertionClaims, *, purposes: Sequence[ExternalPurpose], now: int
    ) -> None:
        allowance = self.settings.clock_allowance_seconds
        if (
            claims.purpose not in purposes
            or claims.email_verified is not True
            or not claims.iat <= claims.nbf < claims.exp
            or claims.exp - claims.iat > self.settings.assertion_lifetime_seconds
            or claims.iat > now + allowance
            or claims.nbf > now + allowance
            or claims.exp <= now - allowance
            or now - claims.iat > self.settings.assertion_lifetime_seconds + allowance
            or claims.auth_time > now + allowance
            or claims.external_session_expires_at <= claims.auth_time
        ):
            raise ValueError("Invalid assertion claims")
