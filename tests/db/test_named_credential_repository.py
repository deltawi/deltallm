from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.db.named_credentials import NamedCredentialRepository


class _TransactionContext:
    def __init__(self, database: "_FakeNamedCredentialDatabase") -> None:
        self.database = database
        self.snapshot: tuple[dict[str, object], int] | None = None

    async def __aenter__(self) -> "_FakeNamedCredentialDatabase":
        self.snapshot = (deepcopy(self.database.credential), self.database.runtime_revision)
        return self.database

    async def __aexit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        del exc, traceback
        if exc_type is not None and self.snapshot is not None:
            self.database.credential, self.database.runtime_revision = self.snapshot
        return False


class _FakeNamedCredentialDatabase:
    def __init__(self, *, linked_deployments: int, fail_revision: bool = False) -> None:
        self.credential: dict[str, object] = {
            "credential_id": "cred-1",
            "name": "old-name",
            "provider": "openai",
            "connection_config": {"api_key": "secret"},
            "metadata": None,
            "created_by_account_id": None,
            "created_at": None,
            "updated_at": None,
        }
        self.linked_deployments = linked_deployments
        self.fail_revision = fail_revision
        self.runtime_revision = 0

    def tx(self) -> _TransactionContext:
        return _TransactionContext(self)

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "UPDATE deltallm_namedcredential" in query:
            self.credential.update(
                {
                    "name": str(params[1]),
                    "provider": str(params[2]),
                    "connection_config": json.loads(str(params[3])),
                    "metadata": json.loads(str(params[4])) if params[4] is not None else None,
                }
            )
            return [dict(self.credential)]
        if "COUNT(*)::int AS count" in query and "deltallm_modeldeployment" in query:
            return [{"count": self.linked_deployments}]
        if "UPDATE deltallm_routeruntimestate" in query:
            if self.fail_revision:
                raise RuntimeError("revision write failed")
            self.runtime_revision += 1
            return [{"revision": self.runtime_revision}]
        return []


@pytest.mark.asyncio
async def test_linked_named_credential_update_bumps_routing_revision_atomically():
    database = _FakeNamedCredentialDatabase(linked_deployments=2)

    updated = await NamedCredentialRepository(database).update(
        "cred-1",
        name="new-name",
        provider="openai",
        connection_config={"api_key": "new-secret"},
        metadata={"region": "us"},
    )

    assert updated is not None
    assert updated.name == "new-name"
    assert database.runtime_revision == 1


@pytest.mark.asyncio
async def test_unlinked_named_credential_update_does_not_bump_routing_revision():
    database = _FakeNamedCredentialDatabase(linked_deployments=0)

    updated = await NamedCredentialRepository(database).update(
        "cred-1",
        name="new-name",
        provider="openai",
        connection_config={"api_key": "new-secret"},
        metadata=None,
    )

    assert updated is not None
    assert database.runtime_revision == 0


@pytest.mark.asyncio
async def test_revision_failure_rolls_back_linked_named_credential_update():
    database = _FakeNamedCredentialDatabase(linked_deployments=1, fail_revision=True)

    with pytest.raises(RuntimeError, match="revision write failed"):
        await NamedCredentialRepository(database).update(
            "cred-1",
            name="new-name",
            provider="openai",
            connection_config={"api_key": "new-secret"},
            metadata=None,
        )

    assert database.credential["name"] == "old-name"
    assert database.runtime_revision == 0
