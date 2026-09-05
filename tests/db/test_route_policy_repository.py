from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError


class _RoutePolicyDB:
    def __init__(
        self,
        *,
        current_policy: dict | None = None,
        draft_policy: dict | None = None,
        rollback_policy: dict | None = None,
        fail_insert: bool = False,
        group_mode: str = "chat",
    ) -> None:
        self.current_policy = current_policy
        self.draft_policy = draft_policy
        self.rollback_policy = rollback_policy
        self.fail_insert = fail_insert
        self.group_mode = group_mode
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params: object) -> list[dict]:
        self.calls.append((sql, params))
        if "FROM deltallm_routegroup" in sql and "FOR UPDATE" in sql:
            return [{"route_group_id": "group-1"}]
        if "COALESCE(d.model_info->>'mode'" in sql:
            return [
                {
                    "group_key": "support-route",
                    "group_mode": self.group_mode,
                    "deployment_id": "dep-a",
                    "enabled": True,
                    "deployment_mode": self.group_mode,
                }
            ]
        if "status = $2" in sql and "SELECT policy_json" in sql:
            return [{"policy_json": self.current_policy}] if self.current_policy is not None else []
        if "SELECT policy_json, semantics_version" in sql and "status = 'published'" in sql:
            return (
                [{"policy_json": self.current_policy, "semantics_version": 2}]
                if self.current_policy is not None
                else []
            )
        if "SELECT route_policy_id, policy_json" in sql and "status = 'draft'" in sql:
            return (
                [{"route_policy_id": "draft-1", "policy_json": self.draft_policy}]
                if self.draft_policy is not None
                else []
            )
        if "SELECT route_policy_id" in sql and "status = 'draft'" in sql:
            return (
                [{"route_policy_id": "draft-1", "semantics_version": 2}]
                if self.draft_policy is not None
                else []
            )
        if "WHERE route_policy_id = $1" in sql and "SELECT policy_json" in sql:
            return [{"policy_json": self.draft_policy}] if self.draft_policy is not None else []
        if "WHERE route_group_id = $1" in sql and "version = $2" in sql:
            return (
                [{"policy_json": self.rollback_policy}] if self.rollback_policy is not None else []
            )
        if "INSERT INTO deltallm_routepolicy" in sql:
            if self.fail_insert:
                raise RuntimeError("simulated insert failure")
            return [
                _policy_row(
                    policy_json=json.loads(str(params[3])),
                    semantics_version=int(params[2]),
                )
            ]
        if "SET policy_json = $2::jsonb" in sql:
            return [_policy_row(policy_json=json.loads(str(params[1])))]
        if "SET status = 'published'" in sql:
            return [
                _policy_row(
                    policy_json=dict(self.draft_policy or {}),
                    semantics_version=2,
                )
            ]
        if "UPDATE deltallm_routeruntimestate" in sql:
            return [{"revision": 1}]
        return []

    async def execute_raw(self, sql: str, *params: object) -> int:
        self.executions.append((sql, params))
        return 1


class _TransactionContext:
    def __init__(self, owner: "_TransactionalRoutePolicyDB") -> None:
        self.owner = owner

    async def __aenter__(self) -> _RoutePolicyDB:
        self.owner.started += 1
        return self.owner.transaction

    async def __aexit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        del exc, traceback
        if exc_type is None:
            self.owner.committed += 1
        else:
            self.owner.rolled_back += 1
        return False


class _TransactionalRoutePolicyDB:
    def __init__(self, transaction: _RoutePolicyDB) -> None:
        self.transaction = transaction
        self.started = 0
        self.committed = 0
        self.rolled_back = 0

    def tx(self) -> _TransactionContext:
        return _TransactionContext(self)


def _policy_row(*, policy_json: dict, semantics_version: int = 2) -> dict:
    return {
        "route_policy_id": "policy-2",
        "route_group_id": "group-1",
        "version": 2,
        "semantics_version": semantics_version,
        "status": "published",
        "policy_json": policy_json,
        "published_by": "admin_api",
    }


class _RuntimeRouteGroupDB:
    async def query_raw(self, sql: str, *params: object) -> list[dict]:
        del params
        if "FROM deltallm_routeruntimestate runtime" in sql:
            return [
                {
                    "runtime_revision": 2,
                    "route_group_id": "group-1",
                    "group_key": "support-route",
                    "mode": "chat",
                    "enabled": True,
                    "routing_strategy": "weighted",
                    "policy_version": 2,
                    "policy_semantics_version": 2,
                    "policy_json": {"members": [{"deployment_id": "dep-b", "weight": 9}]},
                    "members": [
                        {
                            "deployment_id": "dep-a",
                            "enabled": True,
                            "weight": 1,
                            "priority": 0,
                        },
                        {
                            "deployment_id": "dep-b",
                            "enabled": True,
                            "weight": 2,
                            "priority": 1,
                        },
                    ],
                },
            ]
        return []


@pytest.mark.asyncio
async def test_publish_policy_locks_group_and_preserves_opaque_fields() -> None:
    transaction = _RoutePolicyDB(
        current_policy={
            "strategy": "weighted",
            "server_revision": 4,
            "retry": {"max_attempts": 3, "server_classification": "strict"},
        }
    )
    prisma = _TransactionalRoutePolicyDB(transaction)
    repository = RouteGroupRepository(prisma)

    record = await repository.publish_policy(
        "support-route",
        {"strategy": "least-busy", "retry": {"max_attempts": 1}},
        published_by="admin_api",
    )

    assert record is not None
    assert record.policy_json == {
        "server_revision": 4,
        "strategy": "least-busy",
        "retry": {"server_classification": "strict", "max_attempts": 1},
    }
    assert prisma.started == 1
    assert prisma.committed == 1
    assert prisma.rolled_back == 0
    assert "FOR UPDATE" in transaction.calls[0][0]
    assert any("status = $2" in sql for sql, _ in transaction.calls)
    assert "status = 'archived'" in transaction.executions[0][0]
    insert_sql = next(
        sql for sql, _ in transaction.calls if "INSERT INTO deltallm_routepolicy" in sql
    )
    assert "$3, $2, $4::jsonb" in " ".join(insert_sql.split())


@pytest.mark.asyncio
async def test_publish_policy_does_not_allow_client_to_overwrite_opaque_fields() -> None:
    transaction = _RoutePolicyDB(current_policy={"strategy": "weighted", "server_revision": 4})
    prisma = _TransactionalRoutePolicyDB(transaction)

    record = await RouteGroupRepository(prisma).publish_policy(
        "support-route",
        {"strategy": "least-busy", "server_revision": 999},
    )

    assert record is not None
    assert record.policy_json["server_revision"] == 4


@pytest.mark.asyncio
async def test_publish_policy_uses_null_to_delete_context_without_ambiguous_omission() -> None:
    existing = {
        "strategy": "weighted",
        "context": {
            "mode": "smallest-sufficient",
            "unknown_capacity": "exclude",
            "server_capacity_source": "catalog",
        },
    }

    omitted_transaction = _RoutePolicyDB(current_policy=existing)
    omitted = await RouteGroupRepository(
        _TransactionalRoutePolicyDB(omitted_transaction)
    ).publish_policy(
        "support-route",
        {"strategy": "least-busy"},
    )
    deleted_transaction = _RoutePolicyDB(current_policy=existing)
    deleted = await RouteGroupRepository(
        _TransactionalRoutePolicyDB(deleted_transaction)
    ).publish_policy(
        "support-route",
        {"strategy": "least-busy", "context": None},
    )

    assert omitted is not None
    assert omitted.policy_json["context"] == existing["context"]
    assert deleted is not None
    assert "context" not in deleted.policy_json


@pytest.mark.asyncio
async def test_runtime_policy_member_list_excludes_omitted_base_members() -> None:
    groups = await RouteGroupRepository(_RuntimeRouteGroupDB()).list_runtime_groups()

    assert groups[0]["members"] == [
        {
            "deployment_id": "dep-b",
            "enabled": True,
            "weight": 9,
            "priority": 1,
        }
    ]


@pytest.mark.asyncio
async def test_rollback_copies_target_document_exactly() -> None:
    source = {
        "strategy": "weighted",
        "server_revision": 2,
        "members": [{"deployment_id": "dep-a", "server_assignment": "stable"}],
    }
    transaction = _RoutePolicyDB(rollback_policy=source)
    prisma = _TransactionalRoutePolicyDB(transaction)

    record = await RouteGroupRepository(prisma).rollback_policy(
        "support-route",
        target_version=1,
        published_by="admin_api",
    )

    assert record is not None
    assert record.policy_json == source
    insert_params = next(
        params for sql, params in transaction.calls if "INSERT INTO deltallm_routepolicy" in sql
    )
    assert json.loads(str(insert_params[3])) == source
    assert int(insert_params[2]) == 1


@pytest.mark.asyncio
async def test_rollback_rejects_policy_with_removed_member_before_archiving_current() -> None:
    transaction = _RoutePolicyDB(
        rollback_policy={"members": [{"deployment_id": "removed-deployment"}]}
    )
    prisma = _TransactionalRoutePolicyDB(transaction)

    with pytest.raises(RoutePolicyStateConflictError, match="unknown members"):
        await RouteGroupRepository(prisma).rollback_policy(
            "support-route",
            target_version=1,
        )

    assert transaction.executions == []
    assert prisma.rolled_back == 1


@pytest.mark.asyncio
async def test_draft_update_preserves_opaque_fields() -> None:
    transaction = _RoutePolicyDB(draft_policy={"strategy": "weighted", "server_revision": 8})
    prisma = _TransactionalRoutePolicyDB(transaction)

    record = await RouteGroupRepository(prisma).save_draft_policy(
        "support-route",
        {"strategy": "least-busy"},
    )

    assert record is not None
    assert record.policy_json == {"server_revision": 8, "strategy": "least-busy"}
    assert prisma.committed == 1


@pytest.mark.asyncio
async def test_publish_latest_draft_preserves_document_exactly() -> None:
    source = {"strategy": "weighted", "server_revision": 5}
    transaction = _RoutePolicyDB(
        current_policy={"context": {"mode": "eligible-only"}},
        draft_policy=source,
    )
    prisma = _TransactionalRoutePolicyDB(transaction)

    record = await RouteGroupRepository(prisma).publish_latest_draft(
        "support-route",
        published_by="admin_api",
    )

    assert record is not None
    assert record.policy_json == source
    assert prisma.committed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["publish", "save_draft", "publish_latest_draft", "rollback"],
)
async def test_policy_lifecycle_rejects_context_for_unsupported_group_mode(
    operation: str,
) -> None:
    context_policy = {"context": {"mode": "eligible-only"}}
    transaction = _RoutePolicyDB(
        draft_policy=context_policy if operation == "publish_latest_draft" else None,
        rollback_policy=context_policy if operation == "rollback" else None,
        group_mode="rerank",
    )
    repository = RouteGroupRepository(_TransactionalRoutePolicyDB(transaction))

    with pytest.raises(RoutePolicyStateConflictError, match="route group mode 'rerank'"):
        if operation == "publish":
            await repository.publish_policy("support-route", context_policy)
        elif operation == "save_draft":
            await repository.save_draft_policy("support-route", context_policy)
        elif operation == "publish_latest_draft":
            await repository.publish_latest_draft("support-route")
        else:
            await repository.rollback_policy("support-route", target_version=1)


@pytest.mark.asyncio
async def test_group_mode_change_rejects_existing_context_policy() -> None:
    transaction = _RoutePolicyDB(
        current_policy={"context": {"mode": "eligible-only"}},
        group_mode="rerank",
    )
    repository = RouteGroupRepository(transaction)

    with pytest.raises(
        RoutePolicyStateConflictError,
        match="route-group change would invalidate the published policy",
    ):
        await repository._validate_published_policy_after_group_change("group-1")


@pytest.mark.asyncio
async def test_publish_failure_rolls_back_archival_transaction() -> None:
    transaction = _RoutePolicyDB(current_policy={}, fail_insert=True)
    prisma = _TransactionalRoutePolicyDB(transaction)

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        await RouteGroupRepository(prisma).publish_policy(
            "support-route",
            {"strategy": "weighted"},
        )

    assert prisma.committed == 0
    assert prisma.rolled_back == 1
    assert len(transaction.executions) == 1


@pytest.mark.asyncio
async def test_policy_mutations_require_transaction_support() -> None:
    repository = RouteGroupRepository(SimpleNamespace(query_raw=None))

    with pytest.raises(RuntimeError, match="publish_policy requires transaction support"):
        await repository.publish_policy("support-route", {"strategy": "weighted"})
