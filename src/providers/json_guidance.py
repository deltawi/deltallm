from __future__ import annotations

from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest


def validate_guided_json_support(request: ChatCompletionRequest, *, provider: str) -> None:
    """Reject constraints that the selected adapter cannot preserve."""
    if request.guided_json is not None and provider != "vllm":
        raise InvalidRequestError(
            message="Parameter 'guided_json' is supported only by the vllm provider",
            param="guided_json",
        )
