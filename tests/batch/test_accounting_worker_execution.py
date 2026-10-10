import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.batch.accounting_native import decode_checkpoint
from src.billing.accounting_protocol import AccountingOutcome
from src.router.router import Deployment
from src.models.responses import UserAPIKeyAuth
from tests.batch.test_accounting_native import native_batch as _native_batch
from tests.test_batch_worker import (
    _AllowAllCallableTargetGrantService,
    _FailureRepository,
    _build_chat_batch_item,
    _build_chat_batch_job,
    _build_chat_batch_worker,
)
from tests.test_batch_worker_microbatch import _build_item, _build_job, _build_worker

native_batch = _native_batch


def chat_result():
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-oss",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": usage,
    }


def build_worker(owner, *, embedding, grouped):
    info = {
        "max_input_tokens": 100,
        "max_output_tokens": 10,
        "input_cost_per_token": "0.01",
        "output_cost_per_token": "0.02",
        "batch_input_cost_per_token": "0.001",
        "batch_output_cost_per_token": "0.002",
    }
    if embedding:
        info["upstream_max_batch_inputs"] = 4 if grouped else 1
        worker, repo, _, router, _ = _build_worker(deployment_model_info=info)
        job = replace(_build_job(), created_by_api_key="tok-1")
        items = [
            _build_item(item_id=f"i{index}", input_value="hello")
            for index in range(1, 3 if grouped else 2)
        ]
        model = job.model
        deployment = Deployment(
            deployment_id="native",
            model_name=model,
            deltallm_params={"model": model, "custom_llm_provider": "openai"},
            model_info=info,
        )
        router.deployment = deployment
    else:
        repo = _FailureRepository()
        params = {"model": "gpt-oss", "custom_llm_provider": "openai"}
        if grouped:
            params["provider"] = "vllm"
            params["chat_batching"] = {"mode": "sync_microbatch", "upstream_max_batch_size": 4}
        worker, _ = _build_chat_batch_worker(deployment_params=params, repository=repo)
        job = _build_chat_batch_job()
        items = [
            _build_chat_batch_item(f"chat-{index}", "hello")
            for index in range(1, 3 if grouped else 2)
        ]
        deployment = Deployment(
            deployment_id="native", model_name=job.model, deltallm_params=params, model_info=info
        )
        worker.app.state.router.select_deployment = AsyncMock(return_value=deployment)
        worker.app.state.router.require_deployment = lambda *args, **kwargs: deployment
    worker.app.state.callable_target_grant_service = _AllowAllCallableTargetGrantService()
    job = replace(job, created_by_api_key=hashlib.sha256(b"native-test-key").hexdigest())
    worker.app.state.key_service = SimpleNamespace(
        get_auth_by_token_hash=AsyncMock(
            return_value=UserAPIKeyAuth(
                api_key=job.created_by_api_key,
                user_id=job.created_by_user_id,
                team_id=job.created_by_team_id,
                organization_id=job.created_by_organization_id,
            )
        )
    )
    worker.app.state.budget_service = SimpleNamespace(
        check_budgets=AsyncMock(side_effect=AssertionError("legacy budget check"))
    )
    worker.app.state.spend_tracking_service = SimpleNamespace(
        log_request_failure=AsyncMock(side_effect=AssertionError("legacy spend"))
    )
    worker._execution_engine.native_billing = owner
    worker.config.worker_concurrency = 1
    for item in items:
        item.claim_epoch = 1
    return worker, repo, job, items


def provider_stubs(monkeypatch, owner, worker, *, embedding, grouped, cancel=False):
    calls = []
    started = asyncio.Event()

    async def check():
        calls.append(True)
        assert owner.checkpoints.rows
        assert all(value.terminal is None for value in owner.checkpoints.rows.values())
        if cancel:
            started.set()
            await asyncio.Event().wait()

    async def chat(request, payload, deployment, *, record_usage=True):
        await check()
        assert not record_usage
        return chat_result(), 1.0

    async def embeddings(request, payload, deployment):
        await check()
        count = len(payload.input) if isinstance(payload.input, list) else 1
        return {
            "object": "list",
            "data": [{"index": index, "embedding": [0.1, 0.2]} for index in range(count)],
            "usage": {"prompt_tokens": count * 5, "total_tokens": count * 5},
        }

    async def microbatch(*, requests, deployment, request_context):
        await check()
        return [
            {"index": index, "response_body": chat_result(), "usage": chat_result()["usage"]}
            for index in range(len(requests))
        ]

    monkeypatch.setattr("src.batch.worker.execute_chat", chat)
    monkeypatch.setattr("src.batch.worker._execute_embedding", embeddings)
    if grouped and not embedding:
        worker.app.state.chat_microbatch_executor = SimpleNamespace(
            execute_chat_microbatch=microbatch
        )
    return calls, started


@pytest.mark.parametrize(
    "embedding,grouped", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_native_provider_path_freezes_all_proofs_before_dispatch(
    native_batch, monkeypatch, embedding, grouped
):
    owner, checkpoints, _ = native_batch
    worker, repo, job, items = build_worker(owner, embedding=embedding, grouped=grouped)
    calls, _ = provider_stubs(monkeypatch, owner, worker, embedding=embedding, grouped=grouped)
    await worker._process_items(job, items)
    assert len(calls) == 1
    assert len(repo.completed_calls) == len(items)
    assert len(repo.completion_outbox_calls) == len(items)
    for payload in repo.completion_outbox_calls:
        proof = decode_checkpoint(payload["native_accounting"])
        assert proof.terminal.outcome is AccountingOutcome.COMPLETED
        assert len(proof.operation.attempts) == 1
    assert len(checkpoints.calls) == 1
    assert len(checkpoints.calls[0]) == len(items)
    worker.app.state.budget_service.check_budgets.assert_not_awaited()
    worker.app.state.spend_tracking_service.log_request_failure.assert_not_awaited()


async def test_native_worker_denies_unverified_key_before_funding_or_provider(
    native_batch, monkeypatch
):
    owner, checkpoints, _ = native_batch
    worker, repo, job, items = build_worker(owner, embedding=False, grouped=False)
    worker.app.state.key_service = None
    calls, _ = provider_stubs(monkeypatch, owner, worker, embedding=False, grouped=False)
    await worker._process_items(job, items)
    assert not calls
    assert not checkpoints.calls
    assert len(repo.failed_calls) == 1
    assert repo.failed_calls[0]["error_body"]["code"] == "batch_accounting_checkpoint_unavailable"
    worker.app.state.spend_tracking_service.log_request_failure.assert_not_awaited()


async def test_native_proof_is_absent_from_public_batch_output(native_batch, monkeypatch):
    import json

    owner, _, _ = native_batch
    worker, repo, job, items = build_worker(owner, embedding=False, grouped=False)
    provider_stubs(monkeypatch, owner, worker, embedding=False, grouped=False)
    await worker._process_items(job, items)
    completed = replace(
        items[0],
        status="completed",
        response_body=repo.completed_calls[0]["response_body"],
        usage=repo.completed_calls[0]["usage"],
    )
    repo.list_items = AsyncMock(return_value=[completed])
    rows = [
        json.loads(row)
        async for row in worker._iter_output_lines(job.batch_id, endpoint=job.endpoint)
    ]
    assert len(rows) == 1
    text = json.dumps(rows)
    assert "native_accounting" not in text
    assert "accounting_checkpoint" not in text
    assert "owner_token" not in text
    assert "request_fingerprint" not in text


@pytest.mark.parametrize(
    "embedding,grouped", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_native_provider_cancellation_keeps_uncertain_debit(
    native_batch, monkeypatch, embedding, grouped
):
    owner, checkpoints, _ = native_batch
    worker, repo, job, items = build_worker(owner, embedding=embedding, grouped=grouped)
    calls, started = provider_stubs(
        monkeypatch, owner, worker, embedding=embedding, grouped=grouped, cancel=True
    )
    task = asyncio.create_task(worker._process_items(job, items))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(calls) == 1
    assert not repo.completed_calls
    assert all(
        proof.terminal.outcome is AccountingOutcome.UNCERTAIN for proof in checkpoints.rows.values()
    )
    worker.app.state.spend_tracking_service.log_request_failure.assert_not_awaited()
