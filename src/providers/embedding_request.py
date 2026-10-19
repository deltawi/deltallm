from __future__ import annotations

from collections.abc import Mapping

from src.models.errors import InvalidRequestError
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider, resolve_upstream_model


def translate_embedding_request(
    request: EmbeddingRequest, provider_config: Mapping[str, object]
) -> dict[str, object]:
    payload = request.model_dump(exclude_none=True)
    upstream_model = resolve_upstream_model(provider_config)
    if upstream_model:
        payload["model"] = upstream_model
    if resolve_provider(provider_config) == "mistral":
        if isinstance(request.input, list) and any(
            not isinstance(item, str) for item in request.input
        ):
            raise InvalidRequestError(message="Mistral embeddings require text input")
        if request.user is not None:
            raise InvalidRequestError(message="Parameter 'user' is not supported")
        dimensions = payload.pop("dimensions", None)
        if dimensions is not None:
            payload["output_dimension"] = dimensions
    return payload
