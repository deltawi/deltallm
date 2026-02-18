from __future__ import annotations

import hashlib
import json
from typing import Any

from src.models.requests import ChatCompletionRequest

DEFAULT_CACHE_KEY_FIELDS = {
    "model",
    "messages",
    "temperature",
    "top_p",
    "max_tokens",
    "n",
    "stop",
    "tools",
    "tool_choice",
    "response_format",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
    "user",
    "seed",
    "input",
    "encoding_format",
    "dimensions",
}


class CacheKeyBuilder:
    def __init__(self, fields: set[str] | None = None, custom_salt: str = "") -> None:
        self.fields = fields or set(DEFAULT_CACHE_KEY_FIELDS)
        self.salt = custom_salt

    def build_key(self, request: ChatCompletionRequest) -> str:
        return self.build_key_from_payload(request.model_dump(exclude_none=True))

    def build_key_from_payload(self, request_data: dict[str, Any], custom_key: str | None = None) -> str:
        if custom_key:
            return f"custom:{custom_key}"

        components = {field: request_data[field] for field in self.fields if field in request_data}
        normalized = self._normalize(components)
        as_string = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        if self.salt:
            as_string = f"{self.salt}:{as_string}"
        return hashlib.sha256(as_string.encode("utf-8")).hexdigest()

    def _normalize(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: self._normalize(v) for k, v in sorted(data.items())}
        if isinstance(data, list):
            return [self._normalize(item) for item in data]
        if isinstance(data, float):
            return round(data, 6)
        return data
