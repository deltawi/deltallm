from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from src.auth.sso import InMemoryUserRepository, SSOAuthHandler, SSOConfig, SSOProvider

from src.auth.sso_group_claims import decode_id_token_claims, extract_claim_values
from src.auth.sso_group_settings import SSOGroupSyncSettings


def _id_token(claims: dict[str, object]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.signature"


def test_id_token_claims_require_audience_and_unexpired_token() -> None:
    claims = {"aud": "client-id", "exp": 2_000, "deltallm_role": ["admin"]}
    assert decode_id_token_claims(_id_token(claims), client_id="client-id", now=1_000) == claims
    assert (
        decode_id_token_claims(
            _id_token({**claims, "aud": ["other", "client-id"]}), client_id="client-id", now=1_000
        )
        is not None
    )
    assert decode_id_token_claims(_id_token(claims), client_id="other", now=1_000) is None
    assert decode_id_token_claims(_id_token(claims), client_id="client-id", now=2_000) is None


@pytest.mark.parametrize("token", [None, "", "a.b", "a.!!!.c", "a." + "x" * 20_000 + ".c"])
def test_malformed_id_tokens_fall_back(token: object) -> None:
    assert decode_id_token_claims(token, client_id="client-id", now=0) is None


def test_extract_claim_values_supports_kanidm_joins_and_nested_paths() -> None:
    assert extract_claim_values([{"r": ["a", "b", "a", " "]}], "r") == ["a", "b"]
    assert extract_claim_values([{"r": "a,b, c"}], "r") == ["a", "b", "c"]
    assert extract_claim_values([{"r": "DeltaLLM Admins"}], "r") == ["DeltaLLM Admins"]
    assert extract_claim_values([{"realm_access": {"roles": ["x"]}}], "realm_access.roles") == ["x"]
    assert extract_claim_values([{"a.b": ["literal"]}], "a.b") == ["literal"]


def test_extract_claim_values_uses_first_source_with_the_claim() -> None:
    assert extract_claim_values([{"other": 1}, {"r": ["userinfo"]}], "r") == ["userinfo"]
    assert extract_claim_values([{"r": ["id-token"]}, {"r": ["userinfo"]}], "r") == ["id-token"]


def test_missing_and_empty_claims_are_different() -> None:
    assert extract_claim_values([{"email": "x"}], "r") is None
    assert extract_claim_values([{"r": {"not": "a list"}}], "r") is None
    assert extract_claim_values([{"r": []}], "r") == []


def test_settings_validate_targets_roles_and_overlap() -> None:
    settings = SSOGroupSyncSettings.model_validate(
        {
            "enabled": True,
            "claim": "deltallm_role",
            "admin_values": "admin",
            "mappings": [
                {"value": "team-ml", "team_id": "team_ml", "team_role": "team_developer"},
                {"value": "finance", "organization_id": "org_acme", "org_role": "org_billing"},
            ],
        }
    )
    assert settings.admin_values == ["admin"]
    assert settings.mapped_team_ids == {"team_ml"}
    assert settings.mapped_organization_ids == {"org_acme"}

    invalid = [
        {"value": "x", "team_id": "t", "organization_id": "o", "team_role": "team_viewer"},
        {"value": "x"},
        {"value": "x", "team_id": "t", "team_role": "org_owner"},
        {"value": "x", "organization_id": "o", "org_role": "team_admin"},
        {"value": "admin", "team_id": "t", "team_role": "team_viewer"},
        {"value": " ", "team_id": "t", "team_role": "team_viewer"},
    ]
    for mapping in invalid:
        with pytest.raises(ValidationError):
            SSOGroupSyncSettings.model_validate({"admin_values": ["admin"], "mappings": [mapping]})
    with pytest.raises(ValidationError):
        SSOGroupSyncSettings.model_validate({"claim": "bad claim"})
    with pytest.raises(ValidationError):
        SSOGroupSyncSettings.model_validate({"unknown": True})


class _TokenClient:
    def __init__(self, token: dict[str, object]) -> None:
        self.token = token

    async def post(self, url: str, data: dict[str, str]):
        del url, data
        return httpx.Response(200, json=self.token)

    async def get(self, url: str, headers: dict[str, str]):
        del url, headers
        return httpx.Response(200, json={"email": "a@example.com", "groups": ["userinfo"]})


@pytest.mark.asyncio
async def test_sso_callback_returns_id_token_then_userinfo_claim_sources() -> None:
    claims = {"aud": "client-id", "exp": 4_102_444_800, "deltallm_role": ["admin"]}
    handler = SSOAuthHandler(
        config=SSOConfig(
            provider=SSOProvider.GENERIC_OIDC,
            client_id="client-id",
            client_secret="secret",
            authorize_url="https://sso.example.com/authorize",
            token_url="https://sso.example.com/token",
            userinfo_url="https://sso.example.com/userinfo",
            redirect_uri="https://proxy.example.com/auth/callback",
        ),
        user_repository=InMemoryUserRepository(),
        http_client=_TokenClient({"access_token": "at", "id_token": _id_token(claims)}),
    )
    payload = await handler.handle_callback("code")
    sources = payload["claim_sources"]
    assert extract_claim_values(sources, "deltallm_role") == ["admin"]
    assert extract_claim_values(sources, "groups") == ["userinfo"]
