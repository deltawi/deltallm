from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.batch.models import (
    BatchFileRecord,
    BatchItemRecord,
    BatchJobRecord,
    BatchJobStatus,
    BatchWebhookDeliveryStatus,
    BatchWebhookEventType,
    BatchWebhookOutboxRecord,
)
from src.batch.request_validation import parse_batch_input_line
from src.batch.service import BatchService
from src.batch.webhooks.events import build_batch_webhook_event, canonical_batch_webhook_event_bytes
from src.batch.webhooks.signing import build_batch_webhook_headers
from src.batch.worker_artifacts import BatchArtifactFinalizer
from src.batch.worker_types import BatchWorkerConfig
from src.models.responses import UserAPIKeyAuth


FIXTURE_DIR = Path(__file__).parent / "fixtures"
HTTP_FIXTURE_PATH = FIXTURE_DIR / "files_batches_http.json"
CREATED_AT = datetime.fromtimestamp(1_700_000_000, tz=UTC)
STARTED_AT = datetime.fromtimestamp(1_700_000_060, tz=UTC)
COMPLETED_AT = datetime.fromtimestamp(1_700_000_300, tz=UTC)
EXPIRES_AT = datetime.fromtimestamp(1_702_592_000, tz=UTC)


def _load_http_fixture() -> dict[str, Any]:
    return json.loads(HTTP_FIXTURE_PATH.read_text(encoding="utf-8"))


def _job(
    *,
    batch_id: str = "batch_completed_001",
    status: BatchJobStatus = BatchJobStatus.COMPLETED,
    foreign: bool = False,
) -> BatchJobRecord:
    is_completed = status is BatchJobStatus.COMPLETED
    is_cancelling = status is BatchJobStatus.IN_PROGRESS
    return BatchJobRecord(
        batch_id=batch_id,
        endpoint="/v1/embeddings",
        status=status,
        execution_mode="managed_internal",
        input_file_id="file_input_001",
        output_file_id="file_output_001" if is_completed else None,
        error_file_id=None,
        model="text-embedding-3-small",
        metadata={"customer_job_id": "job-42"},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=2,
        in_progress_items=1 if is_cancelling else 0,
        completed_items=1 if is_cancelling else (2 if is_completed else 0),
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=STARTED_AT if is_cancelling else None,
        status_last_updated_at=COMPLETED_AT if is_completed else CREATED_AT,
        created_by_api_key="foreign-key" if foreign else "fixture-owner-key",
        created_by_user_id=None,
        created_by_team_id="foreign-team" if foreign else "team-default",
        created_by_organization_id="foreign-org" if foreign else "org-default",
        created_at=CREATED_AT,
        started_at=STARTED_AT if is_completed or is_cancelling else None,
        completed_at=COMPLETED_AT if is_completed else None,
        expires_at=EXPIRES_AT,
        webhook_config_ciphertext="encrypted-fixture-placeholder",
        webhook_config_fingerprint="f" * 64,
    )


class _ContractStorage:
    backend_name = "memory"

    def __init__(self) -> None:
        self.written_payload: bytes | None = None

    async def write_chunks(self, *, purpose: str, filename: str, chunks):  # noqa: ANN001
        del purpose, filename
        payload = b"".join([chunk async for chunk in chunks])
        self.written_payload = payload
        return (
            "input/file_input_001.jsonl",
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )

    async def read_bytes(self, storage_key: str) -> bytes:
        assert storage_key == "output/file_output_001.jsonl"
        return (FIXTURE_DIR / "batch_output.jsonl").read_bytes()

    async def delete(self, storage_key: str) -> None:
        raise AssertionError(f"unexpected artifact deletion: {storage_key}")


class _ContractRepository:
    def __init__(self) -> None:
        self.created_file: BatchFileRecord | None = None

    async def create_file(
        self,
        *,
        purpose: str,
        filename: str,
        bytes_size: int,
        storage_backend: str,
        storage_key: str,
        checksum: str,
        created_by_api_key: str,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        created_by_organization_id: str | None,
        expires_at: datetime,
    ) -> BatchFileRecord:
        del checksum, created_by_api_key, created_by_user_id, expires_at
        self.created_file = BatchFileRecord(
            file_id="file_input_001",
            purpose=purpose,
            filename=filename,
            bytes=bytes_size,
            status="uploaded",
            storage_backend=storage_backend,
            storage_key=storage_key,
            checksum=None,
            created_by_api_key="fixture-owner-key",
            created_by_user_id=None,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
            created_at=CREATED_AT,
            expires_at=EXPIRES_AT,
        )
        return self.created_file

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        if file_id == "file_missing":
            return None
        if file_id == "file_foreign":
            return replace(
                self._input_file(),
                file_id=file_id,
                created_by_team_id="foreign-team",
                created_by_organization_id="foreign-org",
            )
        if file_id == "file_output_001":
            return replace(
                self._input_file(),
                file_id=file_id,
                purpose="batch_output",
                filename="batch_output.jsonl",
                bytes=(FIXTURE_DIR / "batch_output.jsonl").stat().st_size,
                storage_key="output/file_output_001.jsonl",
            )
        return self._input_file()

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        if batch_id == "batch_missing":
            return None
        if batch_id == "batch_foreign":
            return _job(batch_id=batch_id, foreign=True)
        return _job(batch_id=batch_id)

    async def list_jobs(self, **kwargs: Any) -> list[BatchJobRecord]:
        assert kwargs["limit"] == 20
        assert kwargs["created_by_api_key"]
        assert kwargs["created_by_team_id"] == "team-default"
        assert kwargs["created_by_organization_id"] is None
        assert set(kwargs) == {
            "limit",
            "created_by_api_key",
            "created_by_team_id",
            "created_by_organization_id",
        }
        return [_job()]

    async def request_cancel(self, batch_id: str) -> BatchJobRecord:
        return _job(batch_id=batch_id, status=BatchJobStatus.IN_PROGRESS)

    def _input_file(self) -> BatchFileRecord:
        return self.created_file or BatchFileRecord(
            file_id="file_input_001",
            purpose="batch",
            filename="batch_input.jsonl",
            bytes=(FIXTURE_DIR / "batch_input.jsonl").stat().st_size,
            status="uploaded",
            storage_backend="memory",
            storage_key="input/file_input_001.jsonl",
            checksum=None,
            created_by_api_key="fixture-owner-key",
            created_by_user_id=None,
            created_by_team_id="team-default",
            created_by_organization_id="org-default",
            created_at=CREATED_AT,
            expires_at=EXPIRES_AT,
        )


class _ContractCreateSessionService:
    def __init__(self) -> None:
        self.last_webhook: object | None = None

    async def create_batch(self, **kwargs: Any) -> SimpleNamespace:
        self.last_webhook = kwargs["webhook"]
        return SimpleNamespace(
            job=_job(batch_id="batch_queued_001", status=BatchJobStatus.QUEUED),
            audit_metadata={"idempotency_replayed": False},
        )


@pytest.fixture
def contract_runtime(test_app):
    repository = _ContractRepository()
    storage = _ContractStorage()
    create_session_service = _ContractCreateSessionService()
    service = BatchService(
        repository=repository,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        create_session_service=create_session_service,  # type: ignore[arg-type]
    )
    test_app.state.batch_repository = repository
    test_app.state.batch_service = service
    return SimpleNamespace(
        repository=repository,
        storage=storage,
        create_session_service=create_session_service,
    )


def _headers(test_app) -> dict[str, str]:
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


def _assert_json_response(response, fixture: dict[str, Any]) -> None:  # noqa: ANN001
    assert response.status_code == fixture["status_code"]
    assert response.headers["content-type"] == "application/json"
    assert response.json() == fixture["body"]


@pytest.mark.asyncio
async def test_file_upload_wire_contract_is_frozen(
    client,
    test_app,
    contract_runtime,
) -> None:
    fixture = _load_http_fixture()["file_upload"]
    request_fixture = fixture["request"]
    input_bytes = (FIXTURE_DIR / "batch_input.jsonl").read_bytes()

    response = await client.post(
        request_fixture["path"],
        headers=_headers(test_app),
        files={
            "file": (
                request_fixture["multipart_filename"],
                input_bytes,
                "application/jsonl",
            )
        },
        data={"purpose": request_fixture["multipart_purpose"]},
    )

    _assert_json_response(response, fixture["response"])
    assert (
        contract_runtime.repository.created_file.purpose
        == request_fixture["service_observed_purpose"]
    )
    assert contract_runtime.storage.written_payload == input_bytes


@pytest.mark.asyncio
async def test_file_retrieve_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["file_retrieve"]["response"]

    response = await client.get(
        "/v1/files/file_input_001",
        headers=_headers(test_app),
    )

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
async def test_file_content_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["file_content"]["response"]

    response = await client.get(
        "/v1/files/file_output_001/content",
        headers=_headers(test_app),
    )

    assert response.status_code == fixture["status_code"]
    assert response.headers["content-type"] == fixture["content_type"]
    assert response.content == (FIXTURE_DIR / fixture["fixture"]).read_bytes()


@pytest.mark.asyncio
async def test_batch_create_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    fixture = _load_http_fixture()["batch_create"]
    request_payload = dict(fixture["request"])
    webhook_fixture = request_payload.pop("webhook")
    request_payload["webhook"] = {
        "url": webhook_fixture["url"],
        "signing_secret": "w" * 32,
    }

    response = await client.post(
        "/v1/batches",
        headers=_headers(test_app),
        json=request_payload,
    )

    _assert_json_response(response, fixture["response"])
    assert webhook_fixture["signing_secret_present"] is True
    assert contract_runtime.create_session_service.last_webhook == request_payload["webhook"]
    assert request_payload["webhook"]["signing_secret"] not in response.text


@pytest.mark.asyncio
async def test_batch_retrieve_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["batch_retrieve"]["response"]

    response = await client.get(
        "/v1/batches/batch_completed_001",
        headers=_headers(test_app),
    )

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
async def test_batch_list_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["batch_list"]["response"]

    response = await client.get("/v1/batches", headers=_headers(test_app))

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
async def test_batch_cancel_wire_contract_is_frozen(client, test_app, contract_runtime) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["batch_cancel"]["response"]

    response = await client.post(
        "/v1/batches/batch_completed_001/cancel",
        headers=_headers(test_app),
    )

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "path"),
    [
        ("file_missing", "/v1/files/file_missing"),
        ("file_foreign", "/v1/files/file_foreign"),
        ("batch_missing", "/v1/batches/batch_missing"),
        ("batch_foreign", "/v1/batches/batch_foreign"),
    ],
)
async def test_current_missing_and_foreign_error_wire_contracts_are_frozen(
    client,
    test_app,
    contract_runtime,
    fixture_name: str,
    path: str,
) -> None:
    del contract_runtime
    fixture = _load_http_fixture()["errors"][fixture_name]

    response = await client.get(path, headers=_headers(test_app))

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
async def test_current_file_validation_error_wire_contract_is_frozen(client, test_app) -> None:
    fixture = _load_http_fixture()["errors"]["file_upload_validation"]

    response = await client.post("/v1/files", headers=_headers(test_app))

    _assert_json_response(response, fixture)


@pytest.mark.asyncio
async def test_current_batch_validation_error_wire_contract_is_frozen(client, test_app) -> None:
    fixture = _load_http_fixture()["errors"]["batch_body_validation"]

    response = await client.post("/v1/batches", headers=_headers(test_app))

    _assert_json_response(response, fixture)


def test_batch_jsonl_success_and_error_rows_are_byte_frozen() -> None:
    finalizer = BatchArtifactFinalizer(
        repository=object(),  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="contract-fixture"),
    )
    success_item = BatchItemRecord(
        item_id="item_001",
        batch_id="batch_completed_001",
        line_number=1,
        custom_id="embed-1",
        status="completed",
        request_body={"model": "text-embedding-3-small", "input": "first document"},
        response_body={
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
        },
        error_body=None,
        usage={"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
        provider_cost=0.0,
        billed_cost=0.0,
        attempts=1,
        last_error=None,
        locked_by=None,
        lease_expires_at=None,
        created_at=CREATED_AT,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )
    error_item = replace(
        success_item,
        item_id="item_002",
        line_number=2,
        custom_id="embed-2",
        status="failed",
        response_body=None,
        error_body={"message": "provider unavailable", "type": "BatchItemError"},
        last_error="provider unavailable",
    )

    success_line = json.dumps(finalizer._serialize_completed_artifact_row(success_item)) + "\n"
    error_line = json.dumps(finalizer._serialize_failed_artifact_row(error_item)) + "\n"

    assert success_line.encode() == (FIXTURE_DIR / "batch_output.jsonl").read_bytes()
    assert error_line.encode() == (FIXTURE_DIR / "batch_error.jsonl").read_bytes()


def test_batch_input_jsonl_rows_remain_accepted_by_the_runtime_validator() -> None:
    seen_custom_ids: set[str] = set()
    parsed_rows = []
    for line_number, raw_line in enumerate(
        (FIXTURE_DIR / "batch_input.jsonl").read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        parsed_rows.append(
            parse_batch_input_line(
                raw_line,
                line_number=line_number,
                endpoint="/v1/embeddings",
                auth=UserAPIKeyAuth(api_key="fixture-key"),
                seen_custom_ids=seen_custom_ids,
                callable_target_grant_service=None,
                callable_target_scope_policy_mode="disabled",
                model_access_validator=lambda *args, **kwargs: None,
            )
        )

    assert [row.custom_id for row in parsed_rows if row is not None] == [
        "embed-1",
        "embed-2",
    ]


def test_batch_webhook_event_body_and_headers_are_byte_frozen() -> None:
    fixture = json.loads((FIXTURE_DIR / "batch_webhook_delivery.json").read_text(encoding="utf-8"))
    event = build_batch_webhook_event(
        _job(),
        event_id="evt_contract_001",
        created_at=datetime.fromtimestamp(1_700_000_400, tz=UTC),
    )
    record = BatchWebhookOutboxRecord(
        event_id=event.event_id,
        batch_id="batch_completed_001",
        event_type=BatchWebhookEventType.COMPLETED,
        target_config_ciphertext="encrypted-fixture-placeholder",
        payload_json=event.payload_json,
        payload_sha256=event.payload_sha256,
        status=BatchWebhookDeliveryStatus.PROCESSING,
        attempt_count=2,
        max_attempts=8,
        next_attempt_at=CREATED_AT,
        last_status_code=None,
        last_error=None,
        locked_by="contract-worker",
        lease_expires_at=EXPIRES_AT,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        delivered_at=None,
    )
    headers = build_batch_webhook_headers(
        record,
        signing_secret="w" * 32,
        timestamp=1_700_000_500,
    )

    assert event.payload_json == fixture["event"]
    assert event.payload_sha256 == fixture["canonical_body_sha256"]
    assert headers == fixture["headers"]
    rendered = canonical_batch_webhook_event_bytes(event.payload_json)
    assert b"encrypted-fixture-placeholder" not in rendered
    assert b"signing_secret" not in rendered


def test_omitted_and_null_fields_are_explicit_in_the_golden_contract() -> None:
    fixture = _load_http_fixture()
    file_body = fixture["file_retrieve"]["response"]["body"]
    batch_body = fixture["batch_retrieve"]["response"]["body"]

    assert "expires_at" not in file_body
    assert "status_details" not in file_body
    assert batch_body["error_file_id"] is None
    assert batch_body["errors"] is None
    assert batch_body["failed_at"] is None
    assert batch_body["expired_at"] is None
    assert {
        "model",
        "cancelling_at",
        "cancelled_at",
        "finalizing_at",
        "usage",
    }.isdisjoint(batch_body)
