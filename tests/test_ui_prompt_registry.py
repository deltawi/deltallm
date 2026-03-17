from __future__ import annotations

from dataclasses import replace

import pytest

from src.db.prompt_registry import (
    PromptBindingRecord,
    PromptLabelRecord,
    PromptTemplateRecord,
    PromptVersionRecord,
)
from src.services.asset_ownership import normalize_owner_scope_type, owner_scope_from_metadata
from src.services.asset_scopes import scope_lookup_candidates


class _FakePromptRepository:
    def __init__(self) -> None:
        self.templates: dict[str, PromptTemplateRecord] = {}
        self.versions: dict[str, list[PromptVersionRecord]] = {}
        self.labels: dict[str, list[PromptLabelRecord]] = {}
        self.bindings: list[PromptBindingRecord] = []
        self._template_counter = 0
        self._version_counter = 0
        self._label_counter = 0
        self._binding_counter = 0

    async def list_templates(self, *, search=None, limit=50, offset=0):  # noqa: ANN001, ANN201
        items = list(self.templates.values())
        if search:
            q = str(search).lower()
            items = [item for item in items if q in item.template_key.lower() or q in item.name.lower()]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def get_template(self, template_key: str):  # noqa: ANN201
        return self.templates.get(template_key)

    async def create_template(self, *, template_key, name, description, owner_scope, metadata):  # noqa: ANN001, ANN201
        self._template_counter += 1
        record = self._template_record(
            prompt_template_id=f"pt-{self._template_counter}",
            template_key=template_key,
            name=name,
            description=description,
            owner_scope=owner_scope,
            metadata=metadata,
        )
        self.templates[template_key] = record
        return record

    async def update_template(self, template_key: str, *, name, description, owner_scope, metadata):  # noqa: ANN001, ANN201
        existing = self.templates.get(template_key)
        if existing is None:
            return None
        updated = self._template_record(
            prompt_template_id=existing.prompt_template_id,
            template_key=existing.template_key,
            name=name,
            description=description,
            owner_scope=owner_scope,
            metadata=metadata,
            version_count=existing.version_count,
            label_count=existing.label_count,
            binding_count=existing.binding_count,
        )
        self.templates[template_key] = updated
        return updated

    async def delete_template(self, template_key: str) -> bool:
        if template_key not in self.templates:
            return False
        self.templates.pop(template_key, None)
        self.versions.pop(template_key, None)
        self.labels.pop(template_key, None)
        self.bindings = [item for item in self.bindings if item.template_key != template_key]
        return True

    async def list_versions(self, template_key: str):  # noqa: ANN201
        return list(sorted(self.versions.get(template_key, []), key=lambda item: item.version, reverse=True))

    async def create_version(  # noqa: ANN001, ANN201
        self,
        template_key: str,
        *,
        template_body,
        variables_schema,
        model_hints,
        route_preferences,
        status="draft",
    ):
        template = self.templates.get(template_key)
        if template is None:
            return None
        self._version_counter += 1
        version = len(self.versions.get(template_key, [])) + 1
        record = PromptVersionRecord(
            prompt_version_id=f"pv-{self._version_counter}",
            prompt_template_id=template.prompt_template_id,
            template_key=template_key,
            version=version,
            status=status,
            template_body=template_body,
            variables_schema=variables_schema,
            model_hints=model_hints,
            route_preferences=route_preferences,
        )
        self.versions.setdefault(template_key, []).append(record)
        return record

    async def publish_version(self, template_key: str, *, version: int, published_by=None):  # noqa: ANN001, ANN201
        items = self.versions.get(template_key, [])
        if not items:
            return None
        updated_items: list[PromptVersionRecord] = []
        published: PromptVersionRecord | None = None
        for item in items:
            if item.version == version:
                updated = replace(item, status="published", published_by=published_by)
                published = updated
            elif item.status == "published":
                updated = replace(item, status="archived")
            else:
                updated = item
            updated_items.append(updated)
        self.versions[template_key] = updated_items
        return published

    async def list_labels(self, template_key: str):  # noqa: ANN201
        return list(self.labels.get(template_key, []))

    async def assign_label(self, template_key: str, *, label: str, version: int):  # noqa: ANN201
        template = self.templates.get(template_key)
        version_item = next((item for item in self.versions.get(template_key, []) if item.version == version), None)
        if template is None or version_item is None:
            return None
        current = [item for item in self.labels.get(template_key, []) if item.label != label]
        self._label_counter += 1
        record = PromptLabelRecord(
            prompt_label_id=f"pl-{self._label_counter}",
            prompt_template_id=template.prompt_template_id,
            template_key=template_key,
            label=label,
            prompt_version_id=version_item.prompt_version_id,
            version=version,
        )
        current.append(record)
        self.labels[template_key] = current
        return record

    async def delete_label(self, template_key: str, label: str) -> bool:
        items = self.labels.get(template_key, [])
        updated = [item for item in items if item.label != label]
        if len(updated) == len(items):
            return False
        self.labels[template_key] = updated
        return True

    async def list_bindings(self, *, scope_type=None, scope_id=None, template_key=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if scope_type:
            aliases = set(scope_lookup_candidates(scope_type))
            items = [item for item in items if item.scope_type in aliases]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        if template_key:
            items = [item for item in items if item.template_key == template_key]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_binding(self, *, scope_type, scope_id, template_key, label, priority, enabled, metadata):  # noqa: ANN001, ANN201
        template = self.templates.get(template_key)
        if template is None:
            return None
        for index, item in enumerate(self.bindings):
            if item.scope_type == scope_type and item.scope_id == scope_id and item.template_key == template_key and item.label == label:
                updated = replace(item, priority=priority, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._binding_counter += 1
        record = PromptBindingRecord(
            prompt_binding_id=f"pb-{self._binding_counter}",
            scope_type=scope_type,
            scope_id=scope_id,
            prompt_template_id=template.prompt_template_id,
            template_key=template_key,
            label=label,
            priority=priority,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        updated = [item for item in self.bindings if item.prompt_binding_id != binding_id]
        if len(updated) == len(self.bindings):
            return False
        self.bindings = updated
        return True

    def _template_record(
        self,
        *,
        prompt_template_id: str,
        template_key: str,
        name: str,
        description: str | None,
        owner_scope: str | None,
        metadata: dict | None,
        version_count: int = 0,
        label_count: int = 0,
        binding_count: int = 0,
    ) -> PromptTemplateRecord:
        owner = owner_scope_from_metadata(metadata)
        owner_scope_type = owner.scope_type
        if owner_scope and owner_scope_type == "global" and owner.scope_id is None:
            owner_scope_type = normalize_owner_scope_type(owner_scope)
        return PromptTemplateRecord(
            prompt_template_id=prompt_template_id,
            template_key=template_key,
            name=name,
            description=description,
            owner_scope=owner_scope,
            metadata=metadata,
            owner_scope_type=owner_scope_type,
            owner_scope_id=owner.scope_id,
            version_count=version_count,
            label_count=label_count,
            binding_count=binding_count,
        )


class _FakePromptService:
    def __init__(self) -> None:
        self.invalidate_template_calls: list[str] = []
        self.invalidate_scope_calls: list[tuple[str, str]] = []
        self.invalidate_all_calls = 0

    async def invalidate_template(self, template_key: str) -> None:
        self.invalidate_template_calls.append(template_key)

    async def invalidate_scope(self, *, scope_type: str, scope_id: str) -> None:
        self.invalidate_scope_calls.append((scope_type, scope_id))

    async def invalidate_all(self) -> None:
        self.invalidate_all_calls += 1

    async def dry_run_render(self, *, template_key: str, label: str | None, version: int | None, variables: dict):  # noqa: ANN201
        return {
            "template_key": template_key,
            "label": label,
            "version": version or 1,
            "messages": [{"role": "system", "content": f"hello {variables.get('name', 'world')}"}],
        }

    async def resolve_binding_preview(self, *, api_key: str | None = None, user_id: str | None = None, team_id: str | None = None, organization_id: str | None = None, route_group_key: str | None = None):  # noqa: ANN201
        del user_id
        return {
            "winner": {"scope_type": "group", "scope_id": route_group_key, "template_key": "support.prompt", "label": "production"},
            "candidates": [
                {"scope_type": "group", "scope_id": route_group_key, "template_key": "support.prompt", "label": "production"},
                {"scope_type": "org", "scope_id": organization_id, "template_key": "org.prompt", "label": "staging"},
                {"scope_type": "team", "scope_id": team_id, "template_key": "team.prompt", "label": "staging"},
                {"scope_type": "key", "scope_id": api_key, "template_key": "key.prompt", "label": "canary"},
            ],
        }


@pytest.mark.asyncio
async def test_prompt_registry_admin_lifecycle(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prompt_registry_repository = _FakePromptRepository()
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    create = await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "support.prompt", "name": "Support Prompt", "description": "desc"},
    )
    assert create.status_code == 200
    assert create.json()["template_key"] == "support.prompt"

    version = await client.post(
        "/ui/api/prompt-registry/templates/support.prompt/versions",
        headers=headers,
        json={
            "template_body": {"messages": [{"role": "system", "content": "Hi {name}"}]},
            "variables_schema": {"type": "object"},
            "model_hints": {},
            "route_preferences": {"tags": ["vip"]},
        },
    )
    assert version.status_code == 200
    assert version.json()["version"] == 1
    assert version.json()["route_preferences"] == {"tags": ["vip"]}

    publish = await client.post("/ui/api/prompt-registry/templates/support.prompt/versions/1/publish", headers=headers)
    assert publish.status_code == 200
    assert publish.json()["status"] == "published"

    label = await client.post(
        "/ui/api/prompt-registry/templates/support.prompt/labels",
        headers=headers,
        json={"label": "production", "version": 1},
    )
    assert label.status_code == 200
    assert label.json()["label"] == "production"

    binding = await client.post(
        "/ui/api/prompt-registry/bindings",
        headers=headers,
        json={"scope_type": "group", "scope_id": "support-route", "template_key": "support.prompt", "label": "production", "priority": 100, "enabled": True},
    )
    assert binding.status_code == 200
    assert binding.json()["scope_type"] == "group"

    detail = await client.get("/ui/api/prompt-registry/templates/support.prompt", headers=headers)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["template"]["template_key"] == "support.prompt"
    assert len(payload["versions"]) == 1
    assert len(payload["labels"]) == 1
    assert len(payload["bindings"]) == 1

    render = await client.post(
        "/ui/api/prompt-registry/render",
        headers=headers,
        json={"template_key": "support.prompt", "label": "production", "variables": {"name": "Alice"}},
    )
    assert render.status_code == 200
    assert render.json()["messages"][0]["content"] == "hello Alice"

    preview = await client.post(
        "/ui/api/prompt-registry/preview-resolution",
        headers=headers,
        json={"route_group_key": "support-route", "organization_id": "org-1", "team_id": "team-1", "api_key": "key-1"},
    )
    assert preview.status_code == 200
    assert preview.json()["winner"]["scope_type"] == "group"
    candidate_scopes = [item["scope_type"] for item in preview.json()["candidates"]]
    assert candidate_scopes == ["group", "organization", "team", "api_key"]


@pytest.mark.asyncio
async def test_prompt_registry_rejects_secret_like_template_body(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prompt_registry_repository = _FakePromptRepository()
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "secret.prompt", "name": "Secret Prompt"},
    )

    response = await client.post(
        "/ui/api/prompt-registry/templates/secret.prompt/versions",
        headers=headers,
        json={"template_body": {"text": "use this key sk-abc1234567890123456789012345"}, "variables_schema": {}, "model_hints": {}},
    )
    assert response.status_code == 400
    assert "secret-like content" in response.text


@pytest.mark.asyncio
async def test_prompt_registry_label_assignment_requires_approval_when_enabled(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prompt_registry_repository = _FakePromptRepository()
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "approval.prompt", "name": "Approval Prompt"},
    )
    await client.post(
        "/ui/api/prompt-registry/templates/approval.prompt/versions",
        headers=headers,
        json={"template_body": {"text": "hello"}, "variables_schema": {}, "model_hints": {}},
    )

    response = await client.post(
        "/ui/api/prompt-registry/templates/approval.prompt/labels",
        headers=headers,
        json={"label": "production", "version": 1, "require_approval": True},
    )
    assert response.status_code == 400
    assert "approved_by is required" in response.text


@pytest.mark.asyncio
async def test_prompt_registry_rejects_invalid_route_preferences_shape(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prompt_registry_repository = _FakePromptRepository()
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "routing.prompt", "name": "Routing Prompt"},
    )

    response = await client.post(
        "/ui/api/prompt-registry/templates/routing.prompt/versions",
        headers=headers,
        json={
            "template_body": {"text": "hello"},
            "variables_schema": {},
            "model_hints": {},
            "route_preferences": {"tags": ["", "vip"]},
        },
    )
    assert response.status_code == 400
    assert "route_preferences.tags must be an array of non-empty strings" in response.text


@pytest.mark.asyncio
async def test_prompt_registry_binding_accepts_canonical_scope_names(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "canonical.prompt", "name": "Canonical Prompt"},
    )

    response = await client.post(
        "/ui/api/prompt-registry/bindings",
        headers=headers,
        json={
            "scope_type": "api_key",
            "scope_id": "key-1",
            "template_key": "canonical.prompt",
            "label": "production",
            "priority": 100,
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["scope_type"] == "api_key"
    assert repo.bindings[0].scope_type == "api_key"


@pytest.mark.asyncio
async def test_prompt_registry_list_bindings_supports_canonical_filter_for_legacy_scope_rows(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    template = await repo.create_template(
        template_key="legacy.prompt",
        name="Legacy Prompt",
        description=None,
        owner_scope=None,
        metadata=None,
    )
    repo.bindings.append(
        PromptBindingRecord(
            prompt_binding_id="pb-legacy",
            scope_type="key",
            scope_id="legacy-key",
            prompt_template_id=template.prompt_template_id,
            template_key="legacy.prompt",
            label="production",
            priority=100,
            enabled=True,
            metadata=None,
        )
    )
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.get(
        "/ui/api/prompt-registry/bindings",
        headers=headers,
        params={"scope_type": "api_key", "scope_id": "legacy-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["scope_type"] == "api_key"


@pytest.mark.asyncio
async def test_prompt_registry_template_detail_normalizes_legacy_binding_scope_names(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    template = await repo.create_template(
        template_key="detail.prompt",
        name="Detail Prompt",
        description=None,
        owner_scope=None,
        metadata=None,
    )
    repo.bindings.append(
        PromptBindingRecord(
            prompt_binding_id="pb-org",
            scope_type="org",
            scope_id="org-1",
            prompt_template_id=template.prompt_template_id,
            template_key="detail.prompt",
            label="production",
            priority=100,
            enabled=True,
            metadata=None,
        )
    )
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.get("/ui/api/prompt-registry/templates/detail.prompt", headers=headers)

    assert response.status_code == 200
    assert response.json()["bindings"][0]["scope_type"] == "organization"


@pytest.mark.asyncio
async def test_prompt_registry_template_defaults_to_global_ownership(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={"template_key": "owner-default.prompt", "name": "Owner Default"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_scope"] == "global"
    assert payload["owner_scope_type"] == "global"
    assert payload["owner_scope_id"] is None
    assert payload["metadata"] is None


@pytest.mark.asyncio
async def test_prompt_registry_template_accepts_organization_ownership(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={
            "template_key": "owner-org.prompt",
            "name": "Owner Org",
            "owner_scope_type": "organization",
            "owner_scope_id": "org-123",
            "metadata": {"category": "support"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_scope"] == "organization"
    assert payload["owner_scope_type"] == "organization"
    assert payload["owner_scope_id"] == "org-123"
    assert payload["metadata"] == {"category": "support"}
    assert repo.templates["owner-org.prompt"].metadata == {
        "category": "support",
        "_asset_governance": {"owner_scope_type": "organization", "owner_scope_id": "org-123"},
    }


@pytest.mark.asyncio
async def test_prompt_registry_template_update_can_reset_ownership_to_global(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    await repo.create_template(
        template_key="owner-reset.prompt",
        name="Owner Reset",
        description=None,
        owner_scope="organization",
        metadata={
            "category": "support",
            "_asset_governance": {"owner_scope_type": "organization", "owner_scope_id": "org-123"},
        },
    )
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.put(
        "/ui/api/prompt-registry/templates/owner-reset.prompt",
        headers=headers,
        json={"owner_scope_type": "global"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_scope"] == "global"
    assert payload["owner_scope_type"] == "global"
    assert payload["owner_scope_id"] is None
    assert payload["metadata"] == {"category": "support"}
    assert repo.templates["owner-reset.prompt"].metadata == {"category": "support"}


@pytest.mark.asyncio
async def test_prompt_registry_template_rejects_org_ownership_without_scope_id(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakePromptRepository()
    test_app.state.prompt_registry_repository = repo
    test_app.state.prompt_registry_service = _FakePromptService()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/prompt-registry/templates",
        headers=headers,
        json={
            "template_key": "owner-invalid.prompt",
            "name": "Owner Invalid",
            "owner_scope_type": "organization",
        },
    )

    assert response.status_code == 400
    assert "owner_scope_id is required" in response.text
