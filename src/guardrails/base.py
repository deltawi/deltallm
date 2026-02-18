from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Any


class GuardrailMode(str, Enum):
    PRE_CALL = "pre_call"
    POST_CALL = "post_call"
    DURING_CALL = "during_call"


class GuardrailAction(str, Enum):
    BLOCK = "block"
    LOG = "log"


@dataclass
class GuardrailResult:
    passed: bool
    action: GuardrailAction
    violation_type: str | None = None
    message: str | None = None
    modified_data: dict[str, Any] | None = None


class CustomGuardrail(ABC):
    def __init__(
        self,
        name: str,
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK,
    ) -> None:
        self.name = name
        self.mode = mode
        self.default_on = default_on
        self.action = action

    async def async_pre_call_hook(
        self,
        user_api_key_dict: dict[str, Any],
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any] | None:
        return None

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        return None

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: dict[str, Any],
    ) -> None:
        return None

    async def async_moderation_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        call_type: str,
    ) -> GuardrailResult:
        del data, user_api_key_dict, call_type
        return GuardrailResult(passed=True, action=self.action)
