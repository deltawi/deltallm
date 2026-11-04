from __future__ import annotations

from src.providers import vllm_discovery


def test_openapi_url_sits_next_to_v1() -> None:
    assert vllm_discovery.openapi_url("http://gpu:8000/v1") == "http://gpu:8000/openapi.json"
    assert vllm_discovery.openapi_url("https://x.example/vllm/v1/") == (
        "https://x.example/vllm/openapi.json"
    )
    assert vllm_discovery.openapi_url("http://gpu:8000") == "http://gpu:8000/openapi.json"


def test_served_modes_reads_registered_routes() -> None:
    openapi = {
        "paths": {
            "/v1/embeddings": {},
            "/v1/rerank": {},
            "/rerank": {},
            "/v1/models": {},
        }
    }
    assert vllm_discovery.served_modes(openapi) == ["embedding", "rerank"]
    assert vllm_discovery.served_modes({"paths": {"/health": {}}}) is None
    assert vllm_discovery.served_modes({"paths": []}) is None
    assert vllm_discovery.served_modes([]) is None


def test_inherit_parent_context_only_fills_missing_windows() -> None:
    items = [
        {"id": "base", "max_model_len": 8192},
        {"id": "adapter", "parent": "base", "max_model_len": None},
        {"id": "orphan", "parent": "missing"},
        {"id": "own", "parent": "base", "max_model_len": 4096},
        "junk",
    ]
    vllm_discovery.inherit_parent_context(items)
    assert items[1]["max_model_len"] == 8192
    assert "max_model_len" not in items[2]
    assert items[3]["max_model_len"] == 4096


def test_model_label_shows_hidden_weights_path() -> None:
    assert vllm_discovery.model_label({"root": "Qwen/Qwen3-8B"}, "qwen") == "qwen (Qwen/Qwen3-8B)"
    assert vllm_discovery.model_label({"root": "Qwen/Qwen3-8B"}, "Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert vllm_discovery.model_label({"root": None}, "qwen") == "qwen"
    assert vllm_discovery.model_label({"root": "x" * 300 + "/y"}, "qwen") == "qwen"
