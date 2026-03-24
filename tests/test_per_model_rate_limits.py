from __future__ import annotations

from src.middleware.rate_limit import _extract_model, _model_limit
from src.models.responses import UserAPIKeyAuth
from src.services.limit_counter import RateLimitCheck


class TestExtractModel:
    def test_extracts_model_from_json(self):
        body = b'{"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}'
        assert _extract_model(body) == "gpt-4"

    def test_extracts_model_from_multipart_form_data(self):
        body = (
            b"------boundary\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\n'
            b"whisper-1\r\n"
            b"------boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
            b"RIFFDATA\r\n"
            b"------boundary--\r\n"
        )
        content_type = "multipart/form-data; boundary=----boundary"
        assert _extract_model(body, content_type) == "whisper-1"

    def test_returns_none_for_multipart_without_model_field(self):
        body = (
            b"------boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
            b"RIFFDATA\r\n"
            b"------boundary--\r\n"
        )
        content_type = "multipart/form-data; boundary=----boundary"
        assert _extract_model(body, content_type) is None

    def test_strips_whitespace(self):
        body = b'{"model": "  claude-3-opus  "}'
        assert _extract_model(body) == "claude-3-opus"

    def test_returns_none_for_missing_model(self):
        body = b'{"messages": [{"role": "user", "content": "hi"}]}'
        assert _extract_model(body) is None

    def test_returns_none_for_empty_model(self):
        body = b'{"model": ""}'
        assert _extract_model(body) is None

    def test_returns_none_for_non_string_model(self):
        body = b'{"model": 42}'
        assert _extract_model(body) is None

    def test_returns_none_for_invalid_json(self):
        body = b"not json at all"
        assert _extract_model(body) is None

    def test_returns_none_for_empty_body(self):
        assert _extract_model(b"") is None

    def test_returns_none_for_array_body(self):
        body = b'[{"model": "gpt-4"}]'
        assert _extract_model(body) is None


class TestModelLimit:
    def test_exact_match(self):
        limits = {"gpt-4": 100, "claude-3-opus": 50}
        assert _model_limit(limits, "gpt-4") == 100
        assert _model_limit(limits, "claude-3-opus") == 50

    def test_no_match_returns_none(self):
        limits = {"gpt-4": 100}
        assert _model_limit(limits, "gpt-3.5-turbo") is None

    def test_wildcard_match(self):
        limits = {"claude-*": 200}
        assert _model_limit(limits, "claude-3-opus") == 200
        assert _model_limit(limits, "claude-3-sonnet") == 200

    def test_exact_takes_precedence_over_wildcard(self):
        limits = {"gpt-4": 100, "gpt-*": 500}
        assert _model_limit(limits, "gpt-4") == 100

    def test_longest_prefix_wildcard_wins(self):
        limits = {"gpt-*": 500, "gpt-4*": 100}
        assert _model_limit(limits, "gpt-4-turbo") == 100
        assert _model_limit(limits, "gpt-3.5-turbo") == 500

    def test_none_limits_returns_none(self):
        assert _model_limit(None, "gpt-4") is None

    def test_none_model_returns_none(self):
        assert _model_limit({"gpt-4": 100}, None) is None

    def test_zero_limit_returns_none(self):
        limits = {"gpt-4": 0}
        assert _model_limit(limits, "gpt-4") is None

    def test_negative_limit_returns_none(self):
        limits = {"gpt-4": -1}
        assert _model_limit(limits, "gpt-4") is None

    def test_invalid_value_returns_none(self):
        limits = {"gpt-4": "abc"}
        assert _model_limit(limits, "gpt-4") is None

    def test_wildcard_star_alone_matches_all(self):
        limits = {"*": 1000}
        assert _model_limit(limits, "anything") == 1000

    def test_empty_limits_dict(self):
        assert _model_limit({}, "gpt-4") is None


class TestUserAPIKeyAuthModelLimits:
    def test_model_limit_fields_default_none(self):
        auth = UserAPIKeyAuth(api_key="test-key")
        assert auth.team_model_rpm_limit is None
        assert auth.team_model_tpm_limit is None
        assert auth.org_model_rpm_limit is None
        assert auth.org_model_tpm_limit is None

    def test_model_limit_fields_set(self):
        auth = UserAPIKeyAuth(
            api_key="test-key",
            team_model_rpm_limit={"gpt-4": 100},
            team_model_tpm_limit={"gpt-4": 50000},
            org_model_rpm_limit={"claude-*": 200},
            org_model_tpm_limit={"claude-*": 100000},
        )
        assert auth.team_model_rpm_limit == {"gpt-4": 100}
        assert auth.org_model_tpm_limit == {"claude-*": 100000}

    def test_model_limit_fields_serialize_roundtrip(self):
        auth = UserAPIKeyAuth(
            api_key="test-key",
            team_model_rpm_limit={"gpt-4": 100, "claude-*": 50},
            org_model_rpm_limit={"gpt-*": 500},
        )
        json_str = auth.model_dump_json()
        restored = UserAPIKeyAuth.model_validate_json(json_str)
        assert restored.team_model_rpm_limit == {"gpt-4": 100, "claude-*": 50}
        assert restored.org_model_rpm_limit == {"gpt-*": 500}
        assert restored.team_model_tpm_limit is None


class TestPerModelRateLimitChecks:
    def test_per_model_checks_generated(self):
        from src.middleware.rate_limit import _model_limit

        team_model_rpm = {"gpt-4": 100}
        org_model_rpm = {"gpt-*": 500}

        model = "gpt-4"
        team_id = "team-1"
        org_id = "org-1"

        checks: list[RateLimitCheck] = []

        team_rpm = _model_limit(team_model_rpm, model)
        org_rpm = _model_limit(org_model_rpm, model)

        if team_rpm is not None and team_id:
            checks.append(RateLimitCheck(
                scope="team_model_rpm",
                entity_id=f"{team_id}:{model}",
                limit=team_rpm,
                amount=1,
            ))
        if org_rpm is not None and org_id:
            checks.append(RateLimitCheck(
                scope="org_model_rpm",
                entity_id=f"{org_id}:{model}",
                limit=org_rpm,
                amount=1,
            ))

        assert len(checks) == 2
        assert checks[0].scope == "team_model_rpm"
        assert checks[0].entity_id == "team-1:gpt-4"
        assert checks[0].limit == 100
        assert checks[1].scope == "org_model_rpm"
        assert checks[1].entity_id == "org-1:gpt-4"
        assert checks[1].limit == 500

    def test_no_checks_when_model_not_in_limits(self):
        from src.middleware.rate_limit import _model_limit

        team_model_rpm = {"gpt-4": 100}
        model = "claude-3-opus"

        team_rpm = _model_limit(team_model_rpm, model)
        assert team_rpm is None

    def test_no_checks_when_no_limits_defined(self):
        from src.middleware.rate_limit import _model_limit

        assert _model_limit(None, "gpt-4") is None
