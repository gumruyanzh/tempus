"""User service for profile and settings management."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import decrypt_value, encrypt_value
from app.models.user import APIKeyType, EncryptedAPIKey, User

logger = get_logger(__name__)


class UserServiceError(Exception):
    """User service error."""

    pass


class UserService:
    """Service for user operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_user(self, user_id: UUID) -> Optional[User]:
        """Get a user by ID."""
        stmt = select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_profile(
        self,
        user: User,
        full_name: Optional[str] = None,
        timezone_str: Optional[str] = None,
    ) -> User:
        """Update user profile."""
        if full_name is not None:
            user.full_name = full_name
        if timezone_str is not None:
            user.timezone = timezone_str

        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(user)

        logger.info("Profile updated", user_id=str(user.id))
        return user

    async def update_default_prompt_settings(
        self,
        user: User,
        default_prompt_template: Optional[str] = None,
        default_tone: Optional[str] = None,
    ) -> User:
        """Update user's default prompt settings."""
        if default_prompt_template is not None:
            user.default_prompt_template = default_prompt_template
        if default_tone is not None:
            user.default_tone = default_tone

        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(user)

        logger.info("Prompt settings updated", user_id=str(user.id))
        return user

    async def store_api_key(
        self,
        user: User,
        key_type: APIKeyType,
        api_key: str,
    ) -> EncryptedAPIKey:
        """Store an encrypted API key for a user."""
        # Check if key already exists
        existing_key = await self.get_api_key(user.id, key_type)
        if existing_key:
            # Update existing key
            existing_key.encrypted_key = encrypt_value(api_key)
            existing_key.key_hint = api_key[-4:] if len(api_key) >= 4 else api_key
            existing_key.is_valid = True
            existing_key.updated_at = datetime.now(timezone.utc)
            await self.db.flush()
            await self.db.refresh(existing_key)

            logger.info(
                "API key updated",
                user_id=str(user.id),
                key_type=key_type.value,
            )
            return existing_key

        # Create new key
        encrypted_key = EncryptedAPIKey(
            user_id=user.id,
            key_type=key_type,
            encrypted_key=encrypt_value(api_key),
            key_hint=api_key[-4:] if len(api_key) >= 4 else api_key,
            is_valid=True,
        )

        self.db.add(encrypted_key)
        await self.db.flush()
        await self.db.refresh(encrypted_key)

        logger.info(
            "API key stored",
            user_id=str(user.id),
            key_type=key_type.value,
        )
        return encrypted_key

    async def get_api_key(
        self,
        user_id: UUID,
        key_type: APIKeyType,
    ) -> Optional[EncryptedAPIKey]:
        """Get an encrypted API key for a user."""
        stmt = select(EncryptedAPIKey).where(
            EncryptedAPIKey.user_id == user_id,
            EncryptedAPIKey.key_type == key_type,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_decrypted_api_key(
        self,
        user_id: UUID,
        key_type: APIKeyType,
    ) -> Optional[str]:
        """Get a decrypted API key for a user."""
        encrypted_key = await self.get_api_key(user_id, key_type)
        if not encrypted_key or not encrypted_key.is_valid:
            return None

        # Update last used timestamp
        encrypted_key.update_last_used()
        await self.db.flush()

        return decrypt_value(encrypted_key.encrypted_key)

    async def delete_api_key(
        self,
        user_id: UUID,
        key_type: APIKeyType,
    ) -> bool:
        """Delete an API key."""
        encrypted_key = await self.get_api_key(user_id, key_type)
        if not encrypted_key:
            return False

        await self.db.delete(encrypted_key)
        await self.db.flush()

        logger.info(
            "API key deleted",
            user_id=str(user_id),
            key_type=key_type.value,
        )
        return True

    async def invalidate_api_key(
        self,
        user_id: UUID,
        key_type: APIKeyType,
    ) -> bool:
        """Mark an API key as invalid."""
        encrypted_key = await self.get_api_key(user_id, key_type)
        if not encrypted_key:
            return False

        encrypted_key.is_valid = False
        encrypted_key.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info(
            "API key invalidated",
            user_id=str(user_id),
            key_type=key_type.value,
        )
        return True

    async def soft_delete_user(self, user: User) -> None:
        """Soft delete a user account."""
        user.soft_delete()
        user.is_active = False
        await self.db.flush()

        logger.info("User soft deleted", user_id=str(user.id))

    async def deactivate_user(self, user: User) -> None:
        """Deactivate a user account."""
        user.is_active = False
        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info("User deactivated", user_id=str(user.id))

    async def activate_user(self, user: User) -> None:
        """Activate a user account."""
        user.is_active = True
        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info("User activated", user_id=str(user.id))
