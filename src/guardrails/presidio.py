from __future__ import annotations

import logging
import re
from typing import Any

from src.guardrails.base import CustomGuardrail, GuardrailAction, GuardrailMode
from src.guardrails.exceptions import GuardrailViolationError

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
except Exception:  # pragma: no cover - optional dependency
    AnalyzerEngine = None
    AnonymizerEngine = None
    OperatorConfig = None


class PresidioGuardrail(CustomGuardrail):
    SUPPORTED_ENTITIES = [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "US_SSN",
        "US_PASSPORT",
        "IBAN",
        "IP_ADDRESS",
        "LOCATION",
    ]

    _PATTERN_MAP: dict[str, re.Pattern[str]] = {
        "EMAIL_ADDRESS": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "PHONE_NUMBER": re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
        "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    }

    def __init__(
        self,
        name: str = "presidio",
        mode: GuardrailMode = GuardrailMode.PRE_CALL,
        default_on: bool = True,
        action: GuardrailAction = GuardrailAction.BLOCK,
        anonymize: bool = True,
        entities: list[str] | None = None,
        language: str = "en",
        threshold: float = 0.5,
    ) -> None:
        super().__init__(name=name, mode=mode, default_on=default_on, action=action)
        self.anonymize = anonymize
        self.entities = entities or self.SUPPORTED_ENTITIES
        self.language = language
        self.threshold = threshold
        self.analyzer = AnalyzerEngine() if AnalyzerEngine is not None else None
        self.anonymizer = AnonymizerEngine() if AnonymizerEngine is not None else None

    async def async_pre_call_hook(
        self,
        user_api_key_dict: dict[str, Any],
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any] | None:
        del user_api_key_dict, cache, call_type
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            return None

        modified = []
        changed = False
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                modified.append(message)
                continue

            entities = self._detect(content)
            if not entities:
                modified.append(message)
                continue

            if self.anonymize:
                sanitized = self._anonymize(content, entities)
                if sanitized != content:
                    changed = True
                    new_message = dict(message)
                    new_message["content"] = sanitized
                    modified.append(new_message)
                else:
                    modified.append(message)
                continue

            if self.action == GuardrailAction.BLOCK:
                raise GuardrailViolationError(
                    guardrail_name=self.name,
                    message=f"PII detected: {', '.join(sorted(set(entities)))}",
                    violation_type="pii_detected",
                    status_code=400,
                )

            logger.warning("presidio pii detected", extra={"guardrail": self.name, "entities": entities})
            modified.append(message)

        if changed:
            next_data = dict(data)
            next_data["messages"] = modified
            return next_data
        return None

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        del data, user_api_key_dict
        content = (
            response.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(response, dict)
            else ""
        )
        if not isinstance(content, str) or not content:
            return

        entities = self._detect(content)
        if entities and self.action == GuardrailAction.BLOCK:
            raise GuardrailViolationError(
                guardrail_name=self.name,
                message=f"PII detected in output: {', '.join(sorted(set(entities)))}",
                violation_type="pii_in_output",
                status_code=400,
            )

        if entities:
            logger.warning("presidio output pii detected", extra={"guardrail": self.name, "entities": entities})

    def _detect(self, text: str) -> list[str]:
        if self.analyzer is not None:
            results = self.analyzer.analyze(text=text, entities=self.entities, language=self.language)
            return [item.entity_type for item in results if float(getattr(item, "score", 0)) >= self.threshold]

        detected: list[str] = []
        for entity in self.entities:
            pattern = self._PATTERN_MAP.get(entity)
            if pattern is not None and pattern.search(text):
                detected.append(entity)
        return detected

    def _anonymize(self, text: str, entities: list[str]) -> str:
        if self.anonymizer is not None and self.analyzer is not None and OperatorConfig is not None:
            results = self.analyzer.analyze(text=text, entities=self.entities, language=self.language)
            filtered = [item for item in results if float(getattr(item, "score", 0)) >= self.threshold]
            if not filtered:
                return text
            operators = {
                entity: OperatorConfig("replace", {"new_value": f"<{entity}>"}) for entity in self.entities
            }
            anonymized = self.anonymizer.anonymize(text=text, analyzer_results=filtered, operators=operators)
            return anonymized.text

        masked = text
        for entity in entities:
            pattern = self._PATTERN_MAP.get(entity)
            if pattern is not None:
                masked = pattern.sub(f"<{entity}>", masked)
        return masked
