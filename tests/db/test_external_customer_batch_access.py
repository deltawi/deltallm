from __future__ import annotations

import re
from uuid import uuid4

from fastapi.routing import APIRoute
import pytest

from src.api.admin.endpoints.batches import router as batches_router
from src.api.admin.endpoints.batch_create_sessions import router as create_sessions_router
from tests.db import external_auth_fixtures as fixtures
from tests.db import test_external_customer_directory as directory

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database
directory_customer = directory.directory_customer


@pytest.fixture
async def peer_batch(directory_customer):
    fixture, exchange, peer_team, peer_account = directory_customer
    file_id, batch_id, item_id = (uuid4().hex for _ in range(3))
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_batch_file (file_id, purpose, filename, bytes, storage_key) VALUES ($1, 'batch', 'peer.jsonl', 12, $1)",
        file_id,
    )
    try:
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_batch_job (batch_id, endpoint, status, input_file_id,
                created_by_team_id, created_by_organization_id, created_by_user_id,
                created_by_owner_account_id, created_by_owner_snapshot_complete)
            VALUES ($1, '/v1/chat/completions', 'completed', $2, $3, $4, $5, $5, true)""",
            batch_id,
            file_id,
            peer_team,
            fixture.organization_id,
            peer_account,
        )
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_batch_item (item_id, batch_id, line_number, custom_id, status,
                request_body, response_body) VALUES ($1, $2, 1, 'peer-item', 'completed',
                '{"messages":[{"content":"private peer prompt"}]}', '{"content":"private peer response"}')""",
            item_id,
            batch_id,
        )
        await fixture.db.execute_raw(
            "UPDATE deltallm_organizationmembership SET role = 'org_owner' WHERE account_id = $1",
            fixture.account_id,
        )
        yield fixture, exchange, batch_id, item_id
    finally:
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_batch_item WHERE batch_id = $1", batch_id
        )
        await fixture.db.execute_raw("DELETE FROM deltallm_batch_job WHERE batch_id = $1", batch_id)
        await fixture.db.execute_raw("DELETE FROM deltallm_batch_file WHERE file_id = $1", file_id)


async def test_every_batch_admin_route_denies_external_customer_before_handler(peer_batch, client):
    _, _, batch_id, item_id = peer_batch
    for router in (batches_router, create_sessions_router):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            path = route.path.replace("{batch_id}", batch_id).replace("{item_id}", item_id)
            path = re.sub(r"\{[^}]+\}", "review-unregistered", path)
            for method in sorted(route.methods):
                response = await client.request(method, path, json={})
                assert response.status_code == 403, (method, path, response.text)
                assert "private peer" not in response.text and batch_id not in response.text


async def test_operator_batch_reads_are_preserved(peer_batch, client):
    fixture, exchange, batch_id, item_id = peer_batch
    token = await exchange.identities.sessions.create(
        account_id=fixture.account_id, mfa_verified=False
    )
    client.cookies.set("deltallm_session", token)
    listing = await client.get("/ui/api/batches", params={"search": batch_id})
    item = await client.get(f"/ui/api/batches/{batch_id}/items/{item_id}")
    assert listing.status_code == item.status_code == 200
    assert batch_id in listing.text
    assert "private peer prompt" in item.text and "private peer response" in item.text
