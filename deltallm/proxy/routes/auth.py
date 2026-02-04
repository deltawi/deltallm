"""Authentication routes for ProxyLLM.

Provides login, registration, and user management endpoints.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, User
from deltallm.db.session import get_db_session

# Password hashing using hashlib (no extra dependencies)
def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with a salt."""
    salt = secrets.token_hex(16)
    hash_value = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hash_value}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a hash."""
    try:
        salt, hash_value = hashed.split("$")
        return hashlib.sha256((salt + password).encode()).hexdigest() == hash_value
    except ValueError:
        return False


def generate_token() -> str:
    """Generate a secure API token."""
    return f"sk-admin-{secrets.token_urlsafe(32)}"


# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# Schemas
class LoginRequest(BaseModel):
    """Login request."""
    username: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=1, description="User password")


class TokenResponse(BaseModel):
    """Token response."""
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    is_superuser: bool
    is_active: bool

    class Config:
        from_attributes = True


class RegisterRequest(BaseModel):
    """Registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: Optional[str] = None
    last_name: Optional[str] = None


# Router
router = APIRouter(prefix="/auth", tags=["Authentication"])


async def get_current_user_from_token(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Optional[User]:
    """Get the current user from the token."""
    if not token:
        return None

    # Hash the token to find the API key
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if not api_key or not api_key.user_id:
        return None

    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    return result.scalar_one_or_none()


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Login with email and password.

    Returns an access token that can be used for authenticated requests.
    """
    # Find user by email
    result = await db.execute(
        select(User).where(User.email == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check password
    if not user.password_hash or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    # Generate token and create API key for session
    token = generate_token()
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    # Create session API key (expires in 24 hours)
    session_key = APIKey(
        key_hash=key_hash,
        key_alias=f"session-{user.email}",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        key_metadata={"type": "session"},
    )
    db.add(session_key)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)

    await db.commit()

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            id=str(user.id),
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_superuser=user.is_superuser,
            is_active=user.is_active,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Get the currently authenticated user."""
    user = await get_current_user_from_token(token, db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UserResponse(
        id=str(user.id),
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_superuser=user.is_superuser,
        is_active=user.is_active,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Register a new user.

    Only superusers can register new users.
    """
    # Check if caller is a superuser
    caller = await get_current_user_from_token(token, db)
    if not caller or not caller.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can register new users",
        )

    # Check if email is already taken
    result = await db.execute(
        select(User).where(User.email == data.email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create user
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        is_superuser=False,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_superuser=user.is_superuser,
        is_active=user.is_active,
    )


@router.post("/logout")
async def logout(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Logout and invalidate the current session token."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Delete the session API key
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if api_key:
        await db.delete(api_key)
        await db.commit()

    return {"message": "Logged out successfully"}
