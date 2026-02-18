from __future__ import annotations

import logging
from typing import Any

import httpx

from src.guardrails.base import CustomGuardrail, GuardrailAction, GuardrailMode
from src.guardrails.exceptions import GuardrailViolationError

logger = logging.getLogger(__name__)


class LakeraGuardrail(CustomGuardrail):
    LAKERA_API_URL = "https://api.lakera.ai/v1/prompt_injection"

    def __init__(
        self,
        name: str = "lakera",
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK,
        api_key: str | None = None,
        threshold: float = 0.5,
        fail_open: bool = False,
        timeout: float = 10.0,
        api_url: str | None = None,
    ) -> None:
        super().__init__(name=name, mode=mode, default_on=default_on, action=action)
        self.api_key = api_key
        self.threshold = threshold
        self.fail_open = fail_open
        self.timeout = timeout
        self.api_url = api_url or self.LAKERA_API_URL

    async def async_pre_call_hook(
        self,
        user_api_key_dict: dict[str, Any],
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any] | None:
        del user_api_key_dict, cache, call_type

        text_to_check = self._extract_text(data.get("messages", []))
        if not text_to_check:
            return None

        if not self.api_key:
            logger.warning("lakera api key not configured; skipping check", extra={"guardrail": self.name})
            return None

        payload = {"input": text_to_check}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            score = self._extract_score(result)

            if score >= self.threshold:
                message = f"Prompt injection detected (score: {score:.2f})"
                if self.action == GuardrailAction.BLOCK:
                    raise GuardrailViolationError(
                        guardrail_name=self.name,
                        message=message,
                        violation_type="prompt_injection",
                        status_code=400,
                    )
                logger.warning("lakera prompt injection detected", extra={"guardrail": self.name, "score": score})
        except GuardrailViolationError:
            raise
        except Exception as exc:
            logger.error("lakera api error", extra={"guardrail": self.name, "error": str(exc)})
            if not self.fail_open:
                raise GuardrailViolationError(
                    guardrail_name=self.name,
                    message="Guardrail check failed",
                    violation_type="guardrail_error",
                    status_code=503,
                ) from exc

        return None

    @staticmethod
    def _extract_text(messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        text_chunks: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                text_chunks.append(content)
        return "\n".join(text_chunks)

    @staticmethod
    def _extract_score(response_data: Any) -> float:
        if not isinstance(response_data, dict):
            return 0.0

        candidates = [
            response_data.get("score"),
            response_data.get("prompt_injection_score"),
            (response_data.get("results") or [{}])[0].get("categories", {}).get("prompt_injection")
            if isinstance(response_data.get("results"), list)
            else None,
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
        return 0.0
