from __future__ import annotations

import logging
import re
from functools import lru_cache
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
        if presidio_full_engine_installed():
            self.analyzer = AnalyzerEngine()
            self.anonymizer = AnonymizerEngine()
        else:
            self.analyzer = None
            self.anonymizer = None

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

        modified: list[Any] = []
        changed = False
        for message in messages:
            transformed, entities, did_change = self._inspect_message(message)
            if did_change:
                changed = True
            modified.append(transformed)
            if not entities:
                continue

            if not self.anonymize and self.action == GuardrailAction.BLOCK:
                raise GuardrailViolationError(
                    guardrail_name=self.name,
                    message=f"PII detected: {', '.join(sorted(set(entities)))}",
                    violation_type="pii_detected",
                    status_code=400,
                )

            logger.warning("presidio pii detected", extra={"guardrail": self.name, "entities": entities})

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

    def _inspect_message(self, message: Any) -> tuple[Any, list[str], bool]:
        if self.anonymize:
            return self._scan_and_sanitize(message)
        entities = self._collect_entities(message)
        return message, entities, False

    def _collect_entities(self, value: Any) -> list[str]:
        detected: list[str] = []
        if isinstance(value, str):
            return self._detect(value)
        if isinstance(value, list):
            for item in value:
                detected.extend(self._collect_entities(item))
            return detected
        if isinstance(value, dict):
            for item in value.values():
                detected.extend(self._collect_entities(item))
            return detected
        return detected

    def _scan_and_sanitize(self, value: Any) -> tuple[Any, list[str], bool]:
        if isinstance(value, str):
            entities = self._detect(value)
            if not entities:
                return value, [], False
            sanitized = self._anonymize(value, entities)
            return sanitized, entities, sanitized != value

        if isinstance(value, list):
            changed = False
            detected: list[str] = []
            sanitized_list: list[Any] = []
            for item in value:
                transformed, entities, item_changed = self._scan_and_sanitize(item)
                sanitized_list.append(transformed)
                detected.extend(entities)
                changed = changed or item_changed
            return sanitized_list, detected, changed

        if isinstance(value, dict):
            changed = False
            detected: list[str] = []
            sanitized_dict: dict[str, Any] = {}
            for key, item in value.items():
                transformed, entities, item_changed = self._scan_and_sanitize(item)
                sanitized_dict[key] = transformed
                detected.extend(entities)
                changed = changed or item_changed
            return sanitized_dict, detected, changed

        return value, [], False

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


PRESIDIO_FALLBACK_SUPPORTED_ENTITIES = tuple(PresidioGuardrail._PATTERN_MAP)


@lru_cache(maxsize=1)
def _presidio_engine_ready() -> bool:
    if AnalyzerEngine is None or AnonymizerEngine is None or OperatorConfig is None:
        return False
    try:
        AnalyzerEngine()
        AnonymizerEngine()
    except Exception as exc:  # pragma: no cover - depends on optional local runtime assets
        logger.warning(
            "presidio full engine unavailable; using regex fallback",
            extra={"error_type": type(exc).__name__},
        )
        return False
    return True


def presidio_engine_mode() -> str:
    if _presidio_engine_ready():
        return "full"
    return "regex_fallback"


def presidio_full_engine_installed() -> bool:
    return presidio_engine_mode() == "full"
