"""Batch API routes.

This module provides endpoints for batch job management:
- Create batch jobs
- Get batch status
- List batch jobs
- Cancel batch jobs
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.session import get_db_session
from deltallm.db.models import BatchJob, FileObject
from deltallm.proxy.dependencies import require_auth, AuthContext
from deltallm.proxy.spend_tracking import SpendTrackingContext
from deltallm.pricing.calculator import CostCalculator
from deltallm.proxy.schemas_batch import (
    BatchCancelResponse,
    BatchCostInfo,
    BatchListResponse,
    BatchRequestCounts,
    BatchResponse,
    CreateBatchRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["batches"])


def _to_unix_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """Convert datetime to Unix timestamp."""
    if dt is None:
        return None
    return int(dt.timestamp())


def _batch_to_response(batch: BatchJob) -> BatchResponse:
    """Convert BatchJob model to BatchResponse schema."""
    # Build cost info if available
    cost_info = None
    if batch.original_cost is not None and batch.discounted_cost is not None:
        cost_info = BatchCostInfo(
            original_cost=float(batch.original_cost),
            discounted_cost=float(batch.discounted_cost),
            discount_amount=float(batch.original_cost - batch.discounted_cost),
            currency="USD",
        )
    
    # Build request counts if available
    request_counts = None
    if batch.total_requests is not None:
        request_counts = BatchRequestCounts(
            total=batch.total_requests,
            completed=batch.completed_requests or 0,
            failed=batch.failed_requests or 0,
        )
    
    return BatchResponse(
        id=str(batch.id),
        object="batch",
        endpoint=batch.endpoint,
        errors={"data": [], "object": "list"} if batch.status != "failed" else None,
        input_file_id=batch.input_file_id,
        completion_window=batch.completion_window,
        status=batch.status,
        output_file_id=batch.output_file_id,
        error_file_id=batch.error_file_id,
        created_at=_to_unix_timestamp(batch.created_at),
        in_progress_at=_to_unix_timestamp(batch.created_at) if batch.status in ["in_progress", "finalizing", "completed"] else None,
        expires_at=_to_unix_timestamp(batch.expires_at),
        finalizing_at=_to_unix_timestamp(batch.completed_at) if batch.status == "finalizing" else None,
        completed_at=_to_unix_timestamp(batch.completed_at) if batch.status == "completed" else None,
        failed_at=_to_unix_timestamp(batch.completed_at) if batch.status == "failed" else None,
        expired_at=_to_unix_timestamp(batch.expires_at) if batch.status == "expired" else None,
        cancelling_at=None,
        cancelled_at=None,
        request_counts=request_counts,
        metadata=batch.batch_metadata if batch.batch_metadata else None,
        cost_info=cost_info,
    )


@router.post(
    "/batches",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Batch job created successfully"},
        400: {"description": "Bad request - invalid input file or parameters"},
        401: {"description": "Authentication required"},
        404: {"description": "Input file not found"},
    },
)
async def create_batch(
    request: Request,
    body: CreateBatchRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    auth_context: Annotated[AuthContext, Depends(require_auth)],
) -> BatchResponse:
    """Create a batch job.
    
    Batch jobs process multiple API requests asynchronously with 50% discount.
    
    **Input File Format (JSONL):**
    Each line must be a JSON object with:
    - `custom_id`: Unique identifier for the request
    - `method`: HTTP method (POST)
    - `url`: Endpoint URL (/v1/chat/completions)
    - `body`: Request body parameters
    
    **Example Input Line:**
    ```json
    {
        "custom_id": "request-1",
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello!"}]
        }
    }
    ```
    
    **Example Request:**
    ```bash
    curl -X POST http://localhost:8000/v1/batches \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d '{
            "input_file_id": "file-abc123",
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h"
        }'
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Verify input file exists and belongs to the org/api_key
    try:
        file_uuid = UUID(body.input_file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid input_file_id format",
        )
    
    result = await db.execute(
        select(FileObject).where(FileObject.id == file_uuid)
    )
    input_file = result.scalar_one_or_none()
    
    if not input_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Input file not found",
        )
    
    # Check access to file
    if key_info:
        if input_file.org_id and input_file.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Input file not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not input_file.org_id and api_key_id and input_file.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Input file not found",
            )
    
    # Validate file purpose
    if input_file.purpose != "batch":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File purpose must be 'batch', got '{input_file.purpose}'",
        )
    
    # Count requests in file and estimate cost
    try:
        content = input_file.content.decode('utf-8')
        lines = [line for line in content.strip().split('\n') if line.strip()]
        total_requests = len(lines)
        
        if total_requests == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Input file is empty",
            )
        
        # Parse requests to estimate tokens (rough estimation)
        estimated_prompt_tokens = 0
        for line in lines:
            try:
                req = json.loads(line)
                body = req.get("body", {})
                messages = body.get("messages", [])
                # Rough estimate: 4 chars per token
                for msg in messages:
                    content_text = msg.get("content", "")
                    estimated_prompt_tokens += len(content_text) // 4
            except (json.JSONDecodeError, KeyError):
                pass
        
        # Rough estimate for completion tokens
        estimated_completion_tokens = estimated_prompt_tokens // 2
        
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Input file is not valid UTF-8",
        )
    
    # Calculate estimated cost with batch discount
    try:
        from deltallm.pricing.manager import PricingManager
        pricing_manager = PricingManager(config_path="config/pricing.yaml", enable_hot_reload=False)
        calculator = CostCalculator(pricing_manager)
        
        # Get model from first request (assume all use same model)
        model = "gpt-4o"  # Default
        try:
            first_req = json.loads(lines[0])
            model = first_req.get("body", {}).get("model", "gpt-4o")
        except (json.JSONDecodeError, IndexError):
            pass
        
        cost_breakdown = calculator.calculate_chat_cost(
            model=model,
            prompt_tokens=estimated_prompt_tokens,
            completion_tokens=estimated_completion_tokens,
            is_batch=True,  # Apply 50% discount
        )
        
        original_cost = float(cost_breakdown.original_cost)
        discounted_cost = float(cost_breakdown.total_cost)
        
    except Exception as e:
        logger.warning(f"Could not calculate estimated cost: {e}")
        original_cost = None
        discounted_cost = None
    
    try:
        # Create batch job
        expires_at = datetime.utcnow() + timedelta(hours=24)
        
        batch = BatchJob(
            status="validating",
            endpoint=body.endpoint,
            completion_window=body.completion_window,
            input_file_id=body.input_file_id,
            original_cost=Decimal(str(original_cost)) if original_cost else None,
            discounted_cost=Decimal(str(discounted_cost)) if discounted_cost else None,
            total_requests=total_requests,
            completed_requests=0,
            failed_requests=0,
            org_id=key_info.org_id if key_info else None,
            api_key_id=api_key_id,
            batch_metadata=body.metadata or {},
            expires_at=expires_at,
        )
        
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        
        logger.info(
            f"Batch job created: id={batch.id}, endpoint={body.endpoint}, "
            f"requests={total_requests}, estimated_cost={discounted_cost}"
        )
        
        return _batch_to_response(batch)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating batch job")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating batch job: {str(e)}",
        )


@router.get(
    "/batches/{batch_id}",
    response_model=BatchResponse,
    responses={
        200: {"description": "Batch job information"},
        401: {"description": "Authentication required"},
        404: {"description": "Batch job not found"},
    },
)
async def retrieve_batch(
    batch_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> BatchResponse:
    """Retrieve a batch job.
    
    **Example Request:**
    ```bash
    curl -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/batches/batch-abc123"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid batch ID format",
        )
    
    result = await db.execute(
        select(BatchJob).where(BatchJob.id == batch_uuid)
    )
    batch = result.scalar_one_or_none()
    
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch job not found",
        )
    
    # Check access
    if key_info:
        if batch.org_id and batch.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch job not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not batch.org_id and api_key_id and batch.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch job not found",
            )
    
    return _batch_to_response(batch)


@router.get(
    "/batches",
    response_model=BatchListResponse,
    responses={
        200: {"description": "List of batch jobs"},
        401: {"description": "Authentication required"},
    },
)
async def list_batches(
    after: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(default=20, ge=1, le=100, description="Number of batches to return"),
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> BatchListResponse:
    """List batch jobs.
    
    **Parameters:**
    - `limit`: Number of batches to return (1-100, default 20)
    - `after`: Pagination cursor (batch ID)
    
    **Example Request:**
    ```bash
    curl -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/batches?limit=10"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Build query
    query = select(BatchJob)
    
    # Filter by org/api_key
    if key_info and key_info.org_id:
        query = query.where(BatchJob.org_id == key_info.org_id)
    elif api_key_id:
        query = query.where(BatchJob.api_key_id == api_key_id)
    
    # Pagination cursor
    if after:
        try:
            after_uuid = UUID(after)
            query = query.where(BatchJob.id > after_uuid)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'after' cursor",
            )
    
    # Order by creation time descending
    query = query.order_by(desc(BatchJob.created_at))
    
    # Limit
    query = query.limit(limit + 1)  # +1 to check if there are more results
    
    result = await db.execute(query)
    batches = result.scalars().all()
    
    # Check if there are more results
    has_more = len(batches) > limit
    if has_more:
        batches = batches[:limit]
    
    # Build response
    batch_responses = [_batch_to_response(b) for b in batches]
    
    return BatchListResponse(
        object="list",
        data=batch_responses,
        has_more=has_more,
        first_id=str(batches[0].id) if batches else None,
        last_id=str(batches[-1].id) if batches else None,
    )


@router.post(
    "/batches/{batch_id}/cancel",
    response_model=BatchCancelResponse,
    responses={
        200: {"description": "Batch job cancelled"},
        400: {"description": "Batch job cannot be cancelled"},
        401: {"description": "Authentication required"},
        404: {"description": "Batch job not found"},
    },
)
async def cancel_batch(
    batch_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> BatchCancelResponse:
    """Cancel a batch job.
    
    Only batch jobs with status `validating` or `in_progress` can be cancelled.
    
    **Example Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/batches/batch-abc123/cancel"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid batch ID format",
        )
    
    result = await db.execute(
        select(BatchJob).where(BatchJob.id == batch_uuid)
    )
    batch = result.scalar_one_or_none()
    
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch job not found",
        )
    
    # Check access
    if key_info:
        if batch.org_id and batch.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch job not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not batch.org_id and api_key_id and batch.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch job not found",
            )
    
    # Check if batch can be cancelled
    cancellable_statuses = ["validating", "in_progress"]
    if batch.status not in cancellable_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel batch with status '{batch.status}'. "
                   f"Only batches with status {cancellable_statuses} can be cancelled.",
        )
    
    # Update batch status
    batch.status = "cancelled"
    batch.completed_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(batch)
    
    logger.info(f"Batch job cancelled: id={batch_id}")
    
    return _batch_to_response(batch)
