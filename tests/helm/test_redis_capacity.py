from __future__ import annotations

from dataclasses import fields

import pytest

from src.redis_runtime import RedisLimits
from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error
from tests.helm.test_tier_policy_settings import _config_maps, _general_settings

pytestmark = pytest.mark.helm


@pytest.mark.parametrize("values", ["values.yaml", "values-eval.yaml", "values-production.yaml"])
def test_redis_allocations_render_in_all_profiles(values):
    configs = [
        c
        for c in _config_maps(_render("-f", str(HELM_CHART_DIR / values)))
        if "config.yaml" in c.get("data", {})
    ]
    assert configs
    for config in configs:
        settings = _general_settings(config)
        for field in fields(RedisLimits()):
            assert settings["redis_" + field.name] == getattr(RedisLimits(), field.name)


@pytest.mark.parametrize("field", ["redis_" + f.name for f in fields(RedisLimits())])
def test_invalid_redis_values_fail_helm(field):
    invalid = -1 if field.endswith("_max_waiters") else 0
    assert field in _render_error("--set-json", f"config.general_settings.{field}={invalid}")
