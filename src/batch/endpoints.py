from __future__ import annotations

BATCH_ENDPOINT_EMBEDDINGS = "/v1/embeddings"
BATCH_ENDPOINT_CHAT_COMPLETIONS = "/v1/chat/completions"

SUPPORTED_BATCH_ENDPOINTS: tuple[str, ...] = (
    BATCH_ENDPOINT_EMBEDDINGS,
    BATCH_ENDPOINT_CHAT_COMPLETIONS,
)
SUPPORTED_BATCH_ENDPOINT_SET = frozenset(SUPPORTED_BATCH_ENDPOINTS)

BATCH_CALL_TYPE_BY_ENDPOINT = {
    BATCH_ENDPOINT_EMBEDDINGS: "embedding_batch",
    BATCH_ENDPOINT_CHAT_COMPLETIONS: "chat_batch",
}

BATCH_ROUTER_USAGE_MODE_BY_ENDPOINT = {
    BATCH_ENDPOINT_EMBEDDINGS: "embedding",
    BATCH_ENDPOINT_CHAT_COMPLETIONS: "chat",
}


def supported_batch_endpoints_display() -> str:
    return ", ".join(SUPPORTED_BATCH_ENDPOINTS)


def batch_call_type_for_endpoint(endpoint: str) -> str:
    return BATCH_CALL_TYPE_BY_ENDPOINT.get(endpoint, "batch")


def router_usage_mode_for_batch_endpoint(endpoint: str) -> str:
    return BATCH_ROUTER_USAGE_MODE_BY_ENDPOINT.get(endpoint, "batch")
