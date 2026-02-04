"""Chat completion routes."""

import time
import logging
import asyncio
from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, Request, HTTPException, status, BackgroundTasks
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from deltallm.types import CompletionRequest, Message
from deltallm.types.common import ModelType
from deltallm.router import Router
from deltallm.dynamic_router import DynamicRouter
from decimal import Decimal
from deltallm.proxy.dependencies import require_auth, AuthContext
from deltallm.proxy.spend_tracking import record_spend_from_endpoint

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def get_router(request: Request) -> Router:
    """Get the static router from app state."""
    return request.app.state.router


def get_dynamic_router(request: Request) -> DynamicRouter:
    """Get the dynamic router from app state."""
    return request.app.state.dynamic_router


async def _record_chat_spend(
    request: Request,
    auth_context: AuthContext,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    provider: Optional[str] = None,
    start_time: Optional[float] = None,
) -> None:
    """Record spend for chat completion in background.
    
    This function runs as a background task to avoid delaying the response.
    """
    logger.info(f"_record_chat_spend START: model={model}, tokens={prompt_tokens}/{completion_tokens}, provider={provider}")
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
        
        input_cost = Decimal(prompt_tokens) * pricing.input_cost_per_token
        output_cost = Decimal(completion_tokens) * pricing.output_cost_per_token
        cost_decimal = input_cost + output_cost
        
        logger.info(f"Cost calculated: ${float(cost_decimal):.12f} (input=${float(input_cost):.12f}, output=${float(output_cost):.12f})")
        
        # Record spend
        logger.info(f"Calling record_spend_from_endpoint for model={model}")
        await record_spend_from_endpoint(
            request=request,
            auth_context=auth_context,
            model=model,
            endpoint_type="chat",
            cost=cost_decimal,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            provider=provider,
            latency_ms=latency_ms,
        )
        
        logger.info(f"Background spend recorded SUCCESS: model={model}, cost=${float(cost_decimal):.12f}, latency={latency_ms:.2f}ms")
    except Exception as e:
        # Log error but don't fail the request
        logger.exception(f"Failed to record spend in background: {e}")


@router.post("/chat/completions")
async def chat_completions(
    body: CompletionRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_context: Annotated[AuthContext, Depends(require_auth)],
    static_router: Router = Depends(get_router),
    dynamic_router: DynamicRouter = Depends(get_dynamic_router),
):
    """Create a chat completion.

    This endpoint is compatible with OpenAI's chat completions API.
    Requires a valid API key (master, generated, or session token).

    The router selection priority:
    1. DynamicRouter (database-backed deployments) - if deployments exist
    2. Static Router (config.yaml-based) - fallback
    """
    start_time = time.time()
    key_info = auth_context.key_info

    # Check model permissions
    if key_info and key_info.models:
        requested_model = body.model
        # Check if model matches any allowed pattern
        allowed = any(
            requested_model == allowed_model or requested_model.endswith(allowed_model)
            for allowed_model in key_info.models
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Model '{requested_model}' not allowed for this key",
            )

    # Check budget
    if key_info and key_info.max_budget is not None and key_info.spend >= key_info.max_budget:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Budget exceeded",
        )

    # Determine organization context for routing
    org_id = None
    if auth_context.org_id:
        org_id = auth_context.org_id

    # Validate model type - chat endpoint requires chat models
    try:
        model_info = await dynamic_router.get_deployment_info(body.model, org_id=org_id)
        if model_info and model_info.model_type != ModelType.CHAT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{body.model}' is type '{model_info.model_type}', expected 'chat'. Use the appropriate endpoint for this model type.",
            )
    except HTTPException:
        raise
    except Exception:
        # If we can't get deployment info (e.g., using static router), skip validation
        pass

    # Helper to execute completion with the appropriate router
    async def execute_completion(use_dynamic: bool):
        if use_dynamic:
            return await dynamic_router.completion(
                model=body.model,
                messages=body.messages,
                stream=body.stream,
                temperature=body.temperature,
                top_p=body.top_p,
                max_tokens=body.max_tokens or body.max_completion_tokens,
                stop=body.stop,
                tools=body.tools,
                tool_choice=body.tool_choice,
                response_format=body.response_format,
                org_id=org_id,
            )
        else:
            return await static_router.completion(
                model=body.model,
                messages=body.messages,
                stream=body.stream,
                temperature=body.temperature,
                top_p=body.top_p,
                max_tokens=body.max_tokens or body.max_completion_tokens,
                stop=body.stop,
                tools=body.tools,
                tool_choice=body.tool_choice,
                response_format=body.response_format,
            )

    try:
        # Check if dynamic router has deployments for this model
        use_dynamic = await dynamic_router.has_deployments()

        if body.stream:
            # Streaming response - collect usage and record in background
            # Use a queue to capture stream chunks for spend tracking
            stream_usage_data = {"captured": False, "tokens": None}
            
            async def generate():
                final_chunk = None
                try:
                    async for chunk in await execute_completion(use_dynamic):
                        final_chunk = chunk
                        yield {"data": chunk.model_dump_json(exclude_none=True)}

                    # Send [DONE]
                    yield {"data": "[DONE]"}
                    
                    # Capture usage data for background tracking
                    if key_info and final_chunk and final_chunk.usage:
                        stream_usage_data["captured"] = True
                        stream_usage_data["tokens"] = {
                            "prompt_tokens": final_chunk.usage.prompt_tokens,
                            "completion_tokens": final_chunk.usage.completion_tokens,
                            "total_tokens": final_chunk.usage.total_tokens,
                        }
                        stream_usage_data["provider"] = getattr(final_chunk, 'provider', None)
                        
                except Exception as e:
                    logger.exception(f"Streaming error: {e}")
                    raise
            
            # After streaming completes, schedule spend recording
            async def record_stream_spend():
                # Wait a bit for the stream to finish processing
                await asyncio.sleep(0.1)
                
                if stream_usage_data["captured"]:
                    await _record_chat_spend(
                        http_request,
                        auth_context,
                        body.model,  # Use deployment model name for pricing
                        stream_usage_data["tokens"]["prompt_tokens"],
                        stream_usage_data["tokens"]["completion_tokens"],
                        stream_usage_data["tokens"]["total_tokens"],
                        stream_usage_data.get("provider"),
                        start_time,  # Pass start_time for latency calculation
                    )
            
            # Schedule background task for stream spend tracking
            background_tasks.add_task(record_stream_spend)

            return EventSourceResponse(
                generate(),
                media_type="text/event-stream",
            )

        else:
            # Non-streaming response
            response = await execute_completion(use_dynamic)

            logger.info(
                f"Chat response received: model={body.model}, "
                f"response_model={response.model}, key_info={key_info is not None}, "
                f"usage={response.usage}")

            # Track usage and cost in background to not delay response
            if key_info and response.usage:
                background_tasks.add_task(
                    _record_chat_spend,
                    http_request,
                    auth_context,
                    body.model,  # Use deployment model name for pricing, not provider model
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
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
        # Log error
        # Re-raise as HTTP exception
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/completions")
async def completions(
    request: Request,
    router: Router = Depends(get_router),
):
    """Create a completion (legacy endpoint).
    
    Note: This endpoint is for backward compatibility.
    New code should use /chat/completions.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Legacy completions endpoint not implemented. Use /v1/chat/completions instead.",
    )
