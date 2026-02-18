from __future__ import annotations

import pytest

from src.guardrails.base import CustomGuardrail, GuardrailAction, GuardrailMode
from src.guardrails.exceptions import GuardrailViolationError
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry


class EchoGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        del user_api_key_dict, cache, call_type
        next_data = dict(data)
        next_data["metadata"] = {**(data.get("metadata") or {}), "guarded": True}
        return next_data


class BlockingGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        del user_api_key_dict, cache, data, call_type
        raise GuardrailViolationError(self.name, "blocked", "policy")


@pytest.mark.asyncio
async def test_registry_default_and_key_specific_resolution():
    registry = GuardrailRegistry()
    default_guardrail = EchoGuardrail(name="default", default_on=True)
    key_guardrail = EchoGuardrail(name="key", default_on=False)
    registry.register(default_guardrail)
    registry.register(key_guardrail)

    selected_default = registry.get_for_key({})
    assert [item.name for item in selected_default] == ["default"]

    selected_key = registry.get_for_key({"guardrails": ["key"]})
    assert [item.name for item in selected_key] == ["key"]


@pytest.mark.asyncio
async def test_middleware_runs_pre_call_modification():
    registry = GuardrailRegistry()
    registry.register(EchoGuardrail(name="echo", default_on=True))
    middleware = GuardrailMiddleware(registry=registry)

    modified = await middleware.run_pre_call(
        request_data={"messages": [{"role": "user", "content": "hello"}]},
        user_api_key_dict={},
        call_type="completion",
    )

    assert modified["metadata"]["guarded"] is True


@pytest.mark.asyncio
async def test_middleware_blocks_on_violation_in_block_mode():
    registry = GuardrailRegistry()
    registry.register(BlockingGuardrail(name="block", action=GuardrailAction.BLOCK, default_on=True))
    middleware = GuardrailMiddleware(registry=registry)

    with pytest.raises(GuardrailViolationError):
        await middleware.run_pre_call(
            request_data={"messages": [{"role": "user", "content": "hello"}]},
            user_api_key_dict={},
            call_type="completion",
        )


def test_registry_loads_from_config():
    registry = GuardrailRegistry()
    registry.load_from_config(
        [
            {
                "guardrail_name": "echo-from-config",
                "litellm_params": {
                    "guardrail": "tests.guardrails.test_framework.EchoGuardrail",
                    "mode": "pre_call",
                    "default_on": True,
                    "default_action": "log",
                },
            }
        ]
    )

    loaded = registry.get("echo-from-config")
    assert loaded is not None
    assert loaded.mode == GuardrailMode.PRE_CALL
    assert loaded.action == GuardrailAction.LOG
