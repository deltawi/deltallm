from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from prisma import Prisma

from src.api.admin.endpoints import audit as audit_endpoints
from src.api.admin.endpoints.common import AuthScope

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _reset_event_id_type(monkeypatch):
    monkeypatch.setattr(audit_endpoints, "_EVENT_ID_IS_UUID", None)


@pytest.fixture
async def audit_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL for audit explorer tests")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    tag = uuid4().hex[:10]
    try:
        yield db, tag
    finally:
        await db.execute_raw("DELETE FROM deltallm_auditevent WHERE organization_id = $1", f"org-{tag}")
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", f"hash-{tag}")
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", f"team-{tag}")
        await db.execute_raw("DELETE FROM deltallm_organizationmembership WHERE organization_id = $1", f"org-{tag}")
        await db.execute_raw("DELETE FROM deltallm_platformaccount WHERE account_id = ANY($1::text[])", [f"acc-{tag}", f"outsider-{tag}"])
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id = $1", f"org-{tag}")
        await db.disconnect()


async def _seed(db: Prisma, tag: str) -> datetime:
    org = f"org-{tag}"
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, organization_name, updated_at) VALUES ($1, $1, 'Acme Corp', now())",
        org,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id, email, role, updated_at) VALUES ($1, $2, 'org_user', now())",
        f"acc-{tag}",
        f"carol-{tag}@acme.test",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id, email, role, updated_at) VALUES ($1, $2, 'platform_admin', now())",
        f"outsider-{tag}",
        f"admin-{tag}@platform.test",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_organizationmembership (membership_id, account_id, organization_id, role, updated_at) VALUES ($1, $2, $3, 'org_member', now())",
        f"m-{tag}",
        f"acc-{tag}",
        org,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, team_alias, organization_id, updated_at) VALUES ($1, 'Search', $2, now())",
        f"team-{tag}",
        org,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id, token, key_name, team_id, updated_at) VALUES ($1, $1, 'search-indexer', $2, now())",
        f"hash-{tag}",
        f"team-{tag}",
    )
    rows = [
        # action, actor_type, actor_id, api_key, resource_type, resource_id, status, minutes ago, request_id, ip
        ("AUTH_INTERNAL_LOGIN", "platform_account", f"acc-{tag}", None, "session", None, "error", 2, f"r1-{tag}", "10.0.4.17"),
        ("CHAT_COMPLETION_REQUEST", "api_key", f"hash-{tag}", f"hash-{tag}", "model", "glm-4.6", "error", 3, f"r2-{tag}", "10.0.4.18"),
        ("ADMIN_KEY_UPDATE", "platform_account", f"outsider-{tag}", None, "api_key", f"hash-{tag}", "success", 12, f"r3-{tag}", "10.0.1.4"),
        ("ADMIN_KEY_CREATE", "platform_account", f"outsider-{tag}", None, "api_key", f"hash-{tag}", "success", 40, f"r4-{tag}", "10.0.1.4"),
        ("MCP_TOOL_CALL", "api_key", f"hash-{tag}", f"hash-{tag}", "mcp_tool", "github.search", "success", 18, f"r2-{tag}", "10.0.4.18"),
        ("AUTH_INTERNAL_LOGIN", "platform_account", f"acc-{tag}", None, "session", None, "success", 60 * 30, f"r5-{tag}", "10.0.4.17"),
    ]
    for action, actor_type, actor_id, api_key, resource_type, resource_id, status, minutes, request_id, ip in rows:
        await db.execute_raw(
            """
            INSERT INTO deltallm_auditevent
              (occurred_at, organization_id, actor_type, actor_id, api_key, action, resource_type, resource_id,
               request_id, correlation_id, ip, status, metadata)
            VALUES ($1::timestamp, $2, $3, $4, $5, $6, $7, $8, $9, $9, $10, $11, '{}'::jsonb)
            """,
            now - timedelta(minutes=minutes),
            org,
            actor_type,
            actor_id,
            api_key,
            action,
            resource_type,
            resource_id,
            request_id,
            ip,
            status,
        )
    return now


class _State:
    def __init__(self, db: Prisma) -> None:
        self.prisma_manager = type("Manager", (), {"client": db})()


class _App:
    def __init__(self, db: Prisma) -> None:
        self.state = _State(db)


class _Request:
    def __init__(self, db: Prisma) -> None:
        self.app = _App(db)


def _scope(tag: str) -> AuthScope:
    return AuthScope(is_platform_admin=False, org_ids=[f"org-{tag}"], account_id="viewer")


async def _list(db: Prisma, tag: str, monkeypatch, **params):
    monkeypatch.setattr(audit_endpoints, "get_auth_scope", lambda *args, **kwargs: _scope(tag))
    defaults = {
        "action": None, "category": None, "status_value": None, "actor_id": None, "actor": None, "actor_type": None,
        "organization_id": None, "request_id": None, "correlation_id": None, "resource_type": None,
        "resource_id": None, "ip": None, "search": None, "start_date": None, "end_date": None, "since": None,
        "until": None, "cursor": None, "count": False, "enrich": True, "limit": 50, "offset": 0,
        "authorization": None, "x_master_key": None,
    }
    defaults.update(params)
    return await audit_endpoints.list_audit_events(_Request(db), **defaults)


async def test_explorer_filters_names_and_cursor_paging(audit_db, monkeypatch):
    db, tag = audit_db
    now = await _seed(db, tag)
    since = (now - timedelta(hours=24)).replace(tzinfo=UTC)

    result = await _list(db, tag, monkeypatch, since=since)
    actions = [event["action"] for event in result["events"]]
    assert actions == ["AUTH_INTERNAL_LOGIN", "CHAT_COMPLETION_REQUEST", "ADMIN_KEY_UPDATE", "MCP_TOOL_CALL", "ADMIN_KEY_CREATE"]
    assert "total" not in result["pagination"]
    login, chat, key_update = result["events"][:3]
    assert login["labels"]["actor"] == f"carol-{tag}@acme.test"
    assert login["labels"]["organization"] == "Acme Corp"
    assert chat["labels"]["actor"] == "search-indexer"
    assert chat["labels"]["team"] == "Search"
    assert key_update["labels"]["resource"] == "search-indexer"
    # An org admin does not see the email of a platform account outside their organizations.
    assert key_update["labels"]["actor"] is None

    by_category = await _list(db, tag, monkeypatch, since=since, category="api")
    assert [event["action"] for event in by_category["events"]] == ["CHAT_COMPLETION_REQUEST"]
    by_category = await _list(db, tag, monkeypatch, since=since, category="admin")
    assert {event["action"] for event in by_category["events"]} == {"ADMIN_KEY_UPDATE", "ADMIN_KEY_CREATE"}

    by_actor = await _list(db, tag, monkeypatch, since=since, actor=f"Carol-{tag}@acme.test")
    assert [event["action"] for event in by_actor["events"]] == ["AUTH_INTERNAL_LOGIN"]
    by_key_name = await _list(db, tag, monkeypatch, since=since, actor="search-indexer")
    assert {event["action"] for event in by_key_name["events"]} == {"CHAT_COMPLETION_REQUEST", "MCP_TOOL_CALL"}
    # The UI filters an API key actor by its hash, which also matches rows whose actor_id is the owner's user ID.
    await db.execute_raw(
        "UPDATE deltallm_auditevent SET actor_id = 'user-1' WHERE organization_id = $1 AND action = 'MCP_TOOL_CALL'",
        f"org-{tag}",
    )
    by_key_hash = await _list(db, tag, monkeypatch, since=since, actor=f"hash-{tag}")
    assert {event["action"] for event in by_key_hash["events"]} == {"CHAT_COMPLETION_REQUEST", "MCP_TOOL_CALL"}

    by_search = await _list(db, tag, monkeypatch, since=since, search=f"r2-{tag}")
    assert len(by_search["events"]) == 2
    by_ip = await _list(db, tag, monkeypatch, since=since, ip="10.0.1.4", resource_type="api_key")
    assert len(by_ip["events"]) == 2

    first = await _list(db, tag, monkeypatch, since=since, limit=2)
    assert first["pagination"]["has_more"] is True
    second = await _list(db, tag, monkeypatch, since=since, limit=2, cursor=first["pagination"]["next_cursor"])
    third = await _list(db, tag, monkeypatch, since=since, limit=2, cursor=second["pagination"]["next_cursor"])
    paged = [event["event_id"] for page in (first, second, third) for event in page["events"]]
    assert paged == [event["event_id"] for event in result["events"]]
    assert third["pagination"]["has_more"] is False

    counted = await _list(db, tag, monkeypatch, since=since, count=True, enrich=False)
    assert counted["pagination"]["total"] == 5
    assert "labels" not in counted["events"][0]


async def test_event_detail_context_and_related(audit_db, monkeypatch):
    db, tag = audit_db
    now = await _seed(db, tag)
    since = (now - timedelta(days=2)).replace(tzinfo=UTC)
    events = (await _list(db, tag, monkeypatch, since=since))["events"]
    chat = next(event for event in events if event["action"] == "CHAT_COMPLETION_REQUEST")
    login = events[0]
    key_update = next(event for event in events if event["action"] == "ADMIN_KEY_UPDATE")

    detail = await audit_endpoints.get_audit_event(
        _Request(db), chat["event_id"], include_payloads=False, context=True, authorization=None, x_master_key=None
    )
    assert detail["payload_summary"] == {"count": 0, "redacted": False, "kinds": []}
    assert "payloads" not in detail
    assert detail["context"]["key"]["name"] == "search-indexer"
    assert detail["context"]["key"]["team"] == "Search"
    assert detail["context"]["key"]["state"] == "active"
    assert detail["context"]["resource"]["state"] == "missing"

    key_detail = await audit_endpoints.get_audit_event(
        _Request(db), key_update["event_id"], include_payloads=True, context=True, authorization=None, x_master_key=None
    )
    assert key_detail["payloads"] == []
    assert key_detail["context"]["resource"]["label"] == "search-indexer"
    assert key_detail["context"]["resource"]["team"] == "Search"
    assert key_detail["context"]["actor"]["email"] is None
    assert key_detail["context"]["actor"]["platform_role"] == "platform_admin"

    login_detail = await audit_endpoints.get_audit_event(
        _Request(db), login["event_id"], include_payloads=False, context=True, authorization=None, x_master_key=None
    )
    assert login_detail["context"]["actor"]["email"] == f"carol-{tag}@acme.test"
    assert login_detail["context"]["actor"]["organization_role"] == "org_member"

    related = await audit_endpoints.get_related_audit_events(_Request(db), chat["event_id"], authorization=None, x_master_key=None)
    assert [event["action"] for event in related["request"]] == ["MCP_TOOL_CALL", "CHAT_COMPLETION_REQUEST"]
    key_related = await audit_endpoints.get_related_audit_events(
        _Request(db), key_update["event_id"], authorization=None, x_master_key=None
    )
    assert [event["action"] for event in key_related["resource"]] == ["ADMIN_KEY_UPDATE", "ADMIN_KEY_CREATE"]
    login_related = await audit_endpoints.get_related_audit_events(_Request(db), login["event_id"], authorization=None, x_master_key=None)
    assert [event["status"] for event in login_related["actor_signins"]] == ["error", "success"]

    with pytest.raises(Exception) as missing:
        await audit_endpoints.get_audit_event(
            _Request(db), "not-a-uuid", include_payloads=False, context=False, authorization=None, x_master_key=None
        )
    assert getattr(missing.value, "status_code", None) == 404
