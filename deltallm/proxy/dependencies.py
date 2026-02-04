"""Unified authentication dependencies for ProxyLLM routes.

This module provides FastAPI dependencies for authentication that work with:
- Master key (for admin operations)
- In-memory generated keys (sk-proxy-*)
- Database-backed session keys (sk-admin-* from login)
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, OrgMember, User
from deltallm.db.session import get_db_session
from deltallm.proxy.auth import APIKeyManager, VirtualKey

# Security scheme for Bearer token extraction
security = HTTPBearer(auto_error=False)


class AuthContext:
    """Authentication context holding key info and user details."""
    
    def __init__(
        self,
        key_info: VirtualKey,
        user: Optional[User] = None,
        api_key_model: Optional[APIKey] = None,
    ):
        self.key_info = key_info
        self.user = user
        self.api_key_model = api_key_model
        
    @property
    def is_master_key(self) -> bool:
        """Check if the authenticated key is the master key."""
        return self.key_info.key_hash == "master"
    
    @property
    def user_id(self) -> Optional[str]:
        """Get user ID from key info."""
        return self.key_info.user_id
    
    @property
    def org_id(self) -> Optional[str]:
        """Get org ID from key info."""
        return self.key_info.org_id
    
    @property
    def team_id(self) -> Optional[str]:
        """Get team ID from key info."""
        return self.key_info.team_id
    
    @property
    def api_key_id(self) -> Optional[UUID]:
        """Get API key ID from database model.
        
        Returns the database UUID for DB-backed keys (sk-admin-*).
        Returns None for in-memory keys (sk-proxy-*) or master key.
        """
        if self.api_key_model and hasattr(self.api_key_model, 'id'):
            return self.api_key_model.id
        return None


async def extract_token(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
) -> Optional[str]:
    """Extract Bearer token from request.
    
    Args:
        request: FastAPI request
        credentials: HTTP Authorization credentials from Bearer scheme
        
    Returns:
        Token string if valid Bearer token present, None otherwise
    """
    # Skip auth for public endpoints (health, docs, login)
    public_paths = ["/health", "/docs", "/openapi.json", "/redoc", "/auth/login"]
    if any(request.url.path.startswith(path) for path in public_paths):
        return None
    
    if not credentials:
        # Also check header directly for compatibility
        auth_header = request.headers.get("authorization")
        if not auth_header:
            return None
        
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]
    
    return credentials.credentials


async def require_auth(
    request: Request,
    token: Annotated[Optional[str], Depends(extract_token)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthContext:
    """Require valid authentication (any valid API key).
    
    Validates Bearer token against:
    1. Master key (in-memory)
    2. In-memory generated keys (sk-proxy-*)
    3. Database keys (sk-admin-* session tokens from login)
    
    Args:
        request: FastAPI request
        token: Extracted Bearer token
        db: Database session
        
    Returns:
        AuthContext with key information
        
    Raises:
        HTTPException: 401 if authentication fails
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get key manager from app state
    key_manager: APIKeyManager = request.app.state.key_manager
    
    # 1. Try in-memory validation first (fast path for generated keys)
    key_info = key_manager.validate_key(token)
    if key_info:
        return AuthContext(key_info=key_info)
    
    # 2. Try database validation (for login session tokens)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    db_key = result.scalar_one_or_none()
    
    if db_key:
        # Check expiration
        if db_key.expires_at and db_key.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Convert DB model to VirtualKey
        key_info = VirtualKey(
            key=token,
            key_hash=key_hash,
            key_alias=db_key.key_alias,
            user_id=str(db_key.user_id) if db_key.user_id else None,
            team_id=str(db_key.team_id) if db_key.team_id else None,
            org_id=str(db_key.org_id) if db_key.org_id else None,
            models=db_key.models,
            max_budget=float(db_key.max_budget) if db_key.max_budget else None,
            spend=float(db_key.spend),
            tpm_limit=db_key.tpm_limit,
            rpm_limit=db_key.rpm_limit,
            expires_at=db_key.expires_at,
            created_at=db_key.created_at,
            metadata=dict(db_key.key_metadata) if db_key.key_metadata else {},
        )
        
        # Optionally load user if associated
        user = None
        if db_key.user_id:
            user_result = await db.execute(
                select(User).where(User.id == db_key.user_id)
            )
            user = user_result.scalar_one_or_none()
        
        return AuthContext(
            key_info=key_info,
            user=user,
            api_key_model=db_key,
        )
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    auth_context: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Require authentication with a user account.
    
    This dependency ensures the API key is associated with a valid,
    active user account. Required for organization/team management.
    
    Args:
        auth_context: Authentication context from require_auth
        db: Database session
        
    Returns:
        Authenticated User model
        
    Raises:
        HTTPException: 401 if no user associated or user inactive
    """
    # Check if user already loaded in auth context
    if auth_context.user:
        if not auth_context.user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is deactivated",
            )
        return auth_context.user
    
    # Need to load user from database
    if not auth_context.key_info.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. This endpoint requires a user account.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    result = await db.execute(
        select(User).where(User.id == UUID(auth_context.key_info.user_id))
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )
    
    return user


async def require_master_key(
    auth_context: Annotated[AuthContext, Depends(require_auth)],
) -> AuthContext:
    """Require master key authentication.
    
    For admin-only operations like creating initial users or
    system-wide configuration.
    
    Args:
        auth_context: Authentication context from require_auth
        
    Returns:
        AuthContext with master key
        
    Raises:
        HTTPException: 403 if not master key
    """
    if not auth_context.is_master_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Master key required",
        )
    return auth_context


async def require_superuser(
    current_user: Annotated[User, Depends(require_user)],
) -> User:
    """Require superuser privileges.
    
    Args:
        current_user: Authenticated user from require_user
        
    Returns:
        User if superuser
        
    Raises:
        HTTPException: 403 if not superuser
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )
    return current_user


async def get_current_user_optional(
    request: Request,
    token: Annotated[Optional[str], Depends(extract_token)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Optional[User]:
    """Get current user if authenticated (optional).

    This dependency extracts the user from the token without requiring
    authentication. Returns None if not authenticated.

    Args:
        request: FastAPI request
        token: Extracted Bearer token
        db: Database session

    Returns:
        User if authenticated, None otherwise
    """
    if not token:
        return None

    try:
        auth_context = await require_auth(request, token, db)
        if auth_context.user:
            return auth_context.user

        # Load user if not already loaded
        if auth_context.key_info.user_id:
            result = await db.execute(
                select(User).where(User.id == UUID(auth_context.key_info.user_id))
            )
            return result.scalar_one_or_none()
    except HTTPException:
        pass

    return None


async def check_org_admin(
    user: User,
    org_id: UUID,
    db: AsyncSession,
) -> bool:
    """Check if a user is an admin or owner of an organization.

    Args:
        user: The user to check
        org_id: The organization ID
        db: Database session

    Returns:
        True if user is org admin/owner or superuser, False otherwise
    """
    # Superusers always have admin access
    if user.is_superuser:
        return True

    # Check org membership
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.user_id == user.id,
            OrgMember.org_id == org_id,
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        return False

    # Check if role is owner or admin
    return membership.role in ("org_owner", "org_admin", "owner", "admin")


async def require_org_admin(
    user: User,
    org_id: UUID,
    db: AsyncSession,
) -> None:
    """Require the user to be an admin or owner of an organization.

    Args:
        user: The user to check
        org_id: The organization ID
        db: Database session

    Raises:
        HTTPException: 403 if user is not an org admin
    """
    if not await check_org_admin(user, org_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin privileges required",
        )


async def check_org_member(
    user: User,
    org_id: UUID,
    db: AsyncSession,
) -> bool:
    """Check if a user is a member of an organization.

    Args:
        user: The user to check
        org_id: The organization ID
        db: Database session

    Returns:
        True if user is an org member or superuser, False otherwise
    """
    # Superusers always have access
    if user.is_superuser:
        return True

    # Check org membership
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.user_id == user.id,
            OrgMember.org_id == org_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_user_org_ids(
    user: User,
    db: AsyncSession,
) -> list[UUID]:
    """Get all organization IDs the user belongs to.

    Args:
        user: The user
        db: Database session

    Returns:
        List of organization UUIDs
    """
    result = await db.execute(
        select(OrgMember.org_id).where(OrgMember.user_id == user.id)
    )
    return list(result.scalars().all())
