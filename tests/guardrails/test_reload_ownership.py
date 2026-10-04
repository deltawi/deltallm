import pytest

from src.config import DeltaLLMSettings
from src.guardrails.base import CustomGuardrail, GuardrailMode
from src.guardrails.registry import GuardrailRegistry
from src.request_work_settings import MAX_GUARDRAILS

pytestmark = pytest.mark.hermetic


def config(name):
    return {
        "guardrail_name": name,
        "deltallm_params": {"guardrail": "src.guardrails.base.CustomGuardrail"},
    }


def test_reloads_replace_removed_engines_and_empty_config_preserves_manual_handlers():
    registry = GuardrailRegistry()
    manual = CustomGuardrail(name="manual")
    registry.register(manual)
    for index in range(100):
        name = f"configured-{index}"
        registry.load_from_config([config(name)])
        assert set(registry.get_all_names()) == {"manual", name}
        assert len(registry.get_for_mode(GuardrailMode.PRE_CALL)) == 2
    registry.load_from_config([])
    assert registry.get_all_names() == ["manual"]
    assert registry.get("manual") is manual


def test_failed_reload_does_not_publish_partial_guardrail_policy():
    registry = GuardrailRegistry()
    registry.load_from_config([config("existing")])
    existing = registry.get("existing")
    with pytest.raises(ImportError):
        registry.load_from_config(
            [
                config("new"),
                {
                    "guardrail_name": "invalid",
                    "deltallm_params": {"guardrail": "missing.module.Class"},
                },
            ]
        )
    assert registry.get_all_names() == ["existing"]
    assert registry.get("existing") is existing


def test_guardrail_limit_is_enforced_before_constructing_or_persisting_excess_work():
    registry = GuardrailRegistry()
    items = [config(str(index)) for index in range(MAX_GUARDRAILS + 1)]
    with pytest.raises(ValueError):
        DeltaLLMSettings(guardrails=items)
    with pytest.raises(ValueError, match="At most"):
        registry.load_from_config(items)
    assert registry.get_all_names() == []
    for index in range(MAX_GUARDRAILS):
        registry.register(CustomGuardrail(name=str(index)))
    with pytest.raises(ValueError, match="At most"):
        registry.register(CustomGuardrail(name="too-many"))
    assert len(registry.get_all_names()) == MAX_GUARDRAILS
