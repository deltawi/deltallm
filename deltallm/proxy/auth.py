"""Authentication for the proxy server."""

import hashlib
import secrets
from typing import Annotated, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, User
from deltallm.db.session import get_db_session


@dataclass
class VirtualKey:
    """Virtual API key information."""
    
    key: str
    key_hash: str
    key_alias: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    org_id: Optional[str] = None
    models: Optional[list[str]] = None
    max_budget: Optional[float] = None
    spend: float = 0.0
    tpm_limit: Optional[int] = None
    rpm_limit: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    metadata: dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


class APIKeyManager:
    """Manages API keys for the proxy server."""
    
    def __init__(self, master_key: Optional[str] = None, db_cache_ttl: int = 300):
        """Initialize the key manager.
        
        Args:
            master_key: Master key for admin operations
            db_cache_ttl: Cache TTL for database-backed keys in seconds (default: 5 min)
        """
        self.master_key = master_key
        self._keys: dict[str, VirtualKey] = {}  # hash -> key info (in-memory keys)
        self._key_cache: dict[str, VirtualKey] = {}  # key -> key info (for lookup)
        # Cache for database-backed keys with TTL
        self._db_key_cache: dict[str, tuple[VirtualKey, datetime]] = {}
        self._db_cache_ttl = timedelta(seconds=db_cache_ttl)
    
    def _hash_key(self, key: str) -> str:
        """Hash an API key.
        
        Args:
            key: The API key
            
        Returns:
            Hashed key
        """
        return hashlib.sha256(key.encode()).hexdigest()
    
    def generate_key(
        self,
        key_alias: Optional[str] = None,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        models: Optional[list[str]] = None,
        max_budget: Optional[float] = None,
        tpm_limit: Optional[int] = None,
        rpm_limit: Optional[int] = None,
        expires_in_days: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Generate a new API key.
        
        Args:
            key_alias: Human-readable alias
            user_id: Associated user ID
            team_id: Associated team ID
            models: Allowed models
            max_budget: Maximum budget
            tpm_limit: Tokens per minute limit
            rpm_limit: Requests per minute limit
            expires_in_days: Key expiration
            metadata: Additional metadata
            
        Returns:
            The generated API key
        """
        # Generate key with prefix
        key = f"sk-proxy-{secrets.token_urlsafe(32)}"
        key_hash = self._hash_key(key)
        
        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        
        # Create key info
        key_info = VirtualKey(
            key=key,
            key_hash=key_hash,
            key_alias=key_alias,
            user_id=user_id,
            team_id=team_id,
            models=models,
            max_budget=max_budget,
            tpm_limit=tpm_limit,
            rpm_limit=rpm_limit,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        
        # Store key
        self._keys[key_hash] = key_info
        self._key_cache[key] = key_info
        
        return key
    
    def validate_key(self, key: str) -> Optional[VirtualKey]:
        """Validate an API key (synchronous, in-memory only).
        
        This method only checks in-memory keys (master key and generated keys).
        For database-backed keys (login session tokens), use validate_key_async.
        
        Args:
            key: The API key to validate
            
        Returns:
            Key info if valid, None otherwise
        """
        # Check master key
        if self.master_key and key == self.master_key:
            return VirtualKey(
                key=key,
                key_hash="master",
                key_alias="master",
            )
        
        # Check cache first
        if key in self._key_cache:
            key_info = self._key_cache[key]
            # Check expiration
            if key_info.expires_at and key_info.expires_at < datetime.now(timezone.utc):
                return None
            return key_info
        
        # Check by hash
        key_hash = self._hash_key(key)
        if key_hash in self._keys:
            key_info = self._keys[key_hash]
            # Check expiration
            if key_info.expires_at and key_info.expires_at < datetime.now(timezone.utc):
                return None
            # Cache for future lookups
            self._key_cache[key] = key_info
            return key_info
        
        return None
    
    def _get_cached_db_key(self, key_hash: str) -> Optional[VirtualKey]:
        """Get cached database key if not expired.
        
        Args:
            key_hash: The key hash to look up
            
        Returns:
            Cached VirtualKey if valid, None otherwise
        """
        if key_hash in self._db_key_cache:
            key_info, cached_at = self._db_key_cache[key_hash]
            if datetime.now(timezone.utc) - cached_at < self._db_cache_ttl:
                # Check if key itself is not expired
                if key_info.expires_at and key_info.expires_at < datetime.now(timezone.utc):
                    del self._db_key_cache[key_hash]
                    return None
                return key_info
            # Expired, remove from cache
            del self._db_key_cache[key_hash]
        return None
    
    def _cache_db_key(self, key_hash: str, key_info: VirtualKey) -> None:
        """Cache database key with timestamp.
        
        Args:
            key_hash: The key hash
            key_info: VirtualKey to cache
        """
        self._db_key_cache[key_hash] = (key_info, datetime.now(timezone.utc))
    
    async def validate_key_async(
        self, 
        key: str, 
        db: Optional[AsyncSession] = None
    ) -> Optional[VirtualKey]:
        """Validate an API key with database support.
        
        This method checks in-memory keys first (fast path), then falls back
        to database query for session/login tokens.
        
        Args:
            key: The API key to validate
            db: Database session for querying DB-backed keys
            
        Returns:
            Key info if valid, None otherwise
        """
        # 1. Check in-memory keys first (fast path)
        key_info = self.validate_key(key)
        if key_info:
            return key_info
        
        # 2. Check database-backed keys (requires DB session)
        if db is None:
            return None
        
        key_hash = self._hash_key(key)
        
        # Check cache first
        cached = self._get_cached_db_key(key_hash)
        if cached:
            return cached
        
        # Query database
        result = await db.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        db_key = result.scalar_one_or_none()
        
        if not db_key:
            return None
        
        # Check expiration
        if db_key.expires_at and db_key.expires_at < datetime.now(timezone.utc):
            return None
        
        # Convert to VirtualKey
        key_info = VirtualKey(
            key=key,
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
        
        # Cache for future lookups
        self._cache_db_key(key_hash, key_info)
        
        return key_info
    
    def revoke_key(self, key: str) -> bool:
        """Revoke an API key.
        
        Args:
            key: The API key to revoke
            
        Returns:
            True if key was found and revoked
        """
        key_hash = self._hash_key(key)
        if key_hash in self._keys:
            del self._keys[key_hash]
            self._key_cache.pop(key, None)
            return True
        return False
    
    def get_key_info(self, key_hash: str) -> Optional[VirtualKey]:
        """Get key info by hash.
        
        Args:
            key_hash: The key hash
            
        Returns:
            Key info if found
        """
        return self._keys.get(key_hash)
    
    def list_keys(
        self,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> list[VirtualKey]:
        """List API keys.
        
        Args:
            user_id: Filter by user
            team_id: Filter by team
            
        Returns:
            List of key info
        """
        keys = list(self._keys.values())
        
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]
        
        if team_id:
            keys = [k for k in keys if k.team_id == team_id]
        
        return keys
    
    def update_spend(self, key_hash: str, amount: float) -> None:
        """Update spend for a key.
        
        Args:
            key_hash: The key hash
            amount: Amount to add
        """
        if key_hash in self._keys:
            self._keys[key_hash].spend += amount


class AuthMiddleware:
    """Authentication middleware for FastAPI.
    
    Note: With Option A (route-level auth), this middleware is deprecated.
    It now only extracts key info for rate limiting without raising exceptions.
    Use the `require_auth` dependency in routes for proper authentication.
    """
    
    def __init__(self, key_manager: APIKeyManager):
        """Initialize the auth middleware.
        
        Args:
            key_manager: The key manager
        """
        self.key_manager = key_manager
        self.security = HTTPBearer(auto_error=False)
    
    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ) -> Optional[VirtualKey]:
        """Extract key info from request for rate limiting purposes.
        
        This method no longer raises authentication exceptions.
        Authentication is now handled by route-level dependencies.
        
        Args:
            request: The request
            credentials: Authorization credentials
            
        Returns:
            Key info if valid in-memory key found, None otherwise
            (doesn't raise exceptions - auth is done in dependencies)
        """
        # Skip for public endpoints
        public_paths = ["/health", "/docs", "/openapi.json", "/redoc", "/auth/login"]
        if any(request.url.path.startswith(path) for path in public_paths):
            return None
        
        # Get token from header
        auth_header = request.headers.get("authorization")
        if not auth_header:
            return None
        
        # Parse Bearer token
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        
        token = parts[1]
        
        # Only check in-memory keys (fast path)
        # DB-backed keys are validated in route dependencies
        key_info = self.key_manager.validate_key(token)
        
        # Check budget for in-memory keys only
        if key_info and key_info.max_budget is not None and key_info.spend >= key_info.max_budget:
            # Budget exceeded - still return key_info but budget check will fail in route
            pass
        
        return key_info


# ========== User Authentication Dependencies ==========

security = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Optional[User]:
    """Get the current user from the request (optional).
    
    This dependency extracts the user from the JWT/API key without requiring
    authentication. Returns None if not authenticated.
    
    Args:
        request: The FastAPI request
        credentials: HTTP Authorization credentials
        db: Database session
        
    Returns:
        User if authenticated, None otherwise
    """
    # Skip for health endpoints
    if request.url.path.startswith("/health"):
        return None
    
    if not credentials:
        return None
    
    token = credentials.credentials
    
    # Try to find user by API key
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()
    
    if api_key and api_key.user_id:
        result = await db.execute(
            select(User).where(User.id == api_key.user_id)
        )
        return result.scalar_one_or_none()
    
    return None


async def get_current_user(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Get the current user from the request (required).
    
    This dependency requires authentication and returns the current user.
    Raises HTTPException if not authenticated.
    
    Args:
        request: The FastAPI request
        credentials: HTTP Authorization credentials
        db: Database session
        
    Returns:
        The authenticated User
        
    Raises:
        HTTPException: If authentication fails
    """
    user = await get_current_user_optional(request, credentials, db)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )
    
    return user


async def get_current_active_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current user and verify they are a superuser.
    
    Args:
        current_user: The current authenticated user
        
    Returns:
        The user if they are a superuser
        
    Raises:
        HTTPException: If user is not a superuser
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )
    return current_user
