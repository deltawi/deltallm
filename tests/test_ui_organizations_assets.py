from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.services.callable_targets import CallableTarget
from src.services.asset_ownership import owner_scope_from_metadata


class _FakeAdminDB:
    def __init__(self) -> None:
        self.organizations: dict[str, dict[str, Any]] = {}

    async def execute_raw(self, query: str, *params):
        if "INSERT INTO deltallm_organizationtable" in query:
            (
                organization_id,
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
                reset_fields_provided,
            ) = params
            row = self.organizations.get(organization_id)
            if row is not None:
                next_metadata = _merge_upsert_metadata(
                    row.get("metadata"),
                    metadata,
                    budget_duration=budget_duration,
                    reset_fields_provided=bool(reset_fields_provided),
                )
                row.update(
                    {
                        "organization_name": organization_name,
                        "max_budget": max_budget,
                        "soft_budget": soft_budget,
                        "budget_duration": budget_duration if reset_fields_provided else row.get("budget_duration"),
                        "budget_reset_at": budget_reset_at if reset_fields_provided else row.get("budget_reset_at"),
                        "rpm_limit": rpm_limit,
                        "tpm_limit": tpm_limit,
                        "rph_limit": rph_limit,
                        "rpd_limit": rpd_limit,
                        "tpd_limit": tpd_limit,
                        "model_rpm_limit": model_rpm_limit,
                        "model_tpm_limit": model_tpm_limit,
                        "audit_content_storage_enabled": bool(audit_content_storage_enabled),
                        "metadata": next_metadata or {},
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
                return 1

            self.organizations[organization_id] = {
                "organization_id": organization_id,
                "organization_name": organization_name,
                "max_budget": max_budget,
                "soft_budget": soft_budget,
                "budget_duration": budget_duration,
                "budget_reset_at": budget_reset_at,
                "spend": 0.0,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "rph_limit": rph_limit,
                "rpd_limit": rpd_limit,
                "tpd_limit": tpd_limit,
                "model_rpm_limit": model_rpm_limit,
                "model_tpm_limit": model_tpm_limit,
                "audit_content_storage_enabled": bool(audit_content_storage_enabled),
                "metadata": metadata or {},
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE deltallm_organizationtable" in query:
            (
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
                organization_id,
            ) = params
            row = self.organizations[organization_id]
            row.update(
                {
                    "organization_name": organization_name,
                    "max_budget": max_budget,
                    "soft_budget": soft_budget,
                    "budget_duration": budget_duration,
                    "budget_reset_at": budget_reset_at,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "rph_limit": rph_limit,
                    "rpd_limit": rpd_limit,
                    "tpd_limit": tpd_limit,
                    "model_rpm_limit": model_rpm_limit,
                    "model_tpm_limit": model_tpm_limit,
                    "audit_content_storage_enabled": bool(audit_content_storage_enabled),
                    "metadata": metadata or {},
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        return 1

    async def query_raw(self, query: str, *params):
        normalized = " ".join(query.lower().split())
        if "count(*) as total" in normalized and "from deltallm_organizationtable" in normalized:
            return [{"total": len(self.organizations)}]
        if "from deltallm_organizationtable o" in normalized:
            return list(self.organizations.values())
        if "FROM deltallm_organizationtable" in query:
            organization_id = str(params[0])
            row = self.organizations.get(organization_id)
            return [row] if row else []
        return []


def _merge_upsert_metadata(
    existing: Any,
    incoming: Any,
    *,
    budget_duration: str | None,
    reset_fields_provided: bool,
) -> dict[str, Any]:
    if not reset_fields_provided:
        return {**dict(existing or {}), **dict(incoming or {})}
    merged = {**dict(existing or {}), **dict(incoming or {})}
    if budget_duration is None or not budget_duration.endswith("mo"):
        merged.pop("_budget_reset", None)
    return merged


class _FakeRouteGroupRepository:
    def __init__(self) -> None:
        self.groups: dict[str, RouteGroupRecord] = {}
        self.bindings: dict[str, list[RouteGroupBindingRecord]] = {}
        self._group_counter = 0
        self._binding_counter = 0

    async def create_group(self, *, group_key, name, mode, routing_strategy, enabled, metadata):  # noqa: ANN001, ANN201
        self._group_counter += 1
        owner_scope = owner_scope_from_metadata(metadata)
        record = RouteGroupRecord(
            route_group_id=f"rg-{self._group_counter}",
            group_key=group_key,
            name=name,
            mode=mode,
            routing_strategy=routing_strategy,
            enabled=enabled,
            metadata=metadata,
            owner_scope_type=owner_scope.scope_type,
            owner_scope_id=owner_scope.scope_id,
        )
        self.groups[group_key] = record
        return record

    async def get_group(self, group_key: str):  # noqa: ANN201
        return self.groups.get(group_key)

    async def list_groups(self, *, search=None, limit=1000, offset=0):  # noqa: ANN001, ANN201
        del search
        items = list(self.groups.values())[offset : offset + limit]
        return items, len(self.groups)

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=500, offset=0):  # noqa: ANN001, ANN201
        items = [binding for bindings in self.bindings.values() for binding in bindings]
        if group_key:
            items = [binding for binding in items if binding.group_key == group_key]
        if scope_type:
            items = [binding for binding in items if binding.scope_type == scope_type]
        if scope_id:
            items = [binding for binding in items if binding.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        items = self.bindings.setdefault(group_key, [])
        for index, binding in enumerate(items):
            if binding.scope_type == scope_type and binding.scope_id == scope_id:
                updated = replace(binding, enabled=enabled, metadata=metadata)
                items[index] = updated
                return updated
        self._binding_counter += 1
        binding = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{self._binding_counter}",
            route_group_id=group.route_group_id,
            group_key=group.group_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        items.append(binding)
        return binding

    async def delete_binding(self, binding_id: str) -> bool:
        for group_key, items in self.bindings.items():
            kept = [binding for binding in items if binding.route_group_binding_id != binding_id]
            if len(kept) != len(items):
                self.bindings[group_key] = kept
                return True
        return False


class _FakeCallableTargetBindingRepository:
    def __init__(self) -> None:
        self.bindings: list[CallableTargetBindingRecord] = []
        self._counter = 0

    async def list_bindings(self, *, callable_key=None, scope_type=None, scope_id=None, limit=500, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_binding(self, *, callable_key, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        for index, item in enumerate(self.bindings):
            if item.callable_key == callable_key and item.scope_type == scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetBindingRecord(
            callable_target_binding_id=f"ctb-{self._counter}",
            callable_key=callable_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [item for item in self.bindings if item.callable_target_binding_id != binding_id]
        if len(kept) == len(self.bindings):
            return False
        self.bindings = kept
        return True


@pytest.mark.asyncio
async def test_create_organization_applies_route_group_bootstrap_bindings(client, test_app):
    fake_db = _FakeAdminDB()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    await route_groups.create_group(
        group_key="support-route",
        name="Support",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_groups.create_group(
        group_key="sales-route",
        name="Sales",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-route": CallableTarget(key="support-route", target_type="route_group"),
    }
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-asset",
            "organization_name": "Org Asset",
            "route_group_bindings": [
                {"group_key": "support-route"},
                {"group_key": "sales-route", "enabled": False, "metadata": {"source": "bootstrap"}},
            ],
            "callable_target_bindings": [
                {"callable_key": "gpt-4o-mini"},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["route_group_bindings"]) == 2
    assert {item["group_key"] for item in payload["route_group_bindings"]} == {"support-route", "sales-route"}
    assert all(item["scope_type"] == "organization" for item in payload["route_group_bindings"])
    assert all(item["scope_id"] == "org-asset" for item in payload["route_group_bindings"])
    assert {item["callable_key"] for item in payload["callable_target_bindings"]} == {
        "gpt-4o-mini",
        "support-route",
        "sales-route",
    }
    assert all(item["scope_type"] == "organization" for item in payload["callable_target_bindings"])

    detail = await client.get("/ui/api/organizations/org-asset", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["route_group_bindings"]) == 2
    assert len(detail.json()["callable_target_bindings"]) == 3

    visibility = await client.get("/ui/api/organizations/org-asset/asset-visibility", headers=headers)
    assert visibility.status_code == 200
    visibility_payload = visibility.json()
    assert visibility_payload["route_groups"]["total"] == 2
    assert visibility_payload["callable_targets"]["total"] == 3
    assert {item["visibility_source"] for item in visibility_payload["route_groups"]["items"]} == {"granted"}


@pytest.mark.asyncio
async def test_create_organization_persists_soft_budget(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-budget",
            "organization_name": "Org Budget",
            "max_budget": 100.0,
            "soft_budget": 80.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["max_budget"] == 100.0
    assert payload["soft_budget"] == 80.0

    detail = await client.get("/ui/api/organizations/org-budget", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["soft_budget"] == 80.0


@pytest.mark.asyncio
async def test_create_organization_persists_monthly_budget_reset(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "organization_name": "Org Reset",
            "max_budget": 100.0,
            "budget_duration": "1mo",
            "budget_reset_at": "2099-05-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["budget_duration"] == "1mo"
    assert payload["budget_reset_at"] == "2099-05-01T00:00:00Z"
    assert fake_db.organizations["org-reset"]["budget_reset_at"] == datetime(2099, 5, 1)
    assert fake_db.organizations["org-reset"]["metadata"]["_budget_reset"]["monthly_anchor_day"] == 1

    detail = await client.get("/ui/api/organizations/org-reset", headers=headers)
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["budget_duration"] == "1mo"
    assert detail_payload["budget_reset_at"] == "2099-05-01T00:00:00Z"

    listed = await client.get("/ui/api/organizations", headers=headers)
    assert listed.status_code == 200
    listed_payload = listed.json()["data"][0]
    assert listed_payload["budget_reset_at"] == "2099-05-01T00:00:00Z"


@pytest.mark.asyncio
async def test_create_organization_upsert_preserves_reset_when_fields_are_omitted(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "organization_name": "Org Reset",
            "budget_duration": "1mo",
            "budget_reset_at": "2099-01-30T00:00:00Z",
        },
    )

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "organization_name": "Renamed Org Reset",
            "metadata": {"source": "upsert"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_name"] == "Renamed Org Reset"
    assert payload["budget_duration"] == "1mo"
    assert payload["budget_reset_at"] == "2099-01-30T00:00:00Z"
    assert payload["metadata"]["source"] == "upsert"
    assert payload["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30
    stored = fake_db.organizations["org-reset"]
    assert stored["organization_name"] == "Renamed Org Reset"
    assert stored["budget_duration"] == "1mo"
    assert stored["budget_reset_at"] == datetime(2099, 1, 30)
    assert stored["metadata"]["source"] == "upsert"
    assert stored["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30


@pytest.mark.asyncio
async def test_create_organization_upsert_clears_reset_when_fields_are_null(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "1mo",
            "budget_reset_at": "2099-01-30T00:00:00Z",
        },
    )

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": None,
            "budget_reset_at": None,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["budget_duration"] is None
    assert payload["budget_reset_at"] is None
    assert "_budget_reset" not in payload["metadata"]
    stored = fake_db.organizations["org-reset"]
    assert stored["budget_duration"] is None
    assert stored["budget_reset_at"] is None
    assert "_budget_reset" not in stored["metadata"]


@pytest.mark.asyncio
async def test_create_organization_normalizes_budget_reset_to_utc(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "1mo",
            "budget_reset_at": "2099-05-01T01:30:00+03:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["budget_reset_at"] == "2099-04-30T22:30:00Z"
    assert fake_db.organizations["org-reset"]["budget_reset_at"] == datetime(2099, 4, 30, 22, 30)
    assert fake_db.organizations["org-reset"]["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30


@pytest.mark.asyncio
async def test_update_organization_resyncs_route_group_bootstrap_bindings(client, test_app):
    fake_db = _FakeAdminDB()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    await route_groups.create_group(
        group_key="support-route",
        name="Support",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_groups.create_group(
        group_key="sales-route",
        name="Sales",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-route": CallableTarget(key="support-route", target_type="route_group"),
        "sales-route": CallableTarget(key="sales-route", target_type="route_group"),
    }
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-asset",
            "route_group_bindings": [{"group_key": "support-route"}],
            "callable_target_bindings": [{"callable_key": "gpt-4o-mini"}],
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-asset",
        headers=headers,
        json={
            "organization_name": "Updated Org Asset",
            "route_group_bindings": [{"group_key": "sales-route", "enabled": False}],
            "callable_target_bindings": [{"callable_key": "sales-route", "enabled": False}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_name"] == "Updated Org Asset"
    assert len(payload["route_group_bindings"]) == 1
    assert payload["route_group_bindings"][0]["group_key"] == "sales-route"
    assert payload["route_group_bindings"][0]["enabled"] is False
    assert len(payload["callable_target_bindings"]) == 1
    assert payload["callable_target_bindings"][0]["callable_key"] == "sales-route"
    assert payload["callable_target_bindings"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_update_organization_persists_soft_budget(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-budget",
            "max_budget": 100.0,
            "soft_budget": 75.0,
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-budget",
        headers=headers,
        json={
            "organization_name": "Updated Org Budget",
            "soft_budget": 60.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_name"] == "Updated Org Budget"
    assert payload["soft_budget"] == 60.0


@pytest.mark.asyncio
async def test_update_organization_persists_and_clears_monthly_budget_reset(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "1mo",
            "budget_reset_at": "2099-05-01T00:00:00Z",
        },
    )

    update = await client.put(
        "/ui/api/organizations/org-reset",
        headers=headers,
        json={
            "budget_duration": "1mo",
            "budget_reset_at": "2099-06-01T00:00:00Z",
        },
    )

    assert update.status_code == 200
    update_payload = update.json()
    assert update_payload["budget_duration"] == "1mo"
    assert update_payload["budget_reset_at"] == "2099-06-01T00:00:00Z"
    assert fake_db.organizations["org-reset"]["metadata"]["_budget_reset"]["monthly_anchor_day"] == 1

    cleared = await client.put(
        "/ui/api/organizations/org-reset",
        headers=headers,
        json={"budget_duration": None, "budget_reset_at": None},
    )

    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["budget_duration"] is None
    assert cleared_payload["budget_reset_at"] is None
    assert "_budget_reset" not in fake_db.organizations["org-reset"]["metadata"]


@pytest.mark.asyncio
async def test_update_organization_preserves_monthly_anchor_when_reset_fields_are_omitted(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "1mo",
            "budget_reset_at": "2099-01-30T00:00:00Z",
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-reset",
        headers=headers,
        json={"organization_name": "Renamed Org Reset"},
    )

    assert response.status_code == 200
    assert response.json()["budget_reset_at"] == "2099-01-30T00:00:00Z"
    assert fake_db.organizations["org-reset"]["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30


@pytest.mark.asyncio
async def test_update_organization_preserves_budget_reset_when_fields_are_omitted(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "organization_name": "Org Reset",
            "budget_duration": "14d",
            "budget_reset_at": "2099-05-01T00:00:00Z",
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-reset",
        headers=headers,
        json={"organization_name": "Renamed Org Reset"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_name"] == "Renamed Org Reset"
    assert payload["budget_duration"] == "14d"
    assert payload["budget_reset_at"].startswith("2099-05-01T00:00:00")


@pytest.mark.asyncio
async def test_update_organization_preserves_custom_month_reset_when_fields_are_omitted(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "organization_name": "Org Reset",
            "budget_duration": "3mo",
            "budget_reset_at": "2099-05-31T00:00:00Z",
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-reset",
        headers=headers,
        json={"organization_name": "Renamed Org Reset"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_name"] == "Renamed Org Reset"
    assert payload["budget_duration"] == "3mo"
    assert payload["budget_reset_at"].startswith("2099-05-31T00:00:00")
    assert fake_db.organizations["org-reset"]["metadata"]["_budget_reset"]["monthly_anchor_day"] == 31


@pytest.mark.asyncio
async def test_create_organization_rejects_soft_budget_above_max_budget(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-budget",
            "max_budget": 50.0,
            "soft_budget": 60.0,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "soft_budget must be less than or equal to max_budget"


@pytest.mark.asyncio
async def test_create_organization_rejects_invalid_budget_reset_fields(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    invalid_duration = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "monthly",
            "budget_reset_at": "2099-05-01T00:00:00Z",
        },
    )
    out_of_range_duration = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-reset",
            "budget_duration": "10001d",
            "budget_reset_at": "2099-05-01T00:00:00Z",
        },
    )
    missing_reset_at = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-reset", "budget_duration": "1mo"},
    )
    missing_duration = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-reset", "budget_reset_at": "2099-05-01T00:00:00Z"},
    )

    assert invalid_duration.status_code == 400
    assert invalid_duration.json()["detail"] == "budget_duration must be a positive integer up to 10000 followed by h, d, or mo"
    assert out_of_range_duration.status_code == 400
    assert out_of_range_duration.json()["detail"] == "budget_duration must be a positive integer up to 10000 followed by h, d, or mo"
    assert missing_reset_at.status_code == 400
    assert missing_reset_at.json()["detail"] == "budget_reset_at is required when budget_duration is set"
    assert missing_duration.status_code == 400
    assert missing_duration.json()["detail"] == "budget_duration is required when budget_reset_at is set"


@pytest.mark.asyncio
async def test_organization_asset_visibility_includes_owned_route_groups(client, test_app):
    fake_db = _FakeAdminDB()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    await route_groups.create_group(
        group_key="owned-route",
        name="Owned",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata={"_asset_governance": {"owner_scope_type": "organization", "owner_scope_id": "org-asset"}},
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-asset"},
    )

    visibility = await client.get("/ui/api/organizations/org-asset/asset-visibility", headers=headers)

    assert visibility.status_code == 200
    route_groups_payload = visibility.json()["route_groups"]["items"]
    assert len(route_groups_payload) == 1
    assert route_groups_payload[0]["group_key"] == "owned-route"
    assert route_groups_payload[0]["visibility_source"] == "owned"


@pytest.mark.asyncio
async def test_create_organization_rejects_unknown_route_group_binding(client, test_app):
    fake_db = _FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-asset",
            "route_group_bindings": [{"group_key": "missing-route"}],
        },
    )

    assert response.status_code == 400
    assert "route_group_bindings.group_key does not exist" in response.text
    assert fake_db.organizations == {}


@pytest.mark.asyncio
async def test_create_organization_rejects_unknown_callable_target_binding(client, test_app):
    fake_db = _FakeAdminDB()
    route_groups = _FakeRouteGroupRepository()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {"gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model")}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-asset",
            "callable_target_bindings": [{"callable_key": "missing-target"}],
        },
    )

    assert response.status_code == 400
    assert "callable_target_bindings.callable_key does not exist" in response.text
    assert fake_db.organizations == {}


@pytest.mark.asyncio
async def test_update_organization_rejects_invalid_bindings_without_persisting_changes(client, test_app):
    fake_db = _FakeAdminDB()
    route_groups = _FakeRouteGroupRepository()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_catalog = {"gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model")}
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={
            "organization_id": "org-asset",
            "organization_name": "Stable Org",
            "max_budget": 100.0,
        },
    )

    response = await client.put(
        "/ui/api/organizations/org-asset",
        headers=headers,
        json={
            "organization_name": "Broken Update",
            "route_group_bindings": [{"group_key": "missing-route"}],
        },
    )

    assert response.status_code == 400
    assert fake_db.organizations["org-asset"]["organization_name"] == "Stable Org"
    assert fake_db.organizations["org-asset"]["max_budget"] == 100.0
