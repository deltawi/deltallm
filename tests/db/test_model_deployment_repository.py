from __future__ import annotations

import json

import pytest

from src.db.catalog.model_deployments import ModelDeploymentRecord, ModelDeploymentRepository
from src.db.routing.route_policy_lifecycle import RoutePolicyStateConflictError


class FakePrisma:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.runtime_revision = 0

    async def query_raw(self, query: str, *args):
        if "UPDATE deltallm_routeruntimestate" in query:
            self.runtime_revision += 1
            return [{"revision": self.runtime_revision}]
        if "FROM deltallm_modeldeployment" in query and "WHERE model_name = $1" in query:
            model_name = str(args[0])
            excluded = str(args[1]) if len(args) > 1 and args[1] is not None else None
            return [
                {"deployment_id": deployment_id}
                for deployment_id, row in self.rows.items()
                if row["model_name"] == model_name and deployment_id != excluded
            ][:1]
        if "SELECT model_name" in query and "WHERE deployment_id = $1" in query:
            row = self.rows.get(str(args[0]))
            return [{"model_name": row["model_name"]}] if row else []
        if (
            "SELECT deployment_id, model_name, model_id, named_credential_id" in query
            and "WHERE deployment_id = $1" not in query
        ):
            values = sorted(
                self.rows.values(),
                key=lambda item: (str(item["model_name"]), str(item["deployment_id"])),
            )
            return [dict(item) for item in values]
        if (
            "WHERE deployment_id = $1" in query
            and "SELECT deployment_id, model_name, model_id, named_credential_id" in query
        ):
            deployment_id = str(args[0])
            row = self.rows.get(deployment_id)
            return [dict(row)] if row else []
        if (
            "UPDATE deltallm_modeldeployment" in query
            and "credential_binding_state = 'revoked'" in query
        ):
            deployment_id = str(args[0])
            expected_credential_id = str(args[1])
            row = self.rows.get(deployment_id)
            if (
                row is None
                or row.get("named_credential_id") != expected_credential_id
                or row.get("credential_binding_state") != "active"
            ):
                return []
            row["named_credential_id"] = None
            row["credential_binding_state"] = "revoked"
            row["credential_revoked_at"] = "now"
            return [dict(row)]
        if "UPDATE deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            if deployment_id not in self.rows:
                return []
            incarnation = self.rows[deployment_id]["routing_state_incarnation"]
            self.rows[deployment_id] = {
                "deployment_id": deployment_id,
                "model_name": str(args[1]),
                "model_id": self.rows[deployment_id].get("model_id"),
                "named_credential_id": str(args[2]) if args[2] is not None else None,
                "deltallm_params": json.loads(str(args[3])),
                "model_info": json.loads(str(args[4])) if args[4] is not None else None,
                "routing_state_incarnation": incarnation,
                "credential_binding_mode": (
                    args[5]
                    if len(args) > 5 and args[5] is not None
                    else self.rows[deployment_id].get("credential_binding_mode")
                ),
                "credential_binding_state": (
                    args[6]
                    if len(args) > 6 and args[6] is not None
                    else self.rows[deployment_id].get("credential_binding_state")
                ),
                "credential_bound_by_account_id": (
                    args[7]
                    if len(args) > 7 and args[7] is not None
                    else self.rows[deployment_id].get("credential_bound_by_account_id")
                ),
            }
            if len(args) > 8 and args[8] is True:
                self.rows[deployment_id].update(
                    credential_binding_mode=None,
                    credential_binding_state=None,
                    credential_bound_by_account_id=None,
                    credential_revoked_at=None,
                )
            return [dict(self.rows[deployment_id])]
        if "DELETE FROM deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            existed = deployment_id in self.rows
            self.rows.pop(deployment_id, None)
            return [{"deployment_id": deployment_id}] if existed else []
        if "SELECT COUNT(*)::int AS count FROM deltallm_modeldeployment" in query:
            return [{"count": len(self.rows)}]
        return []

    async def execute_raw(self, query: str, *args):
        if "INSERT INTO deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            if "ON CONFLICT (deployment_id) DO NOTHING" in query and deployment_id in self.rows:
                return
            self.rows[deployment_id] = {
                "deployment_id": deployment_id,
                "model_name": str(args[1]),
                "model_id": str(args[2]) if args[2] is not None else None,
                "named_credential_id": str(args[3]) if args[3] is not None else None,
                "deltallm_params": json.loads(str(args[4])),
                "model_info": json.loads(str(args[5])) if args[5] is not None else None,
                "routing_state_incarnation": f"created-{deployment_id}",
                "credential_binding_mode": args[6] if len(args) > 6 else None,
                "credential_binding_state": args[7] if len(args) > 7 else None,
                "credential_bound_by_account_id": args[8] if len(args) > 8 else None,
                "credential_revoked_at": None,
            }


class _RouteAwareTransaction:
    def __init__(self) -> None:
        self.deleted = False

    async def query_raw(self, query: str, *args):  # noqa: ANN201
        del args
        if "FOR UPDATE OF g" in query:
            return [{"route_group_id": "group-1", "group_key": "support-route"}]
        if "DELETE FROM deltallm_modeldeployment" in query:
            self.deleted = True
            return [{"deployment_id": "dep-1"}]
        if "g.mode AS group_mode" in query:
            return [{"group_mode": "chat", "deployment_id": None, "model_info": None}]
        if "SELECT policy_json, semantics_version" in query:
            return [
                {
                    "policy_json": {"members": [{"deployment_id": "dep-1"}]},
                    "semantics_version": 2,
                }
            ]
        if "COALESCE(d.model_info->>'mode'" in query:
            return [
                {
                    "group_key": "support-route",
                    "group_mode": "chat",
                    "deployment_id": None,
                    "enabled": None,
                    "deployment_mode": None,
                }
            ]
        return []


class _RouteAwareTransactionContext:
    def __init__(self, owner: "_RouteAwarePrisma") -> None:
        self.owner = owner

    async def __aenter__(self) -> _RouteAwareTransaction:
        return self.owner.transaction

    async def __aexit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        del exc, traceback
        if exc_type is not None:
            self.owner.transaction.deleted = False
            self.owner.rolled_back += 1
        return False


class _RouteAwarePrisma:
    def __init__(self) -> None:
        self.transaction = _RouteAwareTransaction()
        self.rolled_back = 0

    def tx(self) -> _RouteAwareTransactionContext:
        return _RouteAwareTransactionContext(self)


@pytest.mark.asyncio
async def test_model_deployment_repository_crud_roundtrip():
    repo = ModelDeploymentRepository(FakePrisma())

    record = ModelDeploymentRecord(
        deployment_id="dep-1",
        model_name="openai/gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini"},
        named_credential_id="cred-1",
        model_info={"priority": 1},
    )
    await repo.create(record)

    loaded = await repo.get_by_deployment_id("dep-1")
    assert loaded is not None
    assert loaded.model_name == "openai/gpt-4o-mini"
    assert loaded.named_credential_id == "cred-1"
    assert loaded.routing_state_incarnation == "created-dep-1"

    updated = await repo.update(
        "dep-1",
        model_name="openai/gpt-4.1-mini",
        named_credential_id=None,
        deltallm_params={"model": "openai/gpt-4.1-mini"},
        model_info={"priority": 2},
    )
    assert updated is not None
    assert updated.model_name == "openai/gpt-4.1-mini"
    assert updated.named_credential_id is None
    assert updated.model_info == {"priority": 2}
    assert updated.routing_state_incarnation == loaded.routing_state_incarnation

    rows = await repo.list_all()
    assert len(rows) == 1
    assert rows[0].deployment_id == "dep-1"

    deleted = await repo.delete("dep-1")
    assert deleted is True
    assert await repo.get_by_deployment_id("dep-1") is None


@pytest.mark.asyncio
async def test_model_deployment_repository_explicitly_clears_named_binding_metadata():
    prisma = FakePrisma()
    repo = ModelDeploymentRepository(prisma, use_transactions=False)
    await repo.create(
        ModelDeploymentRecord(
            deployment_id="dep-inline",
            model_name="platform-model",
            named_credential_id="cred-1",
            credential_binding_mode="platform_override",
            credential_binding_state="active",
            credential_bound_by_account_id="admin-1",
            deltallm_params={"model": "openai/gpt-4o-mini"},
            model_info={"mode": "chat"},
        )
    )

    updated = await repo.update(
        "dep-inline",
        model_name="platform-model",
        named_credential_id=None,
        deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "inline"},
        model_info={"mode": "chat"},
        clear_credential_binding=True,
    )

    assert updated is not None
    assert updated.named_credential_id is None
    assert updated.credential_binding_mode is None
    assert updated.credential_binding_state is None
    assert updated.credential_bound_by_account_id is None
    assert updated.credential_revoked_at is None


@pytest.mark.asyncio
async def test_model_deployment_repository_bulk_insert_if_empty_only_once():
    repo = ModelDeploymentRepository(FakePrisma())
    records = [
        ModelDeploymentRecord(
            deployment_id="dep-a",
            model_name="m-a",
            deltallm_params={"model": "openai/a"},
            model_info={},
        )
    ]

    assert await repo.bulk_insert_if_empty(records) is True
    assert (
        await repo.bulk_insert_if_empty(
            [
                ModelDeploymentRecord(
                    deployment_id="dep-b",
                    model_name="m-b",
                    deltallm_params={"model": "openai/b"},
                    model_info={},
                )
            ]
        )
        is False
    )
    rows = await repo.list_all()
    assert [item.deployment_id for item in rows] == ["dep-a"]


@pytest.mark.asyncio
async def test_model_deployment_repository_preserves_duplicate_model_name() -> None:
    repo = ModelDeploymentRepository(FakePrisma())
    await repo.create(
        ModelDeploymentRecord(
            deployment_id="dep-a",
            model_name="shared-model",
            deltallm_params={"model": "openai/a"},
        )
    )

    await repo.create(
        ModelDeploymentRecord(
            deployment_id="dep-b",
            model_name="shared-model",
            deltallm_params={"model": "openai/b"},
        )
    )

    rows = await repo.list_all()
    assert [row.deployment_id for row in rows] == ["dep-a", "dep-b"]
    assert {row.model_name for row in rows} == {"shared-model"}


@pytest.mark.asyncio
async def test_transaction_scoped_model_mutations_bump_full_routing_revision():
    prisma = FakePrisma()
    repo = ModelDeploymentRepository(prisma, use_transactions=False)
    record = ModelDeploymentRecord(
        deployment_id="dep-1",
        model_name="m-a",
        deltallm_params={"model": "openai/a"},
        model_info={},
    )

    await repo.create(record)
    await repo.update(
        "dep-1",
        model_name="m-b",
        named_credential_id=None,
        deltallm_params={"model": "openai/b"},
        model_info={},
    )
    await repo.delete("dep-1")

    assert prisma.runtime_revision == 3


@pytest.mark.asyncio
async def test_model_deployment_credential_owner_can_revoke_exact_live_binding():
    prisma = FakePrisma()
    repo = ModelDeploymentRepository(prisma, use_transactions=False)
    await repo.create(
        ModelDeploymentRecord(
            deployment_id="dep-revoke",
            model_name="shared-model",
            named_credential_id="cred-owner",
            credential_binding_mode="owner_delegated",
            credential_binding_state="active",
            credential_bound_by_account_id="credential-owner",
            deltallm_params={"model": "openai/gpt-4o-mini"},
            model_info={"mode": "chat"},
        )
    )

    revoked = await repo.revoke_credential_binding(
        "dep-revoke",
        expected_credential_id="cred-owner",
    )

    assert revoked is not None
    assert revoked.named_credential_id is None
    assert revoked.credential_binding_state == "revoked"
    assert revoked.credential_revoked_at is not None
    assert prisma.runtime_revision == 2
    assert (
        await repo.revoke_credential_binding(
            "dep-revoke",
            expected_credential_id="cred-owner",
        )
        is None
    )


@pytest.mark.asyncio
async def test_model_deployment_delete_rolls_back_invalid_policy_cascade():
    prisma = _RouteAwarePrisma()

    with pytest.raises(RoutePolicyStateConflictError, match="would invalidate route group"):
        await ModelDeploymentRepository(prisma).delete("dep-1")

    assert prisma.rolled_back == 1
    assert prisma.transaction.deleted is False
