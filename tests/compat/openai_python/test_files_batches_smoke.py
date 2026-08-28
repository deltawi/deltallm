from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from src.models.responses import UserAPIKeyAuth


FIXTURE_DIR = Path(__file__).parents[2] / "contracts" / "fixtures"
HTTP_FIXTURE_PATH = FIXTURE_DIR / "files_batches_http.json"


def _load_http_fixture() -> dict[str, Any]:
    return json.loads(HTTP_FIXTURE_PATH.read_text(encoding="utf-8"))


class _OfficialClientKeyService:
    async def validate_key(self, raw_key: str) -> UserAPIKeyAuth:
        assert raw_key == "sk-compat-fixture"
        return UserAPIKeyAuth(
            api_key="fixture-owner-key",
            team_id="team-default",
            organization_id="org-default",
        )


class _OfficialClientFileRecord:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.created_by_api_key = "fixture-owner-key"
        self.created_by_team_id = "team-default"
        self.created_by_organization_id = "org-default"


class _OfficialClientRepository:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.file_response = fixture["file_retrieve"]["response"]["body"]

    async def get_file(self, file_id: str) -> _OfficialClientFileRecord | None:
        if file_id not in {"file_input_001", "file_output_001"}:
            return None
        return _OfficialClientFileRecord(self.file_response)


class _OfficialClientBatchService:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.uploaded_bytes: bytes | None = None
        self.uploaded_purpose: str | None = None
        self.create_arguments: dict[str, Any] | None = None

    async def create_file(self, *, auth, upload, purpose: str) -> dict[str, Any]:  # noqa: ANN001
        del auth
        self.uploaded_bytes = await upload.read()
        self.uploaded_purpose = purpose
        return self.fixture["file_retrieve"]["response"]["body"]

    def file_to_response(self, file_record: _OfficialClientFileRecord) -> dict[str, Any]:
        return file_record.response

    async def get_file_content(self, *, file_id: str, auth) -> bytes:  # noqa: ANN001
        del auth
        assert file_id == "file_output_001"
        return (FIXTURE_DIR / "batch_output.jsonl").read_bytes()

    async def create_batch_result(self, **kwargs: Any) -> SimpleNamespace:
        self.create_arguments = kwargs
        return SimpleNamespace(
            response=self.fixture["batch_create"]["response"]["body"],
            audit_metadata={"idempotency_replayed": False},
        )

    async def get_batch(self, *, batch_id: str, auth) -> dict[str, Any]:  # noqa: ANN001
        del auth
        assert batch_id == "batch_completed_001"
        return self.fixture["batch_retrieve"]["response"]["body"]

    async def list_batches(self, *, auth, limit: int) -> dict[str, Any]:  # noqa: ANN001
        del auth
        assert 1 <= limit <= 20
        return self.fixture["batch_list"]["response"]["body"]

    async def cancel_batch(self, *, batch_id: str, auth) -> dict[str, Any]:  # noqa: ANN001
        del auth
        assert batch_id == "batch_completed_001"
        return self.fixture["batch_cancel"]["response"]["body"]


@pytest.fixture
async def official_client(test_app):
    fixture = _load_http_fixture()
    service = _OfficialClientBatchService(fixture)
    test_app.state.key_service = _OfficialClientKeyService()
    test_app.state.batch_service = service
    test_app.state.batch_repository = _OfficialClientRepository(fixture)
    http_client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=test_app),
        base_url="http://deltallm.test",
    )
    client = AsyncOpenAI(
        api_key="sk-compat-fixture",
        base_url="http://deltallm.test/v1",
        http_client=http_client,
        max_retries=0,
    )
    try:
        yield SimpleNamespace(client=client, service=service)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_official_openai_python_upload_and_basic_batch_calls(official_client) -> None:
    sdk = official_client.client
    input_path = FIXTURE_DIR / "batch_input.jsonl"
    with input_path.open("rb") as input_file:
        uploaded = await sdk.files.create(file=input_file, purpose="batch")

    created = await sdk.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/embeddings",
        completion_window="24h",
        metadata={"customer_job_id": "job-42"},
        extra_headers={"Idempotency-Key": "compat-python-001"},
        extra_body={
            "webhook": {
                "url": "https://receiver.example/deltallm/batches",
                "signing_secret": "w" * 32,
            }
        },
    )
    retrieved_file = await sdk.files.retrieve(uploaded.id)
    retrieved_batch = await sdk.batches.retrieve("batch_completed_001")
    page = await sdk.batches.list(limit=20)
    cancelled = await sdk.batches.cancel("batch_completed_001")
    content = await sdk.files.content("file_output_001")

    assert uploaded.id == "file_input_001"
    assert retrieved_file.purpose == "batch"
    assert created.status == "validating"
    assert retrieved_batch.status == "completed"
    assert retrieved_batch.request_counts.completed == 2
    assert [batch.id for batch in page.data] == ["batch_completed_001"]
    assert cancelled.status == "cancelling"
    assert content.content == (FIXTURE_DIR / "batch_output.jsonl").read_bytes()
    assert official_client.service.uploaded_bytes == input_path.read_bytes()
    assert official_client.service.uploaded_purpose == "batch"
    assert official_client.service.create_arguments["idempotency_key"] == "compat-python-001"
    assert official_client.service.create_arguments["webhook"]["signing_secret"] == "w" * 32
    assert "w" * 32 not in repr(created)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="known gap: GET /v1/files is not implemented (tracked by issue #280 Slice 4)",
)
async def test_official_openai_python_file_listing_known_gap(official_client) -> None:
    page = await official_client.client.files.list(limit=1)
    assert page.data


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "known gap: batch list ignores object-ID cursors and omits has_more "
        "(tracked by issue #280 Slice 3)"
    ),
)
async def test_official_openai_python_batch_auto_pagination_known_gap(
    official_client,
) -> None:
    first_page = await official_client.client.batches.list(limit=1)
    second_page = await first_page.get_next_page()

    assert second_page.data[0].id != first_page.data[0].id
