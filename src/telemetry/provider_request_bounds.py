"""Map final edge models to billing facts without retaining request content."""

from src.billing.provider_allowance import ProviderRequestBounds
from src.models.requests import (
    AudioSpeechRequest,
    ChatCompletionRequest,
    EmbeddingRequest,
    ImageGenerationRequest,
    RerankRequest,
)

ValidatedProviderRequest = (
    AudioSpeechRequest
    | ChatCompletionRequest
    | EmbeddingRequest
    | ImageGenerationRequest
    | RerankRequest
)


def validated_provider_request_bounds(payload: ValidatedProviderRequest) -> ProviderRequestBounds:
    if isinstance(payload, ChatCompletionRequest):
        return ProviderRequestBounds(
            output_items=payload.n or 1, max_output_tokens=payload.max_tokens
        )
    if isinstance(payload, ImageGenerationRequest):
        return ProviderRequestBounds(output_items=payload.n or 1)
    if isinstance(payload, AudioSpeechRequest):
        return ProviderRequestBounds(input_characters=len(payload.input))
    if isinstance(payload, RerankRequest):
        return ProviderRequestBounds(input_items=len(payload.documents))
    if isinstance(payload, EmbeddingRequest):
        inputs = payload.input
        # A flat integer list is one tokenized input, not one input per token.
        count = len(inputs) if isinstance(inputs, list) and not isinstance(inputs[0], int) else 1
        return ProviderRequestBounds(input_items=count)
    raise TypeError("provider bounds require a validated request model")
