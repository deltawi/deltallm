from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import load_yaml_config
from src.config_import.__main__ import EXIT_FAILED, EXIT_OK, EXIT_STRICT_SKIPPED, main
from src.config_import.litellm import (
    MAX_INPUT_BYTES,
    ConfigImportError,
    convert_litellm_config,
    parse_litellm_yaml,
)
from src.config_import.litellm_providers import PROVIDER_TARGETS, normalize_api_base
from src.config_import.report import Severity

FIXTURE = Path(__file__).parent / "fixtures" / "litellm_config.yaml"
LITERAL_SECRETS = ("sk-literal-test-secret", "sk-1234")


def _convert(raw: dict[str, Any]):
    return convert_litellm_config(raw)


def _deployment(model: str, **params: Any) -> dict[str, Any]:
    return {"model_name": "m", "litellm_params": {"model": model, **params}}


def _only_deployment(model: str, **params: Any) -> dict[str, Any]:
    result = _convert({"model_list": [_deployment(model, **params)]})
    assert result.report.imported_deployments == 1, result.report.render_text()
    return result.config["model_list"][0]


def _messages(result, severity: Severity) -> list[str]:
    return [f"{item.path}: {item.message}" for item in result.report.by_severity(severity)]


@pytest.fixture
def fixture_result():
    return convert_litellm_config(parse_litellm_yaml(FIXTURE.read_text()))


def test_fixture_converts_supported_deployments_and_skips_the_rest(fixture_result) -> None:
    report = fixture_result.report
    assert report.imported_deployments == 8
    assert report.skipped_deployments == 2
    names = [item["model_name"] for item in fixture_result.config["model_list"]]
    assert "gemini-vertex" not in names and "*" not in names
    skipped = _messages(fixture_result, Severity.SKIPPED)
    assert any("Vertex AI is not supported" in line for line in skipped)
    assert any("wildcard" in line for line in skipped)


def test_fixture_maps_providers_bases_and_prices(fixture_result) -> None:
    by_upstream = {
        item["deltallm_params"]["model"]: item for item in fixture_result.config["model_list"]
    }
    openai = by_upstream["gpt-4o"]
    assert openai["deltallm_params"]["provider"] == "openai"
    assert openai["deployment_id"] == "gpt-4o-primary"
    assert openai["model_info"]["input_cost_per_token_cache_hit"] == 0.00000125
    assert openai["model_info"]["default_params"] == {"temperature": 0.2}

    azure = by_upstream["gpt-4o-eastus"]["deltallm_params"]
    assert azure["provider"] == "azure_openai"
    assert azure["api_base"] == "https://contoso-east.openai.azure.com/openai/v1"
    assert "api_version" not in azure
    assert azure["timeout"] == 31
    assert by_upstream["gpt-4o-eastus"]["model_info"]["priority"] == 2

    claude = by_upstream["claude-sonnet-4-5"]
    assert claude["deltallm_params"]["max_tokens"] == 4096
    assert claude["model_info"] == {"max_input_tokens": 200000}

    bedrock = by_upstream["us.anthropic.claude-sonnet-4-5-v1:0"]["deltallm_params"]
    assert bedrock["provider"] == "bedrock" and bedrock["region"] == "us-west-2"

    ollama = by_upstream["llama3.2"]["deltallm_params"]
    assert ollama == {
        "model": "llama3.2",
        "provider": "ollama",
        "api_base": "http://ollama:11434/v1",
        "api_key": "not-needed",
    }
    assert by_upstream["Qwen/Qwen3-8B"]["deltallm_params"]["api_base"] == "http://vllm:8000/v1"

    grok = by_upstream["grok-4"]["deltallm_params"]
    assert "provider" not in grok and grok["api_base"] == "https://api.x.ai/v1"

    embeddings = by_upstream["text-embedding-3-small"]
    assert embeddings["deltallm_params"]["provider"] == "openai"
    assert embeddings["model_info"]["mode"] == "embedding"


def test_fixture_maps_router_litellm_and_general_settings(fixture_result) -> None:
    config = fixture_result.config
    assert config["router_settings"] == {
        "routing_strategy": "usage-based-routing",
        "num_retries": 2,
        "timeout": 120,
    }
    deltallm = config["deltallm_settings"]
    assert deltallm["fallbacks"] == [{"gpt-4o": ["claude-sonnet", "claude-bedrock"]}]
    assert deltallm["success_callback"] == ["langfuse"]
    assert deltallm["guardrails"][0]["deltallm_params"]["guardrail"] == (
        "src.guardrails.presidio.PresidioGuardrail"
    )
    general = config["general_settings"]
    assert general["cache_enabled"] is True and general["cache_backend"] == "redis"
    assert general["cache_ttl"] == 600
    assert general["redis_host"] == "os.environ/REDIS_HOST"
    assert "redis_url" not in general
    assert general["master_key"] == "os.environ/DELTALLM_MASTER_KEY"
    assert general["database_url"] == "os.environ/DATABASE_URL"
    assert general["db_pool_size"] == 10
    assert general["slack_alerting_enabled"] is True
    assert general["model_deployment_source"] == "db_only"
    assert general["model_deployment_bootstrap_from_config"] is True


def test_fixture_report_flags_actions(fixture_result) -> None:
    actions = "\n".join(_messages(fixture_result, Severity.ACTION))
    assert "NEW, empty PostgreSQL database" in actions
    assert "(grok).litellm_params.api_key: holds a literal secret" in actions
    assert "refers to `gemini-vertex`" in actions
    assert "LANGFUSE_HOST" in actions


def test_report_and_messages_never_contain_secret_values(fixture_result) -> None:
    rendered = fixture_result.report.render_text() + fixture_result.report.render_markdown(
        source_name="config.yaml"
    )
    for secret in LITERAL_SECRETS:
        assert secret not in rendered
    assert "https://cloud.langfuse.com" not in rendered


def test_converted_file_loads_through_the_runtime_config_path(
    fixture_result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("DELTALLM_MASTER_KEY", "DELTALLM_SALT_KEY"):
        monkeypatch.setenv(name, "key1" * 10)
    monkeypatch.setenv("REDIS_HOST", "redis")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/T000")
    path = tmp_path / "config.yaml"
    path.write_text(fixture_result.to_yaml())

    loaded = load_yaml_config(path)

    assert len(loaded.model_list) == 8
    assert loaded.router_settings.routing_strategy == "usage-based-routing"
    assert loaded.deltallm_settings.fallbacks == [{"gpt-4o": ["claude-sonnet", "claude-bedrock"]}]


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("https://r.openai.azure.com", "https://r.openai.azure.com/openai/v1"),
        ("https://r.openai.azure.com/", "https://r.openai.azure.com/openai/v1"),
        ("https://r.openai.azure.com/openai", "https://r.openai.azure.com/openai/v1"),
        ("https://r.openai.azure.com/openai/v1", "https://r.openai.azure.com/openai/v1"),
        (
            "https://r.openai.azure.com/openai/deployments/gpt-4o?api-version=2024-10-21",
            "https://r.openai.azure.com/openai/v1",
        ),
        (
            "https://r.cognitiveservices.azure.com",
            "https://r.cognitiveservices.azure.com/openai/v1",
        ),
        ("os.environ/AZURE_API_BASE", "os.environ/AZURE_API_BASE"),
    ],
)
def test_azure_base_becomes_the_v1_form(api_base: str, expected: str) -> None:
    assert normalize_api_base(PROVIDER_TARGETS["azure"], api_base)[0] == expected


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        (None, "http://localhost:11434/v1"),
        ("http://ollama:11434", "http://ollama:11434/v1"),
        ("http://ollama:11434/v1/", "http://ollama:11434/v1"),
    ],
)
def test_ollama_base_gets_the_openai_path(api_base: str | None, expected: str) -> None:
    assert normalize_api_base(PROVIDER_TARGETS["ollama"], api_base)[0] == expected


def test_missing_key_uses_the_litellm_default_variable() -> None:
    deployment = _only_deployment("groq/llama-3.3-70b-versatile")
    assert deployment["deltallm_params"]["api_key"] == "os.environ/GROQ_API_KEY"
    assert deployment["deltallm_params"]["provider"] == "groq"


def test_missing_azure_base_uses_the_litellm_default_variable() -> None:
    result = _convert({"model_list": [_deployment("azure/gpt-4o", api_key="os.environ/K")]})
    params = result.config["model_list"][0]["deltallm_params"]
    assert params["api_base"] == "os.environ/AZURE_API_BASE"
    assert any("AZURE_API_BASE" in line for line in _messages(result, Severity.ACTION))


def test_self_hosted_server_without_base_is_skipped() -> None:
    result = _convert({"model_list": [_deployment("hosted_vllm/llama")]})
    assert result.report.skipped_deployments == 1
    assert any("needs api_base" in line for line in _messages(result, Severity.SKIPPED))


def test_bedrock_invoke_route_is_reported_and_missing_keys_need_action() -> None:
    result = _convert({"model_list": [_deployment("bedrock/invoke/anthropic.claude-v2")]})
    deployment = result.config["model_list"][0]["deltallm_params"]
    assert deployment["model"] == "anthropic.claude-v2"
    assert "api_key" not in deployment
    assert any("Converse API" in line for line in _messages(result, Severity.CHANGED))
    assert any("has no AWS keys" in line for line in _messages(result, Severity.ACTION))


def test_custom_llm_provider_selects_the_provider() -> None:
    deployment = _only_deployment(
        "llama-3.1-8b", custom_llm_provider="together_ai", api_key="os.environ/T"
    )
    assert deployment["deltallm_params"] == {
        "model": "llama-3.1-8b",
        "provider": "together",
        "api_key": "os.environ/T",
    }


def test_unknown_provider_cannot_be_guessed() -> None:
    result = _convert({"model_list": [_deployment("my-private-model")]})
    assert result.config["model_list"] == []
    assert any("provider cannot be found" in line for line in _messages(result, Severity.SKIPPED))


def test_unsupported_mode_is_skipped_and_completion_becomes_chat() -> None:
    raw = {
        "model_list": [
            {**_deployment("openai/omni-moderation-latest"), "model_info": {"mode": "moderation"}},
            {**_deployment("openai/gpt-3.5-turbo-instruct"), "model_info": {"mode": "completion"}},
        ]
    }
    result = _convert(raw)
    assert result.report.skipped_deployments == 1
    assert result.config["model_list"][0]["model_info"]["mode"] == "chat"


def test_unknown_params_are_reported_not_copied() -> None:
    result = _convert(
        {"model_list": [_deployment("openai/gpt-4o", api_key="os.environ/K", drop_params=True)]}
    )
    assert "drop_params" not in result.config["model_list"][0]["deltallm_params"]
    assert any("litellm_params.drop_params" in line for line in _messages(result, Severity.SKIPPED))


def test_fallback_chains_merge_without_duplicates() -> None:
    raw = {
        "router_settings": {"fallbacks": [{"a": ["b"]}]},
        "litellm_settings": {"fallbacks": [{"a": ["b", "c"]}, {"d": ["a"]}]},
    }
    result = _convert(raw)
    assert result.config["deltallm_settings"]["fallbacks"] == [{"a": ["b", "c"]}, {"d": ["a"]}]


def test_presidio_block_entities_disable_masking() -> None:
    raw = {
        "guardrails": [
            {
                "guardrail_name": "pii",
                "litellm_params": {
                    "guardrail": "presidio",
                    "mode": "during_call",
                    "pii_entities_config": {"US_SSN": "BLOCK", "EMAIL_ADDRESS": "MASK"},
                },
            }
        ]
    }
    result = _convert(raw)
    params = result.config["deltallm_settings"]["guardrails"][0]["deltallm_params"]
    assert params["mode"] == "pre_call"
    assert params["anonymize"] is False
    assert params["entities"] == ["US_SSN", "EMAIL_ADDRESS"]
    changed = _messages(result, Severity.CHANGED)
    assert any("during_call" in line for line in changed)
    assert any("all entities block" in line for line in changed)


def test_unsupported_cache_type_keeps_the_cache_off() -> None:
    result = _convert({"litellm_settings": {"cache": True, "cache_params": {"type": "qdrant"}}})
    assert "cache_enabled" not in result.config["general_settings"]


def test_empty_input_still_produces_a_valid_starter_config() -> None:
    result = _convert({})
    assert result.config["model_list"] == []
    assert result.config["general_settings"]["redis_url"] == "os.environ/REDIS_URL"


def test_parse_rejects_bad_yaml_without_echoing_content() -> None:
    with pytest.raises(ConfigImportError) as excinfo:
        parse_litellm_yaml("model_list:\n  - model_name: x\n   bad: [sk-secret")
    assert "line" in str(excinfo.value)
    assert "sk-secret" not in str(excinfo.value)


def test_parse_rejects_non_mapping_and_oversized_input() -> None:
    with pytest.raises(ConfigImportError):
        parse_litellm_yaml("- a\n- b\n")
    with pytest.raises(ConfigImportError):
        parse_litellm_yaml("#" * (MAX_INPUT_BYTES + 1))


def test_cli_writes_private_output_and_report(tmp_path: Path, capsys) -> None:
    output = tmp_path / "out" / "config.yaml"
    report = tmp_path / "report.md"

    code = main(["litellm", str(FIXTURE), "-o", str(output), "--report", str(report)])

    assert code == EXIT_OK
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert yaml.safe_load(output.read_text())["model_list"]
    assert report.read_text().startswith("# LiteLLM import report")
    assert "Imported 8 deployment(s), skipped 2." in capsys.readouterr().out


def test_cli_refuses_to_replace_files_without_force(tmp_path: Path) -> None:
    output = tmp_path / "config.yaml"
    output.write_text("keep me")

    assert main(["litellm", str(FIXTURE), "-o", str(output)]) == EXIT_FAILED
    assert output.read_text() == "keep me"
    assert main(["litellm", str(FIXTURE), "-o", str(output), "--force"]) == EXIT_OK


def test_cli_refuses_to_overwrite_the_input(tmp_path: Path) -> None:
    source = tmp_path / "litellm.yaml"
    source.write_text(FIXTURE.read_text())
    assert main(["litellm", str(source), "-o", str(source), "--force"]) == EXIT_FAILED
    assert source.read_text() == FIXTURE.read_text()


def test_cli_strict_mode_fails_when_something_was_skipped(tmp_path: Path) -> None:
    code = main(["litellm", str(FIXTURE), "-o", str(tmp_path / "c.yaml"), "--strict"])
    assert code == EXIT_STRICT_SKIPPED


def test_cli_reports_missing_input(tmp_path: Path, capsys) -> None:
    code = main(["litellm", str(tmp_path / "missing.yaml"), "-o", str(tmp_path / "c.yaml")])
    assert code == EXIT_FAILED
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("model", "mode"),
    [
        ("openai/text-embedding-3-large", "embedding"),
        ("ollama/nomic-embed-text", "embedding"),
        ("openai/whisper-1", "audio_transcription"),
        ("openai/gpt-4o-mini-tts", "audio_speech"),
        ("openai/gpt-image-1", "image_generation"),
    ],
)
def test_mode_is_set_from_the_model_name_when_missing(model: str, mode: str) -> None:
    deployment = _only_deployment(model, api_key="os.environ/K")
    assert deployment["model_info"]["mode"] == mode


def test_cost_values_given_as_text_are_not_flagged_as_secrets() -> None:
    result = _convert(
        {
            "model_list": [
                _deployment(
                    "openai/gpt-4o",
                    api_key="os.environ/K",
                    input_cost_per_token="0.0000025",
                    max_tokens=100,
                )
            ]
        }
    )
    assert not any("literal secret" in line for line in _messages(result, Severity.ACTION))
    assert result.config["model_list"][0]["model_info"]["input_cost_per_token"] == "0.0000025"


def test_unexpected_value_types_are_reported_not_raised() -> None:
    raw = {
        "model_list": [
            "not-a-mapping",
            {"model_name": "x", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {1: 2}},
            {"model_name": "y", "litellm_params": "bad"},
        ],
        "router_settings": ["bad"],
        "litellm_settings": {"fallbacks": "bad", "success_callback": 3},
        "general_settings": {
            "database_connection_pool_limit": "os.environ/POOL",
            "alerting": "email",
        },
        "guardrails": {"bad": True},
        42: "unknown section",
    }
    result = _convert(raw)
    assert result.report.imported_deployments == 1
    assert result.report.skipped_deployments == 2
    assert "db_pool_size" not in result.config["general_settings"]
    assert any(item.path == "42" for item in result.report.by_severity(Severity.SKIPPED))
