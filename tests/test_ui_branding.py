from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.auth.roles import OrganizationRole, PlatformRole
from src.config import (
    AppConfig,
    GeneralSettings,
    UIBrandingPayload,
    UIBrandingSettings,
    UIBrandingUpdatePayload,
)
from src.config_runtime.loader import deep_merge
from src.models.platform_auth import PlatformAuthContext
from src.services.ui_branding_assets import UIBrandingAssetService, validate_branding_asset


_URL_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_branding_urls.json").read_text(encoding="utf-8")
)


class _FakeDynamicConfigManager:
    def __init__(self, test_app) -> None:  # noqa: ANN001
        self.test_app = test_app
        self.updates: list[tuple[dict[str, object], str]] = []

    async def update_config(
        self,
        update: dict[str, object],
        updated_by: str,
        *,
        transaction_mutation=None,  # noqa: ANN001
    ) -> None:
        if transaction_mutation is not None:
            await transaction_mutation(self.test_app.state.branding_asset_db)
        self.updates.append((deepcopy(update), updated_by))
        current = self.test_app.state.app_config.model_dump(mode="python")
        self.test_app.state.app_config = AppConfig.model_validate(deep_merge(current, update))
        service = getattr(self.test_app.state, "ui_branding_asset_service", None)
        if service is not None:
            await service.on_config_change(
                self.test_app.state.app_config, {"modified": ["general_settings"]}
            )


class _FakeBrandingAssetDB:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.query_count = 0

    async def query_raw(self, _query: str, *_params):  # noqa: ANN201
        self.query_count += 1
        rows = []
        for stored in self.rows.values():
            row = dict(stored)
            encoded = str(row["content_base64"])
            row["content_base64"] = "\n".join(
                encoded[index : index + 76] for index in range(0, len(encoded), 76)
            )
            rows.append(row)
        return rows

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        if "INSERT INTO deltallm_ui_branding_asset" in query:
            (
                asset_key,
                content_type,
                content_base64,
                content_sha256,
                size_bytes,
                filename,
                _updated_by,
            ) = params
            self.rows[str(asset_key)] = {
                "asset_key": asset_key,
                "content_type": content_type,
                "content_base64": content_base64,
                "content_sha256": content_sha256,
                "size_bytes": size_bytes,
                "original_filename": filename,
            }
            return 1
        if "DELETE FROM deltallm_ui_branding_asset" in query:
            return 1 if self.rows.pop(str(params[0]), None) is not None else 0
        raise AssertionError(f"Unexpected query: {query}")


def _configure_branding_app(test_app) -> _FakeDynamicConfigManager:  # noqa: ANN001
    test_app.state.app_config = AppConfig(
        general_settings=GeneralSettings(master_key="BrandingMasterKey2026SecureValue123456")
    )
    manager = _FakeDynamicConfigManager(test_app)
    test_app.state.dynamic_config_manager = manager
    asset_db = _FakeBrandingAssetDB()
    test_app.state.branding_asset_db = asset_db
    test_app.state.ui_branding_asset_service = UIBrandingAssetService(asset_db)
    return manager


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer BrandingMasterKey2026SecureValue123456"}


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext) -> None:
    monkeypatch.setattr(
        "src.middleware.platform_auth.get_platform_auth_context", lambda request: context
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)


def test_ui_branding_settings_normalize_colors_and_asset_urls() -> None:
    branding = UIBrandingSettings(
        logo_mark_url=" /branding/mark.svg ",
        logo_full_url="https://cdn.example.com/brand/full.svg",
        primary_color="#aabbcc",
    )

    assert branding.logo_mark_url == "/branding/mark.svg"
    assert branding.logo_full_url == "https://cdn.example.com/brand/full.svg"
    assert branding.primary_color == "#AABBCC"


@pytest.mark.parametrize("url", _URL_CASES["valid"])
def test_ui_branding_settings_accept_shared_asset_url_contract(url: str) -> None:
    assert UIBrandingSettings(logo_mark_url=url).logo_mark_url == url


@pytest.mark.parametrize("url", _URL_CASES["invalid"])
def test_ui_branding_settings_reject_unsafe_asset_urls(url: str) -> None:
    with pytest.raises(ValueError, match="branding asset URLs"):
        UIBrandingSettings(logo_mark_url=url)


def test_ui_branding_payload_rejects_blank_instance_name() -> None:
    with pytest.raises(ValueError, match="instance_name"):
        UIBrandingPayload(instance_name="   ")
    with pytest.raises(ValueError, match="control characters"):
        UIBrandingPayload(instance_name="Acme\u007fAI")
    with pytest.raises(ValueError, match="instance_name"):
        UIBrandingUpdatePayload(instance_name="   ")


def test_general_settings_enforce_the_same_instance_name_contract() -> None:
    assert GeneralSettings(instance_name=" Acme AI ").instance_name == "Acme AI"
    with pytest.raises(ValueError, match="instance_name"):
        GeneralSettings(instance_name="   ")


@pytest.mark.asyncio
async def test_public_ui_branding_returns_safe_defaults_without_authentication(client) -> None:
    response = await client.get("/ui/api/branding")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "instance_name": "DeltaLLM",
        "logo_mark_url": None,
        "logo_full_url": None,
        "favicon_url": None,
        "primary_color": "#2563EB",
        "secondary_color": "#7C3AED",
        "menu_hover_color": "#F9FAFB",
    }
    assert "master_key" not in response.json()


@pytest.mark.asyncio
async def test_ui_branding_update_requires_platform_admin(client, test_app) -> None:
    _configure_branding_app(test_app)

    response = await client.put("/ui/api/branding", json=UIBrandingUpdatePayload().model_dump())

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ui_branding_update_rejects_organization_admin(client, test_app, monkeypatch) -> None:
    manager = _configure_branding_app(test_app)
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-1",
            email="org-admin@example.com",
            role=PlatformRole.ORG_USER,
            organization_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
            team_memberships=[],
        ),
    )

    response = await client.put("/ui/api/branding", json=UIBrandingUpdatePayload().model_dump())

    assert response.status_code == 403
    assert manager.updates == []


@pytest.mark.asyncio
async def test_ui_branding_update_allows_platform_admin_session(
    client, test_app, monkeypatch
) -> None:
    manager = _configure_branding_app(test_app)
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-admin",
            email="platform-admin@example.com",
            role=PlatformRole.ADMIN,
            organization_memberships=[],
            team_memberships=[],
        ),
    )
    payload = UIBrandingUpdatePayload(instance_name="Admin Brand").model_dump()

    response = await client.put("/ui/api/branding", json=payload)

    assert response.status_code == 200
    assert response.json()["instance_name"] == "Admin Brand"
    assert manager.updates


@pytest.mark.asyncio
async def test_ui_branding_update_persists_normalized_branding(client, test_app) -> None:
    manager = _configure_branding_app(test_app)
    payload = {
        "instance_name": " Acme AI ",
        "primary_color": "#123abc",
        "secondary_color": "#334455",
        "menu_hover_color": "#f0f1f2",
    }

    response = await client.put("/ui/api/branding", headers=_headers(), json=payload)

    assert response.status_code == 200
    assert response.json() == {
        **payload,
        "instance_name": "Acme AI",
        "logo_mark_url": None,
        "logo_full_url": None,
        "favicon_url": None,
        "primary_color": "#123ABC",
        "secondary_color": "#334455",
        "menu_hover_color": "#F0F1F2",
    }
    assert manager.updates == [
        (
            {
                "general_settings": {
                    "instance_name": "Acme AI",
                    "ui_branding": {
                        "primary_color": "#123ABC",
                        "secondary_color": "#334455",
                        "menu_hover_color": "#F0F1F2",
                    },
                }
            },
            "admin_api",
        )
    ]

    public_response = await client.get("/ui/api/branding")
    assert public_response.status_code == 200
    assert public_response.json() == response.json()


@pytest.mark.asyncio
async def test_ui_branding_update_rejects_invalid_color_and_asset_url_injection(
    client, test_app
) -> None:
    _configure_branding_app(test_app)
    payload = UIBrandingUpdatePayload().model_dump()
    payload["logo_mark_url"] = "javascript:alert(1)"

    injected_url = await client.put("/ui/api/branding", headers=_headers(), json=payload)
    invalid_color = await client.put(
        "/ui/api/branding",
        headers=_headers(),
        json={**UIBrandingUpdatePayload().model_dump(), "primary_color": "blue"},
    )

    assert injected_url.status_code == 422
    assert invalid_color.status_code == 422


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_ui_branding_asset_upload_serves_cached_versioned_blob_and_deletes_it(
    client, test_app
) -> None:
    _configure_branding_app(test_app)

    upload = await client.put(
        "/ui/api/branding/assets/logo_mark",
        headers=_headers(),
        files={"file": ("mark.png", _PNG_BYTES, "image/png")},
    )

    assert upload.status_code == 200
    asset_url = upload.json()["logo_mark_url"]
    assert asset_url.startswith("/ui/api/branding/assets/logo_mark?v=")
    assert len(asset_url.rsplit("=", 1)[1]) == 64
    assert test_app.state.branding_asset_db.rows["logo_mark"]["content_base64"] == base64.b64encode(
        _PNG_BYTES
    ).decode("ascii")

    query_count = test_app.state.branding_asset_db.query_count
    first = await client.get(asset_url)
    second = await client.get(asset_url)
    assert first.status_code == 200
    assert first.content == _PNG_BYTES
    assert first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert second.content == _PNG_BYTES
    assert test_app.state.branding_asset_db.query_count == query_count

    head = await client.head(asset_url)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(_PNG_BYTES))
    assert head.headers["etag"] == first.headers["etag"]

    not_modified = await client.get(asset_url, headers={"If-None-Match": first.headers["etag"]})
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert "content-length" not in not_modified.headers

    deleted = await client.delete("/ui/api/branding/assets/logo_mark", headers=_headers())
    assert deleted.status_code == 200
    assert deleted.json()["logo_mark_url"] is None
    missing = await client.get(asset_url)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_ui_branding_asset_upload_requires_platform_admin(client, test_app) -> None:
    _configure_branding_app(test_app)

    response = await client.put(
        "/ui/api/branding/assets/logo_mark",
        files={"file": ("mark.png", _PNG_BYTES, "image/png")},
    )

    assert response.status_code == 401
    assert test_app.state.branding_asset_db.rows == {}


@pytest.mark.asyncio
async def test_ui_branding_asset_upload_rejects_unsafe_svg_and_oversized_files(
    client, test_app
) -> None:
    _configure_branding_app(test_app)
    unsafe_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    unsafe = await client.put(
        "/ui/api/branding/assets/logo_full",
        headers=_headers(),
        files={"file": ("wordmark.svg", unsafe_svg, "image/svg+xml")},
    )
    oversized = await client.put(
        "/ui/api/branding/assets/favicon",
        headers=_headers(),
        files={"file": ("large.png", b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024), "image/png")},
    )

    assert unsafe.status_code == 422
    assert "cannot contain <script>" in unsafe.json()["detail"]
    assert oversized.status_code == 422
    assert oversized.json()["detail"] == "branding assets must be 2 MB or smaller"


@pytest.mark.asyncio
async def test_branding_asset_peer_cache_refreshes_once_then_serves_from_memory() -> None:
    db = _FakeBrandingAssetDB()
    initial_config = AppConfig()
    peer = UIBrandingAssetService(db)
    await peer.initialize(initial_config)
    asset = validate_branding_asset("logo_mark", _PNG_BYTES, original_filename="mark.png")
    await UIBrandingAssetService.upsert_in_transaction(db, asset, updated_by="test")
    changed_config = AppConfig.model_validate(
        deep_merge(
            initial_config.model_dump(mode="python"),
            {
                "general_settings": {
                    "ui_branding": {
                        "logo_mark_url": f"/ui/api/branding/assets/logo_mark?v={asset.content_sha256}"
                    }
                }
            },
        )
    )

    await peer.on_config_change(changed_config, {"modified": ["general_settings"]})
    query_count = db.query_count
    assert (await peer.get_asset("logo_mark", expected_sha256=asset.content_sha256)) == asset
    assert (await peer.get_asset("logo_mark", expected_sha256=asset.content_sha256)) == asset
    assert db.query_count == query_count
