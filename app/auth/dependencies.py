"""Authentication dependencies for FastAPI."""

from typing import Annotated, Optional
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import decode_token
from app.models.user import User, UserRole
from app.utils.rate_limiter import rate_limiter

logger = get_logger(__name__)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    access_token: Optional[str] = Cookie(default=None),
) -> User:
    """Get the current authenticated user from JWT cookie."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not access_token:
        raise credentials_exception

    # Decode token
    payload = decode_token(access_token)
    if payload is None:
        raise credentials_exception

    # Check token type
    if payload.get("type") != "access":
        raise credentials_exception

    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exception

    # Get user from database
    try:
        stmt = select(User).where(
            User.id == UUID(user_id),
            User.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise credentials_exception

        return user

    except ValueError:
        raise credentials_exception


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current active user."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return current_user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    access_token: Optional[str] = Cookie(default=None),
) -> Optional[User]:
    """Get the current user if authenticated, None otherwise."""
    if not access_token:
        return None

    payload = decode_token(access_token)
    if payload is None or payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    try:
        stmt = select(User).where(
            User.id == UUID(user_id),
            User.deleted_at.is_(None),
            User.is_active == True,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    except ValueError:
        return None


async def require_admin(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Require admin role."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def check_rate_limit(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Check rate limit for authenticated user."""
    key = f"user:{current_user.id}"
    is_limited, remaining, reset_time = await rate_limiter.is_rate_limited(key)

    if is_limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_time),
            },
        )

    return current_user


def get_client_ip(request: Request) -> str:
    """Get the client IP address from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    """Get the user agent from request."""
    return request.headers.get("User-Agent", "unknown")


# Type aliases for dependency injection
CurrentUser = Annotated[User, Depends(get_current_active_user)]
OptionalUser = Annotated[Optional[User], Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_admin)]
RateLimitedUser = Annotated[User, Depends(check_rate_limit)]
