from __future__ import annotations

import hashlib
from types import SimpleNamespace

import httpx
import pytest


class _RecordingAuditService:
    def __init__(self) -> None:
        self.records: list[tuple[object, list[object], bool]] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.records.append((event, list(payloads or []), critical))


class _BatchFileRecord:
    def __init__(self, file_id: str, created_by_api_key: str, created_by_team_id: str | None) -> None:
        self.file_id = file_id
        self.created_by_api_key = created_by_api_key
        self.created_by_team_id = created_by_team_id


class _FakeBatchRepository:
    async def get_file(self, file_id: str):  # noqa: ANN201
        key_hash = hashlib.sha256("test-salt:sk-test".encode("utf-8")).hexdigest()
        return _BatchFileRecord(file_id=file_id, created_by_api_key=key_hash, created_by_team_id=None)


class _FakeBatchService:
    async def create_file(self, auth, upload, purpose):  # noqa: ANN001, ANN201
        del auth, upload, purpose
        return {"id": "file-1", "object": "file", "status": "processed"}

    def file_to_response(self, record):  # noqa: ANN001, ANN201
        return {"id": record.file_id, "object": "file"}

    async def get_file_content(self, file_id: str, auth):  # noqa: ANN201, ANN001
        del auth
        return f'{{"id":"{file_id}"}}'.encode("utf-8")

    async def create_embeddings_batch(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return {"id": "batch-1", "status": "validating"}

    async def get_batch(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return {"id": "batch-1", "status": "in_progress"}

    async def list_batches(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return [{"id": "batch-1", "status": "in_progress"}]

    async def cancel_batch(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return {"id": "batch-1", "status": "cancelled"}


class _SpendQueryDB:
    async def query_raw(self, query: str, *args):  # noqa: ANN201
        del args
        normalized = " ".join(query.lower().split())
        if "count(*) as total" in normalized:
            return [{"total": 1}]
        if "from deltallm_spendlogs" in normalized and "order by start_time desc" in normalized:
            return [
                {
                    "id": "log_1",
                    "request_id": "req_1",
                    "call_type": "completion",
                    "model": "gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "hashed-key",
                    "spend": 0.01,
                    "total_tokens": 20,
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "start_time": "2026-02-13T00:00:00+00:00",
                    "end_time": "2026-02-13T00:00:01+00:00",
                    "user": "u1",
                    "team_id": "t1",
                    "cache_hit": False,
                    "request_tags": ["tag-a"],
                }
            ]
        if "total_requests" in normalized and "from deltallm_spendlogs" in normalized:
            return [
                {
                    "total_spend": 1.25,
                    "total_tokens": 200,
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_requests": 5,
                }
            ]
        if "group by model" in normalized:
            return [{"model": "gpt-4o-mini", "total_spend": 1.25, "total_tokens": 200, "request_count": 5}]
        if "from deltallm_verificationtoken" in normalized:
            return [{"token": "t", "key_name": "k", "spend": 1.0, "max_budget": 10.0, "user_id": None, "team_id": None}]
        if "from deltallm_teamtable" in normalized:
            return [{"team_id": "team-1", "team_alias": "team", "spend": 1.0, "max_budget": 10.0}]
        if "group by end_user_id" in normalized:
            return [{"end_user_id": "eu-1", "total_spend": 1.0, "request_count": 2}]
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "expected_action"),
    [
        ("/v1/images/generations", {"model": "gpt-4o-mini", "prompt": "cat"}, "IMAGE_GENERATION_REQUEST"),
        ("/v1/audio/speech", {"model": "gpt-4o-mini", "input": "hello", "voice": "alloy"}, "AUDIO_SPEECH_REQUEST"),
        ("/v1/rerank", {"model": "gpt-4o-mini", "query": "q", "documents": ["a", "b"]}, "RERANK_REQUEST"),
    ],
)
async def test_media_routes_emit_audit_success(client, test_app, path, body, expected_action):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    async def media_post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        if url.endswith("/images/generations"):
            return httpx.Response(200, json={"created": 1, "data": [{"url": "https://example.com/image.png"}]})
        if url.endswith("/audio/speech"):
            return httpx.Response(200, content=b"\x00\x01\x02")
        if url.endswith("/rerank"):
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = media_post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": f"req-{expected_action}"}

    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200
    assert audit.records
    event, _, critical = audit.records[-1]
    assert event.action == expected_action
    assert event.status == "success"
    assert event.request_id == f"req-{expected_action}"
    assert critical is True


@pytest.mark.asyncio
async def test_audio_transcriptions_emits_audit_success(client, test_app):
    from src.middleware.rate_limit import enforce_rate_limits

    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    async def _noop_rate_limit():  # noqa: ANN202
        yield

    test_app.dependency_overrides[enforce_rate_limits] = _noop_rate_limit

    async def stt_post(url: str, headers: dict[str, str], files, data, timeout: int):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        if url.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello world"})
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = stt_post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-audio-transcript"}
    files = {"file": ("audio.wav", b"abc", "audio/wav")}
    data = {"model": "gpt-4o-mini", "response_format": "json"}

    response = await client.post("/v1/audio/transcriptions", headers=headers, files=files, data=data)
    assert response.status_code == 200
    event, _, _ = audit.records[-1]
    assert event.action == "AUDIO_TRANSCRIPTION_REQUEST"
    assert event.status == "success"
    test_app.dependency_overrides.pop(enforce_rate_limits, None)


@pytest.mark.asyncio
async def test_files_and_batches_emit_audit_success(client, test_app):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    test_app.state.batch_service = _FakeBatchService()
    test_app.state.batch_repository = _FakeBatchRepository()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-files-batches"}

    response = await client.post(
        "/v1/files",
        headers=headers,
        files={"file": ("batch.jsonl", b"{}", "application/json")},
        data={"purpose": "batch"},
    )
    assert response.status_code == 200

    response = await client.get("/v1/files/file-1", headers=headers)
    assert response.status_code == 200

    response = await client.get("/v1/files/file-1/content", headers=headers)
    assert response.status_code == 200

    response = await client.post(
        "/v1/batches",
        headers=headers,
        json={"input_file_id": "file-1", "endpoint": "/v1/embeddings", "completion_window": "24h"},
    )
    assert response.status_code == 200

    response = await client.get("/v1/batches/batch-1", headers=headers)
    assert response.status_code == 200
    response = await client.get("/v1/batches", headers=headers)
    assert response.status_code == 200
    response = await client.post("/v1/batches/batch-1/cancel", headers=headers)
    assert response.status_code == 200

    actions = [record[0].action for record in audit.records]
    assert "FILE_CREATE_REQUEST" in actions
    assert "FILE_READ_REQUEST" in actions
    assert "FILE_CONTENT_READ_REQUEST" in actions
    assert "BATCH_CREATE_REQUEST" in actions
    assert "BATCH_READ_REQUEST" in actions
    assert "BATCH_LIST_REQUEST" in actions
    assert "BATCH_CANCEL_REQUEST" in actions


@pytest.mark.asyncio
async def test_spend_routes_emit_audit_success(client, test_app):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    test_app.state.prisma_manager = SimpleNamespace(client=_SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    headers = {"Authorization": "Bearer mk-test", "x-request-id": "req-spend"}
    assert (await client.get("/spend/logs", headers=headers)).status_code == 200
    assert (await client.get("/global/spend", headers=headers)).status_code == 200
    assert (await client.get("/global/spend/report", headers=headers)).status_code == 200
    assert (await client.get("/global/spend/keys", headers=headers)).status_code == 200
    assert (await client.get("/global/spend/teams", headers=headers)).status_code == 200
    assert (await client.get("/global/spend/end_users", headers=headers)).status_code == 200
    assert (await client.get("/global/spend/models", headers=headers)).status_code == 200

    actions = [record[0].action for record in audit.records]
    assert "SPEND_LOGS_READ" in actions
    assert "GLOBAL_SPEND_READ" in actions
    assert "GLOBAL_SPEND_REPORT_READ" in actions
    assert "GLOBAL_SPEND_KEYS_READ" in actions
    assert "GLOBAL_SPEND_TEAMS_READ" in actions
    assert "GLOBAL_SPEND_END_USERS_READ" in actions
    assert "GLOBAL_SPEND_MODELS_READ" in actions
