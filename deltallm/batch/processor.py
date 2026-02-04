"""Batch processor for processing batch jobs.

This module provides the BatchProcessor class which handles:
- Processing batch jobs asynchronously
- Calculating costs with 50% discount
- Generating output and error files
- Recording spend logs
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import BatchJob, FileObject
from deltallm.budget.tracker import BudgetTracker
from deltallm.pricing.calculator import CostCalculator
from deltallm.pricing.manager import PricingManager

logger = logging.getLogger(__name__)


class BatchProcessingError(Exception):
    """Error during batch processing."""
    
    def __init__(self, message: str, batch_id: Optional[str] = None):
        self.message = message
        self.batch_id = batch_id
        super().__init__(message)


class BatchProcessor:
    """Processor for batch jobs.
    
    This class handles the processing of batch jobs including:
    - Reading input files
    - Executing requests
    - Calculating costs with 50% discount
    - Generating output/error files
    - Recording spend logs
    
    Usage:
        processor = BatchProcessor(db_session, pricing_manager)
        await processor.process_batch(batch_id)
    """
    
    def __init__(
        self,
        db_session: AsyncSession,
        pricing_manager: Optional[PricingManager] = None,
    ):
        """Initialize the batch processor.
        
        Args:
            db_session: Database session for accessing batch jobs and files
            pricing_manager: Pricing manager for cost calculations
        """
        self.db = db_session
        self.pricing = pricing_manager or PricingManager(enable_hot_reload=False)
        self.calculator = CostCalculator(self.pricing)
    
    async def process_batch(self, batch_id: str | UUID) -> BatchJob:
        """Process a batch job.
        
        This method processes all requests in a batch job:
        1. Reads the input file
        2. Processes each request
        3. Calculates costs with 50% discount
        4. Generates output and error files
        5. Updates the batch job status
        6. Records spend logs
        
        Args:
            batch_id: ID of the batch job to process
            
        Returns:
            Updated BatchJob object
            
        Raises:
            BatchProcessingError: If processing fails
        """
        # Get batch job
        batch_uuid = UUID(str(batch_id))
        result = await self.db.execute(
            select(BatchJob).where(BatchJob.id == batch_uuid)
        )
        batch = result.scalar_one_or_none()
        
        if not batch:
            raise BatchProcessingError(f"Batch job {batch_id} not found")
        
        if batch.status not in ["validating", "in_progress"]:
            raise BatchProcessingError(
                f"Batch job {batch_id} has status '{batch.status}' and cannot be processed"
            )
        
        # Get input file
        input_file_id = UUID(batch.input_file_id)
        result = await self.db.execute(
            select(FileObject).where(FileObject.id == input_file_id)
        )
        input_file = result.scalar_one_or_none()
        
        if not input_file:
            raise BatchProcessingError(f"Input file {batch.input_file_id} not found")
        
        try:
            # Update status to in_progress
            batch.status = "in_progress"
            await self.db.commit()
            
            # Read input file
            content = input_file.content.decode('utf-8')
            lines = [line for line in content.strip().split('\n') if line.strip()]
            
            # Process requests
            output_lines = []
            error_lines = []
            completed_count = 0
            failed_count = 0
            total_prompt_tokens = 0
            total_completion_tokens = 0
            total_cost = Decimal("0")
            
            for line in lines:
                try:
                    request_data = json.loads(line)
                    result_data = await self._process_single_request(
                        request_data, batch.endpoint
                    )
                    
                    output_lines.append(json.dumps(result_data))
                    completed_count += 1
                    
                    # Track tokens if available
                    if "usage" in result_data.get("response", {}):
                        usage = result_data["response"]["usage"]
                        total_prompt_tokens += usage.get("prompt_tokens", 0)
                        total_completion_tokens += usage.get("completion_tokens", 0)
                    
                except Exception as e:
                    logger.exception(f"Error processing request in batch {batch_id}")
                    error_data = {
                        "custom_id": json.loads(line).get("custom_id", "unknown"),
                        "error": {
                            "message": str(e),
                            "type": type(e).__name__,
                        }
                    }
                    error_lines.append(json.dumps(error_data))
                    failed_count += 1
            
            # Calculate actual cost with 50% discount
            if batch.endpoint == "/v1/chat/completions":
                # Get model from first request
                model = "gpt-4o"  # Default
                try:
                    first_req = json.loads(lines[0])
                    model = first_req.get("body", {}).get("model", "gpt-4o")
                except (json.JSONDecodeError, IndexError):
                    pass
                
                cost_breakdown = self.calculator.calculate_chat_cost(
                    model=model,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    is_batch=True,  # Apply 50% discount
                )
                total_cost = cost_breakdown.total_cost
            
            # Create output file
            output_content = '\n'.join(output_lines).encode('utf-8')
            output_file = FileObject(
                bytes=len(output_content),
                purpose="batch",
                filename=f"batch_{batch_id}_output.jsonl",
                content_type="application/jsonl",
                content=output_content,
                org_id=batch.org_id,
                api_key_id=batch.api_key_id,
            )
            self.db.add(output_file)
            await self.db.flush()  # Flush to get the ID
            
            batch.output_file_id = str(output_file.id)
            
            # Create error file if there are errors
            if error_lines:
                error_content = '\n'.join(error_lines).encode('utf-8')
                error_file = FileObject(
                    bytes=len(error_content),
                    purpose="batch",
                    filename=f"batch_{batch_id}_error.jsonl",
                    content_type="application/jsonl",
                    content=error_content,
                    org_id=batch.org_id,
                    api_key_id=batch.api_key_id,
                )
                self.db.add(error_file)
                await self.db.flush()
                batch.error_file_id = str(error_file.id)
            
            # Update batch job
            batch.status = "completed"
            batch.completed_at = datetime.utcnow()
            batch.completed_requests = completed_count
            batch.failed_requests = failed_count
            batch.prompt_tokens = total_prompt_tokens
            batch.completion_tokens = total_completion_tokens
            batch.total_tokens = total_prompt_tokens + total_completion_tokens
            if batch.original_cost is None:
                batch.original_cost = total_cost * 2  # Reverse calculate original
            batch.discounted_cost = total_cost
            
            await self.db.commit()
            
            # Record spend
            try:
                tracker = BudgetTracker(self.db)
                await tracker.record_spend(
                    request_id=f"batch_{batch_id}",
                    api_key_id=batch.api_key_id,
                    user_id=batch.created_by,
                    team_id=None,  # TODO: Get from API key
                    org_id=batch.org_id,
                    model=model if 'model' in locals() else "unknown",
                    endpoint_type="batch",
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    cost=total_cost,
                    latency_ms=None,
                    status="success",
                    metadata={
                        "batch_id": str(batch_id),
                        "total_requests": len(lines),
                        "completed_requests": completed_count,
                        "failed_requests": failed_count,
                        "discount_percent": 50,
                        "original_cost": str(batch.original_cost) if batch.original_cost else None,
                    },
                )
            except Exception as e:
                logger.exception(f"Failed to record spend for batch {batch_id}: {e}")
            
            logger.info(
                f"Batch job completed: id={batch_id}, "
                f"completed={completed_count}, failed={failed_count}, "
                f"cost={total_cost}"
            )
            
            return batch
            
        except Exception as e:
            # Update batch status to failed
            batch.status = "failed"
            batch.error_message = str(e)
            await self.db.commit()
            
            logger.exception(f"Batch job failed: id={batch_id}")
            raise BatchProcessingError(f"Batch processing failed: {e}", str(batch_id))
    
    async def _process_single_request(
        self,
        request_data: dict[str, Any],
        endpoint: str,
    ) -> dict[str, Any]:
        """Process a single request from a batch.
        
        Args:
            request_data: The request data from the input file
            endpoint: The endpoint to use
            
        Returns:
            Result data for the output file
        """
        custom_id = request_data.get("custom_id", "unknown")
        method = request_data.get("method", "POST")
        url = request_data.get("url", endpoint)
        body = request_data.get("body", {})
        
        # For now, return a placeholder response
        # In production, this would call the actual endpoint
        # TODO: Integrate with actual router/completion logic
        
        if endpoint == "/v1/chat/completions":
            model = body.get("model", "gpt-4o")
            messages = body.get("messages", [])
            
            # Placeholder response
            response_body = {
                "id": f"batch-resp-{custom_id}",
                "object": "chat.completion",
                "created": int(datetime.utcnow().timestamp()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"[Batch response for: {messages[-1].get('content', '')[:50] if messages else 'N/A'}...]",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": sum(len(m.get("content", "")) // 4 for m in messages),
                    "completion_tokens": 10,
                    "total_tokens": sum(len(m.get("content", "")) // 4 for m in messages) + 10,
                },
            }
            
            return {
                "id": response_body["id"],
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "request_id": response_body["id"],
                    "body": response_body,
                },
                "error": None,
            }
        
        elif endpoint == "/v1/embeddings":
            model = body.get("model", "text-embedding-3-small")
            input_text = body.get("input", "")
            
            # Placeholder response
            response_body = {
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "embedding": [0.0] * 1536,  # Placeholder embedding
                        "index": 0,
                    }
                ],
                "model": model,
                "usage": {
                    "prompt_tokens": len(input_text) // 4,
                    "total_tokens": len(input_text) // 4,
                },
            }
            
            return {
                "id": f"batch-emb-{custom_id}",
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "request_id": f"batch-emb-{custom_id}",
                    "body": response_body,
                },
                "error": None,
            }
        
        else:
            raise ValueError(f"Unsupported endpoint: {endpoint}")
    
    async def cancel_batch(self, batch_id: str | UUID) -> BatchJob:
        """Cancel a batch job.
        
        Args:
            batch_id: ID of the batch job to cancel
            
        Returns:
            Updated BatchJob object
            
        Raises:
            BatchProcessingError: If cancellation fails
        """
        batch_uuid = UUID(str(batch_id))
        result = await self.db.execute(
            select(BatchJob).where(BatchJob.id == batch_uuid)
        )
        batch = result.scalar_one_or_none()
        
        if not batch:
            raise BatchProcessingError(f"Batch job {batch_id} not found")
        
        if batch.status not in ["validating", "in_progress"]:
            raise BatchProcessingError(
                f"Cannot cancel batch with status '{batch.status}'"
            )
        
        batch.status = "cancelled"
        batch.completed_at = datetime.utcnow()
        await self.db.commit()
        
        logger.info(f"Batch job cancelled: id={batch_id}")
        
        return batch
