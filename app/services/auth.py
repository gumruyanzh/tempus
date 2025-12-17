"""Authentication service for user registration and login."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import User, UserRole

logger = get_logger(__name__)


class AuthError(Exception):
    """Authentication error."""

    pass


class AuthService:
    """Service for authentication operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register_user(
        self,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        timezone_str: str = "UTC",
    ) -> User:
        """Register a new user."""
        # Check if email already exists
        existing_user = await self.get_user_by_email(email)
        if existing_user:
            raise AuthError("Email already registered")

        # Validate password strength
        self._validate_password(password)

        # Create user
        user = User(
            email=email.lower().strip(),
            hashed_password=hash_password(password),
            full_name=full_name,
            timezone=timezone_str,
            role=UserRole.USER,
            is_active=True,
            is_verified=False,
        )

        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)

        logger.info(
            "User registered",
            user_id=str(user.id),
            email=email,
        )

        return user

    async def authenticate_user(
        self,
        email: str,
        password: str,
    ) -> Optional[User]:
        """Authenticate a user with email and password."""
        user = await self.get_user_by_email(email)

        if not user:
            logger.warning("Login attempt for non-existent email", email=email)
            return None

        if not user.is_active:
            logger.warning("Login attempt for inactive user", user_id=str(user.id))
            return None

        if user.is_deleted:
            logger.warning("Login attempt for deleted user", user_id=str(user.id))
            return None

        if not verify_password(password, user.hashed_password):
            logger.warning("Invalid password attempt", user_id=str(user.id))
            return None

        # Update last login
        user.update_last_login()
        await self.db.flush()

        logger.info("User authenticated", user_id=str(user.id))
        return user

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email address."""
        stmt = select(User).where(
            User.email == email.lower().strip(),
            User.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        """Get a user by ID."""
        stmt = select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def create_tokens(self, user: User) -> dict[str, str]:
        """Create access and refresh tokens for a user."""
        token_data = {
            "sub": str(user.id),
            "email": user.email,
            "role": user.role.value,
        }

        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> bool:
        """Change a user's password."""
        if not verify_password(current_password, user.hashed_password):
            raise AuthError("Current password is incorrect")

        self._validate_password(new_password)

        user.hashed_password = hash_password(new_password)
        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info("Password changed", user_id=str(user.id))
        return True

    @staticmethod
    def _validate_password(password: str) -> None:
        """Validate password strength."""
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters long")
        if not any(c.isupper() for c in password):
            raise AuthError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in password):
            raise AuthError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in password):
            raise AuthError("Password must contain at least one digit")
