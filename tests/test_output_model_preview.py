from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_ui_tier_policy_preview_api import (
    _TierPreviewRepository,
    _headers,
    _install_preview_services,
)


@pytest.mark.parametrize(
    "mode",
    ["chat", "embedding", "rerank", "image_generation", "audio_speech", "audio_transcription"],
)
async def test_output_projection_uses_inferred_deployment_mode(client, test_app, mode):
    repository = _TierPreviewRepository()
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(repository.policy_inputs.model_policies[0], output_tpm_limit=10),),
    )
    await _install_preview_services(test_app, repository=repository)
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(deployment, model_info={"mode": mode})
    ]
    test_app.state.prisma_manager = SimpleNamespace(
        client=SimpleNamespace(
            query_raw=AsyncMock(return_value=[{"output_tpm_limit": 100}]),
        )
    )
    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "completion_tokens": 0 if mode in {"embedding", "rerank"} else 10,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["request"]["billing_mode"] == mode
    projection = response.json()["output_limit_projection"]
    if mode == "chat":
        assert [item["scope"] for item in projection] == ["org_output_tpm", "org_model_output_tpm"]
        assert [item["next_call_blocked"] for item in projection] == [False, True]
    else:
        assert projection == []
