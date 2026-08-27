from __future__ import annotations

import pytest

from src.db.callable_targets import CallableTargetBindingRepository
from src.db.repositories import ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository
from src.services.route_group_mutations import RouteGroupMutationService


class _TransactionContext:
    def __init__(self, database: "_Database") -> None:
        self.database = database
        self.snapshot: tuple[bool, int, list[str]] | None = None

    async def __aenter__(self) -> "_Database":
        self.snapshot = (
            self.database.group_exists,
            self.database.revision,
            list(self.database.binding_ids),
        )
        return self.database

    async def __aexit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        del exc, traceback
        if exc_type is not None and self.snapshot is not None:
            (
                self.database.group_exists,
                self.database.revision,
                self.database.binding_ids,
            ) = self.snapshot
        return False


class _Database:
    def __init__(
        self,
        *,
        fail_binding_delete: bool = False,
        model_exists: bool = False,
    ) -> None:
        self.group_exists = True
        self.revision = 0
        self.binding_ids = ["binding-1", "binding-2"]
        self.fail_binding_delete = fail_binding_delete
        self.model_exists = model_exists
        self.callable_key_locks: list[str] = []

    def tx(self) -> _TransactionContext:
        return _TransactionContext(self)

    async def query_raw(self, query: str, *args):  # noqa: ANN201
        if "pg_advisory_xact_lock" in query:
            self.callable_key_locks.append(str(args[1]))
            return []
        if "SELECT route_group_id" in query and "FOR UPDATE" in query:
            return [{"route_group_id": "group-1"}] if self.group_exists else []
        if "DELETE FROM deltallm_routegroup" in query:
            if not self.group_exists:
                return []
            self.group_exists = False
            return [{"route_group_id": "group-1"}]
        if "UPDATE deltallm_routeruntimestate" in query:
            self.revision += 1
            return [{"revision": self.revision}]
        if "FROM deltallm_modeldeployment" in query:
            return [{"deployment_id": "model-1"}] if self.model_exists else []
        if "DELETE FROM deltallm_callabletargetbinding" in query:
            if self.fail_binding_delete:
                raise RuntimeError("binding cleanup failed")
            deleted = [
                {"callable_target_binding_id": binding_id} for binding_id in self.binding_ids
            ]
            self.binding_ids = []
            return deleted
        return []


def _service(
    database: _Database,
    *,
    config_model_exists: bool = False,
) -> RouteGroupMutationService:
    return RouteGroupMutationService(
        route_groups=RouteGroupRepository(database),
        callable_bindings=CallableTargetBindingRepository(database),
        model_deployments=ModelDeploymentRepository(database),
        model_registry_getter=lambda: {"support-route": [object()]} if config_model_exists else {},
    )


@pytest.mark.asyncio
async def test_route_group_delete_commits_group_bindings_and_revision_together() -> None:
    database = _Database()

    result = await _service(database).delete_group("support-route")

    assert result.deleted is True
    assert result.callable_bindings_deleted == 2
    assert database.group_exists is False
    assert database.binding_ids == []
    assert database.revision == 1
    assert database.callable_key_locks == ["support-route"]


@pytest.mark.asyncio
async def test_route_group_delete_rolls_back_when_binding_cleanup_fails() -> None:
    database = _Database(fail_binding_delete=True)

    with pytest.raises(RuntimeError, match="binding cleanup failed"):
        await _service(database).delete_group("support-route")

    assert database.group_exists is True
    assert database.binding_ids == ["binding-1", "binding-2"]
    assert database.revision == 0


@pytest.mark.parametrize(
    ("model_exists", "config_model_exists"),
    [(True, False), (False, True)],
)
async def test_route_group_delete_preserves_bindings_for_revealed_model(
    model_exists: bool,
    config_model_exists: bool,
) -> None:
    database = _Database(model_exists=model_exists)

    result = await _service(
        database,
        config_model_exists=config_model_exists,
    ).delete_group("support-route")

    assert result.deleted is True
    assert result.callable_bindings_deleted == 0
    assert database.group_exists is False
    assert database.binding_ids == ["binding-1", "binding-2"]
    assert database.revision == 1
