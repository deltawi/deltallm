"""Embeddings routes."""

import time
import logging
from typing import Annotated, Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, HTTPException, status, BackgroundTasks

from deltallm.types import EmbeddingRequest
from deltallm.main import embedding
from deltallm.proxy.dependencies import require_auth, AuthContext
from deltallm.proxy.spend_tracking import record_spend_from_endpoint

router = APIRouter(tags=["embeddings"])
logger = logging.getLogger(__name__)


async def _record_embedding_spend(
    request: Request,
    auth_context: AuthContext,
    model: str,
    prompt_tokens: int,
    total_tokens: int,
    provider: Optional[str] = None,
    start_time: Optional[float] = None,
) -> None:
    """Record spend for embedding in background.

    This function runs as a background task to avoid delaying the response.
    """
    logger.info(f"_record_embedding_spend START: model={model}, tokens={prompt_tokens}, provider={provider}")
    try:
        # Calculate latency if start_time provided
        latency_ms = None
        if start_time:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"Calculated latency: {latency_ms:.2f}ms")
        
        # Calculate cost using the pricing manager (which includes DB pricing)
        logger.info(f"Calculating cost for model={model}")
        pricing_manager = request.app.state.pricing_manager
        pricing = pricing_manager.get_pricing(model)
        
        # Embeddings only have input cost (no completion tokens)
        cost_decimal = Decimal(prompt_tokens) * pricing.input_cost_per_token
        
        logger.info(f"Cost calculated: ${float(cost_decimal):.12f}")
        
        # Record spend
        logger.info(f"Calling record_spend_from_endpoint for model={model}")
        await record_spend_from_endpoint(
            request=request,
            auth_context=auth_context,
            model=model,
            endpoint_type="embedding",
            cost=cost_decimal,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            total_tokens=total_tokens,
            provider=provider,
            latency_ms=latency_ms,
        )
        
        logger.info(f"Background spend recorded SUCCESS: model={model}, cost=${float(cost_decimal):.12f}, latency={latency_ms:.2f}ms")
    except Exception as e:
        # Log error but don't fail the request
        logger.exception(f"Failed to record spend in background: {e}")


@router.post("/embeddings")
async def create_embeddings(
    body: EmbeddingRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_context: Annotated[AuthContext, Depends(require_auth)],
):
    """Create embeddings for text.
    
    This endpoint is compatible with OpenAI's embeddings API.
    """
    start_time = time.time()
    key_info = auth_context.key_info
    
    # Check model permissions
    if key_info and key_info.models:
        requested_model = body.model
        allowed = any(
            requested_model == allowed or requested_model.endswith(allowed)
            for allowed in key_info.models
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Model '{requested_model}' not allowed for this key",
            )
    
    try:
        response = await embedding(
            model=body.model,
            input=body.input,
            encoding_format=body.encoding_format,
            dimensions=body.dimensions,
        )
        
        logger.info(
            f"Embedding response received: model={body.model}, "
            f"response_model={response.model}, key_info={key_info is not None}, "
            f"usage={response.usage}")
        
        # Track usage and cost in background to not delay response
        if key_info and response.usage:
            background_tasks.add_task(
                _record_embedding_spend,
                http_request,
                auth_context,
                body.model,  # Use deployment model name for pricing, not provider model
                response.usage.prompt_tokens,
                response.usage.total_tokens,
                getattr(response, 'provider', None),
                start_time,  # Pass start_time for latency calculation
            )
            logger.info(f"Spend recording scheduled in background for model={body.model}, start_time={start_time}")
        else:
            logger.warning(
                f"Skipping spend tracking: key_info={key_info is not None}, "
                f"usage={response.usage is not None}")
        
        return response
    
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
