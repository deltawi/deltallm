"""API key management routes."""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Request, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, User
from deltallm.db.session import get_db_session
from deltallm.proxy.auth import APIKeyManager
from deltallm.proxy.dependencies import require_auth, require_master_key, require_user, AuthContext

router = APIRouter(tags=["keys"])


def get_key_manager(request: Request) -> APIKeyManager:
    """Get the key manager from app state."""
    return request.app.state.key_manager


class GenerateKeyRequest(BaseModel):
    """Request to generate a new API key."""
    
    key_alias: Optional[str] = None
    org_id: Optional[str] = None  # Added for org scoping
    team_id: Optional[str] = None
    user_id: Optional[str] = None  # Can override, defaults to current user
    models: Optional[list[str]] = None
    max_budget: Optional[float] = None
    tpm_limit: Optional[int] = Field(default=None, ge=1)
    rpm_limit: Optional[int] = Field(default=None, ge=1)
    expires_in_days: Optional[int] = Field(default=None, ge=1)
    metadata: Optional[dict] = None


class GenerateKeyResponse(BaseModel):
    """Response with generated API key."""
    
    key: str
    key_hash: str
    key_alias: Optional[str] = None


class KeyInfoResponse(BaseModel):
    """Response with key information."""
    
    key_hash: str
    key_alias: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    org_id: Optional[str] = None
    models: Optional[list[str]] = None
    max_budget: Optional[float] = None
    spend: float
    tpm_limit: Optional[int] = None
    rpm_limit: Optional[int] = None
    expires_at: Optional[str] = None
    created_at: str


@router.post("/key/generate", response_model=GenerateKeyResponse)
async def generate_key(
    body: GenerateKeyRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
    key_manager: APIKeyManager = Depends(get_key_manager),
):
    """Generate a new API key.
    
    The key is persisted to the database with proper scoping (org/team/user).
    """
    key_manager: APIKeyManager = request.app.state.key_manager
    
    # Determine user_id for the key
    # Users can only create keys for themselves unless admin
    key_user_id = body.user_id
    if key_user_id and key_user_id != str(current_user.id):
        # Only superusers can create keys for other users
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot create key for another user",
            )
    else:
        key_user_id = str(current_user.id)
    
    # Generate key in-memory first
    key = key_manager.generate_key(
        key_alias=body.key_alias,
        user_id=key_user_id,
        team_id=body.team_id,
        models=body.models,
        max_budget=body.max_budget,
        tpm_limit=body.tpm_limit,
        rpm_limit=body.rpm_limit,
        expires_in_days=body.expires_in_days,
        metadata=body.metadata,
    )
    
    # Calculate expiration
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    
    # Persist to database
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    db_key = APIKey(
        key_hash=key_hash,
        key_alias=body.key_alias,
        user_id=UUID(key_user_id) if key_user_id else None,
        team_id=UUID(body.team_id) if body.team_id else None,
        org_id=UUID(body.org_id) if body.org_id else None,
        models=body.models,
        max_budget=Decimal(str(body.max_budget)) if body.max_budget else None,
        tpm_limit=body.tpm_limit,
        rpm_limit=body.rpm_limit,
        expires_at=expires_at,
        key_metadata=body.metadata or {"type": "api"},
        created_by=current_user.id,
    )
    db.add(db_key)
    await db.commit()
    
    return GenerateKeyResponse(
        key=key,
        key_hash=key_hash,
        key_alias=body.key_alias,
    )


@router.get("/key/info", response_model=KeyInfoResponse)
async def get_key_info_endpoint(
    auth_context: Annotated[AuthContext, Depends(require_auth)],
):
    """Get information about the current API key."""
    key_info = auth_context.key_info
    
    return KeyInfoResponse(
        key_hash=key_info.key_hash,
        key_alias=key_info.key_alias,
        user_id=key_info.user_id,
        team_id=key_info.team_id,
        org_id=key_info.org_id,
        models=key_info.models,
        max_budget=key_info.max_budget,
        spend=key_info.spend,
        tpm_limit=key_info.tpm_limit,
        rpm_limit=key_info.rpm_limit,
        expires_at=key_info.expires_at.isoformat() if key_info.expires_at else None,
        created_at=key_info.created_at.isoformat() if key_info.created_at else "",
    )


@router.post("/key/update")
async def update_key(
    auth_context: Annotated[AuthContext, Depends(require_auth)],
):
    """Update an API key (not yet implemented)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Key update not yet implemented",
    )


@router.delete("/key/delete")
async def delete_key(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    auth_context: Annotated[AuthContext, Depends(require_auth)],
):
    """Delete the current API key."""
    key_info = auth_context.key_info
    
    # Don't allow deleting master key
    if key_info.key_hash == "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete master key",
        )
    
    key_manager: APIKeyManager = request.app.state.key_manager
    
    # Revoke from in-memory
    key_manager.revoke_key(key_info.key)
    
    # Delete from database
    key_hash = hashlib.sha256(key_info.key.encode()).hexdigest()
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    db_key = result.scalar_one_or_none()
    
    if db_key:
        await db.delete(db_key)
        await db.commit()
    
    return {"deleted": True}


@router.get("/key/list")
async def list_keys(
    user_id: Optional[str] = None,
    team_id: Optional[str] = None,
    org_id: Optional[str] = None,
    request: Request = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
    key_manager: APIKeyManager = Depends(get_key_manager),
):
    """List API keys.
    
    Users can list their own keys. Superusers can list all keys.
    """
    # Superusers can list all keys
    if current_user.is_superuser:
        # Build query
        query = select(APIKey)
        
        if user_id:
            query = query.where(APIKey.user_id == UUID(user_id))
        if team_id:
            query = query.where(APIKey.team_id == UUID(team_id))
        if org_id:
            query = query.where(APIKey.org_id == UUID(org_id))
        
        result = await db.execute(query)
        db_keys = result.scalars().all()
        
        return {
            "keys": [
                {
                    "key_hash": k.key_hash,
                    "key_alias": k.key_alias,
                    "user_id": str(k.user_id) if k.user_id else None,
                    "team_id": str(k.team_id) if k.team_id else None,
                    "org_id": str(k.org_id) if k.org_id else None,
                    "models": k.models,
                    "max_budget": float(k.max_budget) if k.max_budget else None,
                    "spend": float(k.spend),
                    "created_at": k.created_at.isoformat() if k.created_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                }
                for k in db_keys
            ]
        }
    
    # Regular users can only list their own keys
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == current_user.id)
    )
    db_keys = result.scalars().all()
    
    return {
        "keys": [
            {
                "key_hash": k.key_hash,
                "key_alias": k.key_alias,
                "user_id": str(k.user_id) if k.user_id else None,
                "team_id": str(k.team_id) if k.team_id else None,
                "org_id": str(k.org_id) if k.org_id else None,
                "models": k.models,
                "max_budget": float(k.max_budget) if k.max_budget else None,
                "spend": float(k.spend),
                "created_at": k.created_at.isoformat() if k.created_at else None,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            }
            for k in db_keys
        ]
    }
