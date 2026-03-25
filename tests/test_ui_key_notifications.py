from __future__ import annotations

from typing import Any

import pytest


class _FakeNotificationService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def notify_lifecycle(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("notification failed")


class _FakeKeyNotificationDB:
    def __init__(self) -> None:
        self.teams = {
            "team-1": {
                "team_id": "team-1",
                "team_alias": "Team One",
                "organization_id": "org-1",
            }
        }
        self.accounts = {"acct-owner"}
        self.keys: dict[str, dict[str, Any]] = {
            "key-1": {
                "token": "key-1",
                "key_name": "Existing Key",
                "team_id": "team-1",
                "organization_id": "org-1",
                "owner_account_id": "acct-owner",
                "owner_service_account_id": None,
            }
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        token = str(params[0]) if params else ""

        if normalized.startswith("select account_id from deltallm_platformaccount"):
            return [{"account_id": token}] if token in self.accounts else []

        if "from deltallm_teamtable" in normalized and "where team_id = $1" in normalized and "team_alias" in normalized:
            row = self.teams.get(token)
            return [dict(row)] if row is not None else []

        if "from deltallm_verificationtoken vt" in normalized and "left join deltallm_usertable" in normalized:
            row = self.keys.get(token)
            if row is None:
                return []
            return [
                {
                    "token": row["token"],
                    "user_id": row.get("user_id"),
                    "team_id": row.get("team_id"),
                    "organization_id": row.get("organization_id"),
                }
            ]

        if normalized.startswith("select token from deltallm_verificationtoken where token = $1"):
            row = self.keys.get(token)
            return [{"token": row["token"]}] if row else []

        if (
            "select vt.token, vt.key_name, vt.team_id, t.team_alias, t.organization_id," in normalized
            and "from deltallm_verificationtoken vt" in normalized
        ):
            row = self.keys.get(token)
            if row is None:
                return []
            team = self.teams.get(str(row.get("team_id") or ""), {})
            return [
                {
                    "token": row["token"],
                    "key_name": row.get("key_name"),
                    "team_id": row.get("team_id"),
                    "team_alias": team.get("team_alias"),
                    "organization_id": row.get("organization_id") or team.get("organization_id"),
                    "owner_account_id": row.get("owner_account_id"),
                    "owner_service_account_id": row.get("owner_service_account_id"),
                    "owner_service_account_name": None,
                }
            ]

        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("insert into deltallm_verificationtoken"):
            (
                token_hash,
                key_name,
                user_id,
                team_id,
                owner_account_id,
                owner_service_account_id,
                max_budget,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                expires,
            ) = params
            del max_budget, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, expires
            self.keys[str(token_hash)] = {
                "token": token_hash,
                "key_name": key_name,
                "user_id": user_id,
                "team_id": team_id,
                "organization_id": self.teams[str(team_id)]["organization_id"],
                "owner_account_id": owner_account_id,
                "owner_service_account_id": owner_service_account_id,
            }
            return 1
        if normalized.startswith("update deltallm_verificationtoken set token = $1, updated_at = now() where token = $2"):
            new_hash = str(params[0])
            old_hash = str(params[1])
            row = self.keys.pop(old_hash, None)
            if row is None:
                return 0
            row["token"] = new_hash
            self.keys[new_hash] = row
            return 1
        if normalized.startswith("delete from deltallm_verificationtoken where token = $1"):
            return 1 if self.keys.pop(str(params[0]), None) is not None else 0
        return 0


@pytest.mark.asyncio
async def test_create_key_triggers_notification_when_enabled(client, test_app) -> None:
    db = _FakeKeyNotificationDB()
    notifier = _FakeNotificationService()
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    test_app.state.key_notification_service = notifier
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/keys",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "key_name": "Created Key",
            "team_id": "team-1",
            "owner_account_id": "acct-owner",
        },
    )

    assert response.status_code == 200
    assert notifier.calls[0]["event_kind"] == "api_key_created"
    assert notifier.calls[0]["record"].key_name == "Created Key"


@pytest.mark.asyncio
async def test_regenerate_delete_and_revoke_survive_notification_failure(client, test_app) -> None:
    db = _FakeKeyNotificationDB()
    notifier = _FakeNotificationService(fail=True)
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    test_app.state.key_notification_service = notifier
    setattr(test_app.state.settings, "master_key", "mk-test")

    regenerate = await client.post("/ui/api/keys/key-1/regenerate", headers={"Authorization": "Bearer mk-test"})
    assert regenerate.status_code == 200
    new_hash = regenerate.json()["token"]
    assert notifier.calls[0]["event_kind"] == "api_key_regenerated"

    revoke = await client.post(f"/ui/api/keys/{new_hash}/revoke", headers={"Authorization": "Bearer mk-test"})
    assert revoke.status_code == 200
    assert revoke.json() == {"revoked": True}
    assert notifier.calls[1]["event_kind"] == "api_key_revoked"

    db.keys["key-2"] = {
        "token": "key-2",
        "key_name": "Delete Key",
        "team_id": "team-1",
        "organization_id": "org-1",
        "owner_account_id": "acct-owner",
        "owner_service_account_id": None,
    }
    delete = await client.delete("/ui/api/keys/key-2", headers={"Authorization": "Bearer mk-test"})
    assert delete.status_code == 200
    assert delete.json() == {"deleted": True}
    assert notifier.calls[2]["event_kind"] == "api_key_deleted"
