from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError


class JWTAuthHandler:
    """Validate JWT bearer tokens against a JWKS endpoint with in-process caching."""

    def __init__(
        self,
        jwks_url: str,
        audience: str | None = None,
        issuer: str | None = None,
        claims_mapping: dict[str, str] | None = None,
        jwks_cache_ttl: int = 3600,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.audience = audience
        self.issuer = issuer
        self.claims_mapping = claims_mapping or {
            "user_id": "sub",
            "email": "email",
            "team_id": "team_id",
            "user_role": "role",
        }
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_time = 0.0
        self._jwks_cache_ttl = jwks_cache_ttl
        self._http_client = http_client

    async def validate_token(self, token: str) -> dict[str, Any]:
        signing_key = await self._get_signing_key(token)
        options = {"verify_aud": self.audience is not None}

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.audience,
                issuer=self.issuer,
                options=options,
            )
        except InvalidTokenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}") from exc

        mapped: dict[str, Any] = {}
        for target_field, claim_name in self.claims_mapping.items():
            mapped[target_field] = payload.get(claim_name)
        mapped["claims"] = payload
        return mapped

    async def _get_signing_key(self, token: str) -> Any:
        jwks = await self._get_jwks()

        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header") from exc

        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing kid in token header")

        for key in jwks.get("keys", []):
            if key.get("kid") != kid:
                continue

            key_json = json.dumps(key)
            key_type = key.get("kty")
            if key_type == "RSA":
                return jwt.algorithms.RSAAlgorithm.from_jwk(key_json)
            if key_type == "EC":
                return jwt.algorithms.ECAlgorithm.from_jwk(key_json)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unsupported key type")

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found")

    async def _get_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks_cache and (now - self._jwks_cache_time) < self._jwks_cache_ttl:
            return self._jwks_cache

        if self._http_client is not None:
            response = await self._http_client.get(self.jwks_url)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch JWKS")
            self._jwks_cache = response.json()
            self._jwks_cache_time = now
            return self._jwks_cache

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.jwks_url)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch JWKS")
            self._jwks_cache = response.json()
            self._jwks_cache_time = now
            return self._jwks_cache
