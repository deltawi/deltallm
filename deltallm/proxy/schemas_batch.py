"""Pydantic schemas for Batch API and File API.

This module defines request/response schemas for:
- File upload, retrieval, and deletion
- Batch job creation and management
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ========== File Schemas ==========

class FileUploadResponse(BaseModel):
    """Response for file upload."""
    id: str = Field(..., description="Unique file identifier")
    object: str = Field(default="file", description="Object type")
    bytes: int = Field(..., description="Size of the file in bytes")
    created_at: int = Field(..., description="Unix timestamp of creation")
    filename: Optional[str] = Field(None, description="Original filename")
    purpose: str = Field(..., description="Purpose of the file (batch, fine-tune, etc.)")
    status: Optional[str] = Field(None, description="Processing status")
    status_details: Optional[str] = Field(None, description="Status details")


class FileInfoResponse(BaseModel):
    """Response for file information."""
    id: str = Field(..., description="Unique file identifier")
    object: str = Field(default="file", description="Object type")
    bytes: int = Field(..., description="Size of the file in bytes")
    created_at: int = Field(..., description="Unix timestamp of creation")
    filename: Optional[str] = Field(None, description="Original filename")
    purpose: str = Field(..., description="Purpose of the file")


class FileListResponse(BaseModel):
    """Response for listing files."""
    object: str = Field(default="list", description="Object type")
    data: list[FileInfoResponse] = Field(default_factory=list, description="List of files")
    has_more: bool = Field(default=False, description="Whether there are more results")


class FileDeleteResponse(BaseModel):
    """Response for file deletion."""
    id: str = Field(..., description="File ID")
    object: str = Field(default="file", description="Object type")
    deleted: bool = Field(..., description="Whether the file was deleted")


# ========== Batch Request/Response Schemas ==========

class CreateBatchRequest(BaseModel):
    """Request to create a batch job.
    
    Each line in the input file must be a JSON object with:
    - custom_id: Unique identifier for the request
    - method: HTTP method (POST)
    - url: Endpoint URL (/v1/chat/completions)
    - body: Request body parameters
    """
    input_file_id: str = Field(
        ...,
        description="ID of the input file (JSONL format)",
        min_length=1
    )
    endpoint: str = Field(
        ...,
        description="Endpoint to use for batch requests",
        pattern="^/v1/(chat/completions|embeddings)$"
    )
    completion_window: str = Field(
        default="24h",
        description="Time window for completion",
        pattern="^24h$"  # Currently only 24h is supported
    )
    metadata: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional metadata (max 16 key-value pairs, keys max 64 chars, values max 512 chars)"
    )
    
    @field_validator('metadata')
    @classmethod
    def validate_metadata(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        """Validate metadata constraints."""
        if v is None:
            return v
        
        if len(v) > 16:
            raise ValueError("Metadata can have at most 16 key-value pairs")
        
        for key, value in v.items():
            if len(key) > 64:
                raise ValueError(f"Metadata key '{key}' exceeds 64 characters")
            if len(value) > 512:
                raise ValueError(f"Metadata value for key '{key}' exceeds 512 characters")
        
        return v


class BatchRequestCounts(BaseModel):
    """Counts of requests in a batch job."""
    total: int = Field(..., description="Total number of requests")
    completed: int = Field(..., description="Number of completed requests")
    failed: int = Field(..., description="Number of failed requests")


class BatchCostInfo(BaseModel):
    """Cost information for a batch job."""
    original_cost: float = Field(..., description="Cost before discount")
    discounted_cost: float = Field(..., description="Cost after 50% discount")
    discount_amount: float = Field(..., description="Amount saved from discount")
    currency: str = Field(default="USD", description="Currency")


class BatchResponse(BaseModel):
    """Response for batch job information."""
    id: str = Field(..., description="Unique batch identifier")
    object: str = Field(default="batch", description="Object type")
    endpoint: str = Field(..., description="Endpoint used for batch requests")
    errors: Optional[dict] = Field(None, description="Errors that occurred during processing")
    input_file_id: str = Field(..., description="ID of the input file")
    completion_window: str = Field(..., description="Time window for completion")
    status: str = Field(
        ...,
        description="Current status: validating, in_progress, finalizing, completed, failed, cancelled, expired"
    )
    output_file_id: Optional[str] = Field(None, description="ID of the output file")
    error_file_id: Optional[str] = Field(None, description="ID of the error file")
    created_at: int = Field(..., description="Unix timestamp of creation")
    in_progress_at: Optional[int] = Field(None, description="Unix timestamp when processing started")
    expires_at: Optional[int] = Field(None, description="Unix timestamp when the batch expires")
    finalizing_at: Optional[int] = Field(None, description="Unix timestamp when finalization started")
    completed_at: Optional[int] = Field(None, description="Unix timestamp when completed")
    failed_at: Optional[int] = Field(None, description="Unix timestamp when failed")
    expired_at: Optional[int] = Field(None, description="Unix timestamp when expired")
    cancelling_at: Optional[int] = Field(None, description="Unix timestamp when cancellation started")
    cancelled_at: Optional[int] = Field(None, description="Unix timestamp when cancelled")
    request_counts: Optional[BatchRequestCounts] = Field(None, description="Request counts")
    metadata: Optional[dict[str, str]] = Field(None, description="Metadata")
    cost_info: Optional[BatchCostInfo] = Field(None, description="Cost information")


class BatchListResponse(BaseModel):
    """Response for listing batch jobs."""
    object: str = Field(default="list", description="Object type")
    data: list[BatchResponse] = Field(default_factory=list, description="List of batch jobs")
    has_more: bool = Field(default=False, description="Whether there are more results")
    first_id: Optional[str] = Field(None, description="ID of the first batch in the list")
    last_id: Optional[str] = Field(None, description="ID of the last batch in the list")


class BatchCancelResponse(BatchResponse):
    """Response for batch cancellation (same as BatchResponse)."""
    pass


# ========== Batch Input/Output Line Schemas ==========

class BatchRequestBody(BaseModel):
    """Body of a batch request line."""
    model: str = Field(..., description="Model to use")
    messages: list[dict] = Field(..., description="Messages for chat completion")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")
    temperature: Optional[float] = Field(None, ge=0, le=2, description="Sampling temperature")
    top_p: Optional[float] = Field(None, ge=0, le=1, description="Nucleus sampling parameter")
    
    # Additional fields for flexibility
    extra_body: Optional[dict[str, Any]] = Field(None, description="Additional request parameters")


class BatchInputLine(BaseModel):
    """Single line in the batch input file (JSONL format)."""
    custom_id: str = Field(..., description="Unique identifier for the request")
    method: str = Field(default="POST", description="HTTP method")
    url: str = Field(..., description="Endpoint URL")
    body: dict[str, Any] = Field(..., description="Request body")


class BatchOutputLine(BaseModel):
    """Single line in the batch output file (JSONL format)."""
    id: str = Field(..., description="Response ID")
    custom_id: str = Field(..., description="Custom ID from the request")
    response: dict[str, Any] = Field(..., description="Response body")
    error: Optional[dict] = Field(None, description="Error information if failed")


class BatchErrorLine(BaseModel):
    """Single line in the batch error file (JSONL format)."""
    custom_id: str = Field(..., description="Custom ID from the request")
    error: dict[str, Any] = Field(..., description="Error details")


# ========== Internal Schemas ==========

class BatchProcessingStatus(BaseModel):
    """Internal status for batch processing."""
    batch_id: str
    status: str
    total_requests: int
    processed_requests: int
    failed_requests: int
    current_cost: float
    estimated_total_cost: float
