from __future__ import annotations

import logging
from typing import Any

from src.guardrails.base import GuardrailAction, GuardrailMode
from src.guardrails.exceptions import GuardrailViolationError
from src.guardrails.registry import GuardrailRegistry

logger = logging.getLogger(__name__)


class GuardrailMiddleware:
    def __init__(self, registry: GuardrailRegistry, cache_backend: Any | None = None) -> None:
        self.registry = registry
        self.cache = cache_backend

    async def run_pre_call(
        self,
        request_data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        call_type: str,
        override_guardrails: list[str] | None = None,
    ) -> dict[str, Any]:
        guardrails = self.registry.get_for_key(user_api_key_dict, override_guardrails=override_guardrails)
        pre_call = [item for item in guardrails if item.mode == GuardrailMode.PRE_CALL]

        modified = request_data
        for guardrail in pre_call:
            try:
                result = await guardrail.async_pre_call_hook(
                    user_api_key_dict=user_api_key_dict,
                    cache=self.cache,
                    data=modified,
                    call_type=call_type,
                )
                if result is not None:
                    modified = result
            except GuardrailViolationError:
                if guardrail.action == GuardrailAction.LOG:
                    logger.warning("guardrail violation logged", extra={"guardrail": guardrail.name})
                    continue
                raise

        return modified

    async def run_post_call_success(
        self,
        request_data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        response_data: dict[str, Any],
        call_type: str,
        override_guardrails: list[str] | None = None,
    ) -> None:
        del call_type
        guardrails = self.registry.get_for_key(user_api_key_dict, override_guardrails=override_guardrails)
        post_call = [item for item in guardrails if item.mode == GuardrailMode.POST_CALL]
        for guardrail in post_call:
            try:
                await guardrail.async_post_call_success_hook(
                    data=request_data,
                    user_api_key_dict=user_api_key_dict,
                    response=response_data,
                )
            except GuardrailViolationError:
                if guardrail.action == GuardrailAction.LOG:
                    logger.warning("guardrail violation logged", extra={"guardrail": guardrail.name})
                    continue
                raise

    async def run_post_call_failure(
        self,
        request_data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        original_exception: Exception,
        call_type: str,
        override_guardrails: list[str] | None = None,
    ) -> None:
        del call_type
        guardrails = self.registry.get_for_key(user_api_key_dict, override_guardrails=override_guardrails)
        for guardrail in guardrails:
            await guardrail.async_post_call_failure_hook(
                request_data=request_data,
                original_exception=original_exception,
                user_api_key_dict=user_api_key_dict,
            )
