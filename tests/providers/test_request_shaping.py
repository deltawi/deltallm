from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from src.config import AppConfig, DeltaLLMParams, GeneralSettings
from src.providers.request_shaping import (
    MAX_HEADER_VALUE_BYTES,
    RequestShapingError,
    apply_body_shaping,
    parse_request_shaping,
    shaped_headers,
    upstream_session_id,
)


def test_no_shaping_fields_parse_to_none():
    assert parse_request_shaping({"model": "gpt-4o", "api_key": "sk"}) is None
    assert parse_request_shaping({"override_params": {}, "drop_params": []}) is None


def test_overrides_win_merge_objects_and_drops_run_last():
    shaping = parse_request_shaping(
        {
            "override_params": {
                "reasoning_effort": "high",
                "chat_template_kwargs": {"enable_thinking": False},
            },
            "drop_params": ["logit_bias", "chat_template_kwargs.extra"],
        }
    )
    payload = {
        "model": "m",
        "reasoning_effort": "low",
        "logit_bias": {"1": 2},
        "chat_template_kwargs": {"extra": 1, "keep": 2},
    }
    apply_body_shaping(payload, shaping)
    assert payload == {
        "model": "m",
        "reasoning_effort": "high",
        "chat_template_kwargs": {"keep": 2, "enable_thinking": False},
    }


def test_override_values_are_not_shared_between_requests():
    shaping = parse_request_shaping({"override_params": {"extra": {"tags": ["a"]}}})
    first: dict = {}
    apply_body_shaping(first, shaping)
    first["extra"]["tags"].append("b")
    second: dict = {}
    apply_body_shaping(second, shaping)
    assert second == {"extra": {"tags": ["a"]}}


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"override_params": {"max_tokens": 10}}, "admission and billing"),
        ({"drop_params": ["stream_options"]}, "admission and billing"),
        ({"drop_params": ["n"]}, "admission and billing"),
        ({"override_params": {"generationConfig": {"maxOutputTokens": 9}}}, "admission and billing"),
        ({"override_params": {"inferenceConfig": {"maxTokens": 9}}}, "admission and billing"),
        ({"drop_params": ["generationConfig.maxOutputTokens"]}, "admission and billing"),
        ({"override_params": {"a.b": 1}}, "top-level"),
        ({"override_params": {"top_k": 1}, "drop_params": ["top_k"]}, "both"),
        ({"override_params": {"x": float("nan")}}, "JSON"),
        ({"drop_params": ["bad name"]}, "invalid parameter"),
        ({"forward_headers": ["Authorization"]}, "never sent"),
        ({"forward_headers": ["x-api-key"]}, "never sent"),
        ({"forward_headers": ["X-Goog-Api-Key"]}, "never sent"),
        ({"forward_headers": ["Cookie"]}, "never sent"),
        ({"forward_headers": ["x-session-token"]}, "never sent"),
        ({"forward_headers": ["X-Forwarded-For"]}, "never sent"),
        ({"forward_headers": ["x-amz-date"]}, "never sent"),
        ({"forward_headers": ["Host"]}, "never sent"),
        ({"forward_headers": ["x-deltallm-session-id"]}, "never sent"),
        ({"extra_headers": {"Proxy-Authorization": "x"}}, "never sent"),
        ({"extra_headers": {"X-Project": "a\r\nInjected: 1"}}, "control characters"),
        ({"extra_headers": {"X-Project": "a" * (MAX_HEADER_VALUE_BYTES + 1)}}, "bytes"),
        ({"session_header": "authorization"}, "never sent"),
        ({"session_header": "x-s", "extra_headers": {"X-S": "1"}}, "set header"),
        ({"outbound_proxy": "Corp Proxy"}, "configured proxy"),
        ({"forward_headers": [f"x-h{i}" for i in range(17)]}, "at most"),
    ],
)
def test_invalid_shaping_is_rejected(params, message):
    with pytest.raises(RequestShapingError, match=message):
        parse_request_shaping(params)


def test_headers_forward_set_and_never_replace_provider_headers():
    shaping = parse_request_shaping(
        {
            "forward_headers": ["X-OpenCode-Project", "x-trace"],
            "extra_headers": {"X-Project": "acme", "content-language": "en"},
        }
    )
    added = shaped_headers(
        shaping,
        client_headers={"x-opencode-project": "p1", "authorization": "Bearer client"},
        session_id=lambda: None,
        reserved={"Content-Language": "provider", "Authorization": "Bearer provider"},
    )
    assert added == {"x-opencode-project": "p1", "X-Project": "acme"}


def test_forwarded_client_value_with_control_characters_is_skipped():
    shaping = parse_request_shaping({"forward_headers": ["x-trace"]})
    added = shaped_headers(
        shaping,
        client_headers={"x-trace": "a\nb"},
        session_id=lambda: None,
        reserved={},
    )
    assert added == {}


def test_session_header_prefers_client_then_gateway_then_random():
    shaping = parse_request_shaping({"session_header": "x-opencode-session"})
    calls: list[str] = []

    def gateway() -> str | None:
        calls.append("called")
        return "gateway-id"

    client = shaped_headers(
        shaping, client_headers={"x-opencode-session": "s1"}, session_id=gateway, reserved={}
    )
    assert client == {"x-opencode-session": "s1"}
    assert calls == []
    derived = shaped_headers(shaping, client_headers={}, session_id=gateway, reserved={})
    assert derived == {"x-opencode-session": "gateway-id"}
    random = shaped_headers(shaping, client_headers={}, session_id=lambda: None, reserved={})
    assert uuid.UUID(random["x-opencode-session"]).version == 4


def test_upstream_session_id_is_stable_and_one_way():
    first = upstream_session_id("digest-a")
    assert first == upstream_session_id("digest-a")
    assert first != upstream_session_id("digest-b")
    assert "digest" not in first
    assert uuid.UUID(first).version == 4


def test_config_file_validates_shaping_fields():
    params = DeltaLLMParams(
        model="kimi-k2",
        session_header="x-opencode-session",
        drop_params=["logit_bias"],
    )
    assert params.session_header == "x-opencode-session"
    with pytest.raises(ValidationError):
        DeltaLLMParams(model="m", forward_headers=["authorization"])


def test_upstream_proxies_validate_scheme_name_and_metadata_hosts():
    settings = GeneralSettings(
        upstream_proxies={"corp": "http://user:pass@proxy.internal:3128", "eu": "socks5://10.0.0.5:1080"}
    )
    assert settings.upstream_proxies["corp"].get_secret_value().startswith("http://user:pass@")
    assert "pass" not in repr(settings.upstream_proxies)
    for bad in (
        {"corp": "ftp://proxy:21"},
        {"Corp": "http://proxy:3128"},
        {"corp": "http://169.254.169.254:80"},
        {"corp": "http://metadata.google.internal"},
        {"corp": "not a url"},
    ):
        with pytest.raises(ValidationError) as caught:
            GeneralSettings(upstream_proxies=bad)
        assert "user:pass" not in str(caught.value)


def test_model_must_name_a_configured_proxy():
    entry = {"model_name": "m", "deltallm_params": {"model": "m", "outbound_proxy": "corp"}}
    with pytest.raises(ValidationError, match="does not define"):
        AppConfig(model_list=[entry])
    config = AppConfig(
        model_list=[entry], general_settings={"upstream_proxies": {"corp": "http://proxy:3128"}}
    )
    assert config.model_list[0].deltallm_params.outbound_proxy == "corp"
