from __future__ import annotations

from types import SimpleNamespace

from src.batch.chat_worker_execution import ChatWorkerExecutionMixin
from src.batch.embedding_worker_execution import EmbeddingWorkerExecutionMixin
from src.batch.worker_types import (
    capture_batch_routing_runtime,
    routing_generation_batch_key,
)


def test_legacy_batch_runtime_identity_is_stable_until_aliases_change() -> None:
    router = object()
    failover = object()
    state = SimpleNamespace(router=router, failover_manager=failover)

    first = capture_batch_routing_runtime(state)
    second = capture_batch_routing_runtime(state)

    assert routing_generation_batch_key(first) == routing_generation_batch_key(second)

    state.failover_manager = object()
    replacement = capture_batch_routing_runtime(state)

    assert routing_generation_batch_key(replacement) != routing_generation_batch_key(first)


def test_microbatch_keys_isolate_runtime_generations_with_the_same_revision() -> None:
    first_runtime = SimpleNamespace(generation_id="generation-a", revision=7)
    second_runtime = SimpleNamespace(generation_id="generation-b", revision=7)
    deployment = SimpleNamespace(deployment_id="deployment-a")
    first_chat = SimpleNamespace(
        routing_generation=first_runtime,
        primary_deployment=deployment,
    )
    second_chat = SimpleNamespace(
        routing_generation=second_runtime,
        primary_deployment=deployment,
    )
    first_embedding = SimpleNamespace(
        routing_generation=first_runtime,
        execution_signature=("embedding", "deployment-a"),
        failover_kwargs={"fallbacks": ["backup"]},
    )
    second_embedding = SimpleNamespace(
        routing_generation=second_runtime,
        execution_signature=("embedding", "deployment-a"),
        failover_kwargs={"fallbacks": ["backup"]},
    )

    assert ChatWorkerExecutionMixin._chat_deployment_key(
        first_chat
    ) != ChatWorkerExecutionMixin._chat_deployment_key(second_chat)
    embedding_worker = object.__new__(EmbeddingWorkerExecutionMixin)
    assert embedding_worker._microbatch_group_key(
        first_embedding
    ) != embedding_worker._microbatch_group_key(second_embedding)
