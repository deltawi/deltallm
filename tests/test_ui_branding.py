from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.auth.roles import OrganizationRole, PlatformRole
from src.audit.actions import AuditAction
from src.config import (
    AppConfig,
    GeneralSettings,
    UIBrandingPayload,
    UIBrandingResetPayload,
    UIBrandingSettings,
    UIBrandingUpdatePayload,
)
from src.config_runtime.dynamic import DynamicConfigPostCommitApplyError
from src.config_runtime.loader import deep_merge
from src.db.ui_branding_assets import UIBrandingAssetRepository
from src.models.platform_auth import PlatformAuthContext
from src.services.ui_branding_assets import UIBrandingAssetService, validate_branding_asset


_URL_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_branding_urls.json").read_text(encoding="utf-8")
)


class _FakeDynamicConfigManager:
    def __init__(self, test_app) -> None:  # noqa: ANN001
        self.test_app = test_app
        self.updates: list[tuple[dict[str, object], str]] = []
        self.transaction_mutation_count = 0
        self.committed_apply_failure = False
        self.precommit_error: Exception | None = None

    async def update_config(
        self,
        update: dict[str, object],
        updated_by: str,
        *,
        transaction_mutation=None,  # noqa: ANN001
    ) -> None:
        if self.precommit_error is not None:
            raise self.precommit_error
        if transaction_mutation is not None:
            self.transaction_mutation_count += 1
            await transaction_mutation(self.test_app.state.branding_asset_db)
        self.updates.append((deepcopy(update), updated_by))
        current = self.test_app.state.app_config.model_dump(mode="python")
        next_config = AppConfig.model_validate(deep_merge(current, update))
        if self.committed_apply_failure:
            raise DynamicConfigPostCommitApplyError(next_config)
        self.test_app.state.app_config = next_config
        service = getattr(self.test_app.state, "ui_branding_asset_service", None)
        if service is not None:
            await service.on_config_change(
                self.test_app.state.app_config, {"modified": ["general_settings"]}
            )


class _FakeAuditService:
    def __init__(self) -> None:
        self.sync_events: list[object] = []
        self.async_events: list[object] = []
        self.fail_sync = False
        self.fail_async = False

    async def record_event_sync(  # noqa: ANN201
        self,
        event,  # noqa: ANN001
        *,
        payloads=None,  # noqa: ANN001
        repository=None,  # noqa: ANN001
    ):
        del payloads, repository
        if self.fail_sync:
            raise RuntimeError("audit persistence unavailable")
        self.sync_events.append(event)

    async def enqueue_event(  # noqa: ANN201
        self,
        event,  # noqa: ANN001
        *,
        payloads=None,  # noqa: ANN001
        delivery_class=None,  # noqa: ANN001
    ):
        del payloads, delivery_class
        if self.fail_async:
            raise RuntimeError("audit outcome unavailable")
        self.async_events.append(event)
        return "queued"


class _FakeBrandingAssetDB:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.query_count = 0
        self.bulk_delete_count = 0

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
            if len(params) > 1:
                self.bulk_delete_count += 1
                deleted = sum(1 for asset_key in params if str(asset_key) in self.rows)
                for asset_key in params:
                    self.rows.pop(str(asset_key), None)
                return deleted
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
    test_app.state.audit_service = _FakeAuditService()
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
        "primary_color": "#5B50D6",
        "secondary_color": "#8B7CFF",
        "menu_hover_color": "#F7F5FF",
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


@pytest.mark.asyncio
async def test_ui_branding_reset_requires_platform_admin(client, test_app) -> None:
    manager = _configure_branding_app(test_app)

    response = await client.post("/ui/api/branding/reset")

    assert response.status_code == 401
    assert manager.updates == []
    assert manager.transaction_mutation_count == 0


@pytest.mark.asyncio
async def test_ui_branding_reset_rejects_organization_admin(client, test_app, monkeypatch) -> None:
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

    response = await client.post("/ui/api/branding/reset")

    assert response.status_code == 403
    assert manager.updates == []
    assert manager.transaction_mutation_count == 0


@pytest.mark.asyncio
async def test_ui_branding_reset_allows_platform_admin_session(
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

    response = await client.post("/ui/api/branding/reset")

    assert response.status_code == 200
    assert response.json() == UIBrandingResetPayload().model_dump()
    assert manager.transaction_mutation_count == 1


@pytest.mark.asyncio
async def test_ui_branding_reset_atomically_restores_defaults_and_deletes_assets(
    client, test_app, monkeypatch
) -> None:
    manager = _configure_branding_app(test_app)
    test_app.state.app_config = AppConfig(
        general_settings=GeneralSettings(
            master_key="BrandingMasterKey2026SecureValue123456",
            instance_name="Acme AI",
            log_level="DEBUG",
            ui_branding=UIBrandingSettings(
                primary_color="#112233",
                secondary_color="#445566",
                menu_hover_color="#778899",
            ),
        )
    )
    asset_urls: list[str] = []
    for asset_key in ("logo_mark", "logo_full", "favicon"):
        uploaded = await client.put(
            f"/ui/api/branding/assets/{asset_key}",
            headers=_headers(),
            files={"file": (f"{asset_key}.png", _PNG_BYTES, "image/png")},
        )
        assert uploaded.status_code == 200
        asset_urls.append(str(uploaded.json()[f"{asset_key}_url"]))

    captured_audits: list[dict[str, object]] = []

    async def capture_audit(**kwargs) -> None:  # noqa: ANN003
        captured_audits.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", capture_audit)
    updates_before_reset = len(manager.updates)

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 200
    assert response.json() == UIBrandingResetPayload().model_dump()
    assert len(manager.updates) == updates_before_reset + 1
    assert manager.updates[-1] == (
        {
            "general_settings": {
                "instance_name": "DeltaLLM",
                "ui_branding": {
                    "logo_mark_url": None,
                    "logo_full_url": None,
                    "favicon_url": None,
                    "primary_color": "#5B50D6",
                    "secondary_color": "#8B7CFF",
                    "menu_hover_color": "#F7F5FF",
                },
            }
        },
        "admin_api",
    )
    assert test_app.state.app_config.general_settings.log_level == "DEBUG"
    assert test_app.state.branding_asset_db.rows == {}
    assert test_app.state.branding_asset_db.bulk_delete_count == 1
    for asset_url in asset_urls:
        assert (await client.get(asset_url)).status_code == 404

    assert len(captured_audits) == 2
    attempt, outcome = captured_audits
    assert attempt["action"] is AuditAction.ADMIN_UI_BRANDING_RESET
    assert attempt["resource_type"] == "ui_branding"
    assert attempt["request_payload"] == {"target": "factory_defaults"}
    assert attempt["status"] == "attempted"
    assert attempt["force_sync"] is True
    assert attempt["critical"] is True
    assert attempt["metadata"]["phase"] == "attempt"
    assert attempt["before"] == {
        "instance_name": "Acme AI",
        "logo_mark_url": asset_urls[0],
        "logo_full_url": asset_urls[1],
        "favicon_url": asset_urls[2],
        "primary_color": "#112233",
        "secondary_color": "#445566",
        "menu_hover_color": "#778899",
    }
    assert outcome["status"] == "success"
    assert outcome["critical"] is False
    assert outcome["metadata"]["phase"] == "outcome"
    assert outcome["metadata"]["reconciliation_pending"] is False
    assert outcome["metadata"]["operation_id"] == attempt["metadata"]["operation_id"]
    assert outcome["after"] == UIBrandingPayload().model_dump()

    repeated = await client.post("/ui/api/branding/reset", headers=_headers())
    assert repeated.status_code == 200
    assert repeated.json() == UIBrandingResetPayload().model_dump()
    assert test_app.state.branding_asset_db.rows == {}
    assert test_app.state.branding_asset_db.bulk_delete_count == 2


@pytest.mark.asyncio
async def test_ui_branding_reset_requires_available_dependencies(client, test_app) -> None:
    manager = _configure_branding_app(test_app)
    del test_app.state.dynamic_config_manager

    missing_config = await client.post("/ui/api/branding/reset", headers=_headers())

    assert missing_config.status_code == 503
    assert missing_config.json()["detail"] == "Config manager unavailable"
    assert manager.updates == []

    test_app.state.dynamic_config_manager = manager
    del test_app.state.ui_branding_asset_service
    missing_assets = await client.post("/ui/api/branding/reset", headers=_headers())

    assert missing_assets.status_code == 503
    assert missing_assets.json()["detail"] == "Branding asset service unavailable"
    assert manager.updates == []


@pytest.mark.asyncio
async def test_ui_branding_reset_requires_audit_when_enabled(client, test_app) -> None:
    manager = _configure_branding_app(test_app)
    test_app.state.audit_service = None

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audit_persistence_unavailable"
    assert manager.updates == []
    assert manager.transaction_mutation_count == 0


@pytest.mark.asyncio
async def test_ui_branding_reset_allows_explicitly_disabled_audit(client, test_app) -> None:
    manager = _configure_branding_app(test_app)
    current = test_app.state.app_config.model_dump(mode="python")
    test_app.state.app_config = AppConfig.model_validate(
        deep_merge(current, {"general_settings": {"audit_enabled": False}})
    )
    test_app.state.audit_service = None

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 200
    assert response.json() == UIBrandingResetPayload().model_dump()
    assert manager.transaction_mutation_count == 1


@pytest.mark.asyncio
async def test_ui_branding_reset_fails_before_mutation_when_attempt_audit_fails(
    client, test_app
) -> None:
    manager = _configure_branding_app(test_app)
    test_app.state.audit_service.fail_sync = True

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audit_persistence_unavailable"
    assert manager.updates == []
    assert manager.transaction_mutation_count == 0


@pytest.mark.asyncio
async def test_ui_branding_reset_returns_committed_state_when_local_apply_fails(
    client, test_app
) -> None:
    manager = _configure_branding_app(test_app)
    manager.committed_apply_failure = True
    before = test_app.state.app_config

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 200
    assert response.json() == UIBrandingResetPayload(reconciliation_pending=True).model_dump()
    assert test_app.state.app_config is before
    assert manager.transaction_mutation_count == 1
    assert test_app.state.branding_asset_db.bulk_delete_count == 1
    audit_service = test_app.state.audit_service
    assert len(audit_service.sync_events) == 1
    assert len(audit_service.async_events) == 1
    assert audit_service.async_events[0].status == "success"
    assert audit_service.async_events[0].metadata["reconciliation_pending"] is True


@pytest.mark.asyncio
async def test_ui_branding_reset_outcome_audit_failure_is_non_fatal(client, test_app) -> None:
    manager = _configure_branding_app(test_app)
    test_app.state.audit_service.fail_async = True

    response = await client.post("/ui/api/branding/reset", headers=_headers())

    assert response.status_code == 200
    assert response.json() == UIBrandingResetPayload().model_dump()
    assert manager.transaction_mutation_count == 1


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
    await UIBrandingAssetRepository(db).upsert(
        asset_key=asset.asset_key,
        content_type=asset.content_type,
        content=asset.content,
        content_sha256=asset.content_sha256,
        size_bytes=asset.size_bytes,
        original_filename=asset.original_filename,
        updated_by="test",
    )
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

    await UIBrandingAssetRepository(db).delete_all_known()
    await peer.on_config_change(initial_config, {"modified": ["general_settings"]})

    assert await peer.get_asset("logo_mark", expected_sha256=asset.content_sha256) is None
    assert db.rows == {}
