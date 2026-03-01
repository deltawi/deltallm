from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from src.models.errors import RateLimitError


class SSOProvider(str, Enum):
    MICROSOFT_ENTRA = "microsoft"
    GOOGLE = "google"
    OKTA = "okta"
    GENERIC_OIDC = "oidc"


@dataclass(slots=True)
class SSOConfig:
    provider: SSOProvider
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    redirect_uri: str
    scope: str = "openid email profile"
    admin_email_list: list[str] | None = None
    default_team_id: str | None = None


@dataclass(slots=True)
class SSOUser:
    user_id: str
    email: str
    user_role: str
    team_id: str | None = None


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: dict[str, SSOUser] = {}

    async def get_or_create_by_email(self, email: str, defaults: dict[str, Any]) -> SSOUser:
        existing = self._users.get(email)
        if existing is not None:
            return existing

        user = SSOUser(
            user_id=str(defaults.get("user_id") or uuid.uuid4()),
            email=email,
            user_role=str(defaults.get("user_role") or "internal_user"),
            team_id=defaults.get("team_id"),
        )
        self._users[email] = user
        return user


class SSOAuthHandler:
    """Handle OAuth2 login and callback for supported SSO providers."""

    def __init__(
        self,
        config: SSOConfig,
        user_repository: Any,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: Any | None = None,
    ) -> None:
        self.config = config
        self.users = user_repository
        self._http_client = http_client
        self._rate_limiter = rate_limiter

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_authorize_url(self, state: str, code_challenge: str | None = None) -> str:
        params = {
            "client_id": self.config.client_id,
            "response_type": "code",
            "redirect_uri": self.config.redirect_uri,
            "scope": self.config.scope,
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self.config.authorize_url}?{urlencode(params)}"

    async def handle_callback(self, code: str, code_verifier: str | None = None) -> dict[str, Any]:
        token_data = await self._exchange_code(code, code_verifier=code_verifier)
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing access token")

        user_info = await self._get_userinfo(access_token)
        email = user_info.get("email")
        if not email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email not provided by SSO provider")
        await self._enforce_callback_rate_limit(email)

        is_admin = email in (self.config.admin_email_list or [])
        user = await self.users.get_or_create_by_email(
            email=email,
            defaults={
                "user_id": str(uuid.uuid4()),
                "user_role": "proxy_admin" if is_admin else "internal_user",
                "team_id": self.config.default_team_id,
            },
        )

        return {
            "user_id": user.user_id,
            "email": email,
            "role": user.user_role,
            "team_id": user.team_id,
            "token": self._generate_session_token(user),
        }

    async def _exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier

        if self._http_client is not None:
            response = await self._http_client.post(self.config.token_url, data=payload)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to exchange code")
            return response.json()

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.config.token_url, data=payload)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to exchange code")
            return response.json()

    async def _get_userinfo(self, access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}

        if self._http_client is not None:
            response = await self._http_client.get(self.config.userinfo_url, headers=headers)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to get user info")
            return response.json()

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.config.userinfo_url, headers=headers)
            if response.status_code != 200:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to get user info")
            return response.json()

    async def _enforce_callback_rate_limit(self, email: str) -> None:
        if self._rate_limiter is None:
            return
        normalized_email = email.strip().lower()
        if not normalized_email:
            return
        try:
            await self._rate_limiter.check_rate_limit(
                scope="auth_sso_callback_email",
                entity_id=normalized_email,
                limit=10,
            )
        except RateLimitError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if getattr(exc, "retry_after", None) else None
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many SSO login attempts; please try again later",
                headers=headers,
            ) from exc

    def _generate_session_token(self, user: SSOUser) -> str:
        return f"sso:{user.user_id}:{secrets.token_urlsafe(32)}"
