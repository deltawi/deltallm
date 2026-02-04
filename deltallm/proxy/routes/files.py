"""File API routes.

This module provides endpoints for file management:
- Upload files for batch processing
- List, retrieve, and delete files
"""

import logging
from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.session import get_db_session
from deltallm.db.models import FileObject
from deltallm.proxy.dependencies import require_auth, AuthContext
from deltallm.proxy.schemas_batch import (
    FileDeleteResponse,
    FileInfoResponse,
    FileListResponse,
    FileUploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


# Maximum file size: 100MB (OpenAI's limit)
MAX_FILE_SIZE = 100 * 1024 * 1024

# Allowed purposes for files
ALLOWED_PURPOSES = ["batch", "fine-tune", "fine-tune-results", "assistants", "assistants_output"]


def _to_unix_timestamp(dt: datetime) -> int:
    """Convert datetime to Unix timestamp."""
    return int(dt.timestamp())


@router.post(
    "/files",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "File uploaded successfully"},
        400: {"description": "Bad request - invalid file or purpose"},
        401: {"description": "Authentication required"},
        413: {"description": "File too large"},
    },
)
async def upload_file(
    file: Annotated[UploadFile, File(...)],
    purpose: Annotated[str, Form(...)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    auth_context: Annotated[AuthContext, Depends(require_auth)],
) -> FileUploadResponse:
    """Upload a file for batch processing.
    
    **Supported Purposes:**
    - `batch`: For batch API processing (JSONL format required)
    - `fine-tune`: For fine-tuning models
    - `fine-tune-results`: For fine-tuning results
    - `assistants`: For assistants
    - `assistants_output`: For assistant outputs
    
    **File Requirements:**
    - Maximum size: 100MB
    - For batch: Must be JSONL format
    - Each line must be a valid JSON object
    
    **Example Request:**
    ```bash
    curl -X POST http://localhost:8000/v1/files \
        -H "Authorization: Bearer $API_KEY" \
        -F "file=@batch_requests.jsonl" \
        -F "purpose=batch"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Validate purpose
    if purpose not in ALLOWED_PURPOSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose. Allowed: {', '.join(ALLOWED_PURPOSES)}",
        )
    
    # Validate file
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided",
        )
    
    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        logger.exception("Error reading file")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading file: {str(e)}",
        )
    
    # Check file size
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file provided",
        )
    
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )
    
    # Validate JSONL format for batch purpose
    if purpose == "batch":
        try:
            import json
            lines = content.decode('utf-8').strip().split('\n')
            for i, line in enumerate(lines, 1):
                if line.strip():  # Skip empty lines
                    json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSONL format at line {i}: {str(e)}",
            )
    
    try:
        # Create file object
        file_obj = FileObject(
            bytes=len(content),
            purpose=purpose,
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
            content=content,
            org_id=key_info.org_id if key_info else None,
            api_key_id=api_key_id,
        )
        
        db.add(file_obj)
        await db.commit()
        await db.refresh(file_obj)
        
        logger.info(
            f"File uploaded: id={file_obj.id}, filename={file.filename}, "
            f"bytes={len(content)}, purpose={purpose}"
        )
        
        return FileUploadResponse(
            id=str(file_obj.id),
            object="file",
            bytes=len(content),
            created_at=_to_unix_timestamp(file_obj.created_at),
            filename=file.filename,
            purpose=purpose,
            status="processed",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error uploading file")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading file: {str(e)}",
        )


@router.get(
    "/files",
    response_model=FileListResponse,
    responses={
        200: {"description": "List of files"},
        401: {"description": "Authentication required"},
    },
)
async def list_files(
    purpose: Optional[str] = Query(None, description="Filter by purpose"),
    limit: int = Query(default=20, ge=1, le=100, description="Number of files to return"),
    after: Optional[str] = Query(None, description="Cursor for pagination"),
    order: str = Query(default="desc", pattern="^(asc|desc)$", description="Sort order"),
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> FileListResponse:
    """List files uploaded by the organization.
    
    **Parameters:**
    - `purpose`: Filter by file purpose (batch, fine-tune, etc.)
    - `limit`: Number of files to return (1-100, default 20)
    - `after`: Pagination cursor (file ID)
    - `order`: Sort order by creation time (asc or desc)
    
    **Example Request:**
    ```bash
    curl -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/files?purpose=batch&limit=10"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Build query
    query = select(FileObject)
    
    # Filter by org/api_key
    if key_info and key_info.org_id:
        query = query.where(FileObject.org_id == key_info.org_id)
    elif api_key_id:
        query = query.where(FileObject.api_key_id == api_key_id)
    
    # Filter by purpose
    if purpose:
        query = query.where(FileObject.purpose == purpose)
    
    # Pagination cursor
    if after:
        try:
            after_uuid = UUID(after)
            if order == "desc":
                query = query.where(FileObject.id > after_uuid)
            else:
                query = query.where(FileObject.id < after_uuid)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'after' cursor",
            )
    
    # Order
    if order == "desc":
        query = query.order_by(desc(FileObject.created_at))
    else:
        query = query.order_by(FileObject.created_at)
    
    # Limit
    query = query.limit(limit + 1)  # +1 to check if there are more results
    
    result = await db.execute(query)
    files = result.scalars().all()
    
    # Check if there are more results
    has_more = len(files) > limit
    if has_more:
        files = files[:limit]
    
    # Build response
    file_responses = [
        FileInfoResponse(
            id=str(f.id),
            object="file",
            bytes=f.bytes,
            created_at=_to_unix_timestamp(f.created_at),
            filename=f.filename,
            purpose=f.purpose,
        )
        for f in files
    ]
    
    return FileListResponse(
        object="list",
        data=file_responses,
        has_more=has_more,
    )


@router.get(
    "/files/{file_id}",
    response_model=FileInfoResponse,
    responses={
        200: {"description": "File information"},
        401: {"description": "Authentication required"},
        404: {"description": "File not found"},
    },
)
async def retrieve_file(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> FileInfoResponse:
    """Retrieve information about a file.
    
    **Example Request:**
    ```bash
    curl -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/files/file-abc123"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Get file
    try:
        file_uuid = UUID(file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format",
        )
    
    result = await db.execute(
        select(FileObject).where(FileObject.id == file_uuid)
    )
    file_obj = result.scalar_one_or_none()
    
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Check access (must belong to same org or api_key)
    if key_info:
        if file_obj.org_id and file_obj.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not file_obj.org_id and api_key_id and file_obj.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
    
    return FileInfoResponse(
        id=str(file_obj.id),
        object="file",
        bytes=file_obj.bytes,
        created_at=_to_unix_timestamp(file_obj.created_at),
        filename=file_obj.filename,
        purpose=file_obj.purpose,
    )


@router.get(
    "/files/{file_id}/content",
    responses={
        200: {"description": "File content"},
        401: {"description": "Authentication required"},
        404: {"description": "File not found"},
    },
)
async def retrieve_file_content(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> Response:
    """Retrieve the content of a file.
    
    **Example Request:**
    ```bash
    curl -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/files/file-abc123/content" \
        -o file_content.jsonl
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Get file
    try:
        file_uuid = UUID(file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format",
        )
    
    result = await db.execute(
        select(FileObject).where(FileObject.id == file_uuid)
    )
    file_obj = result.scalar_one_or_none()
    
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Check access
    if key_info:
        if file_obj.org_id and file_obj.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not file_obj.org_id and api_key_id and file_obj.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
    
    # Determine content type
    content_type = file_obj.content_type or "application/octet-stream"
    
    return Response(
        content=file_obj.content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_obj.filename or file_id}"',
        },
    )


@router.delete(
    "/files/{file_id}",
    response_model=FileDeleteResponse,
    responses={
        200: {"description": "File deleted"},
        401: {"description": "Authentication required"},
        404: {"description": "File not found"},
    },
)
async def delete_file(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    auth_context: Annotated[AuthContext, Depends(require_auth)] = None,
) -> FileDeleteResponse:
    """Delete a file.
    
    **Example Request:**
    ```bash
    curl -X DELETE -H "Authorization: Bearer $API_KEY" \
        "http://localhost:8000/v1/files/file-abc123"
    ```
    """
    key_info = auth_context.key_info
    api_key_id = auth_context.api_key_id
    
    # Get file
    try:
        file_uuid = UUID(file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format",
        )
    
    result = await db.execute(
        select(FileObject).where(FileObject.id == file_uuid)
    )
    file_obj = result.scalar_one_or_none()
    
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Check access
    if key_info:
        if file_obj.org_id and file_obj.org_id != key_info.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
        # For DB-backed keys, check api_key_id; for in-memory keys, skip this check
        if not file_obj.org_id and api_key_id and file_obj.api_key_id != api_key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
    
    # Delete file
    await db.delete(file_obj)
    await db.commit()
    
    logger.info(f"File deleted: id={file_id}")
    
    return FileDeleteResponse(
        id=file_id,
        object="file",
        deleted=True,
    )
