"""Tests for user service."""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value
from app.models.tweet import TweetTone
from app.models.user import APIKeyType, EncryptedAPIKey, User
from app.services.user import UserService


class TestUserService:
    """Tests for UserService."""

    @pytest.mark.asyncio
    async def test_get_user(self, db_session: AsyncSession, test_user: User):
        """Test getting user by ID."""
        service = UserService(db_session)

        user = await service.get_user(test_user.id)
        assert user is not None
        assert user.id == test_user.id
        assert user.email == test_user.email

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, db_session: AsyncSession):
        """Test getting non-existent user."""
        service = UserService(db_session)

        user = await service.get_user(uuid4())
        assert user is None

    @pytest.mark.asyncio
    async def test_update_profile(self, db_session: AsyncSession, test_user: User):
        """Test updating user profile."""
        service = UserService(db_session)

        await service.update_profile(
            user=test_user,
            full_name="New Name",
            timezone_str="America/New_York",
        )
        await db_session.commit()

        assert test_user.full_name == "New Name"
        assert test_user.timezone == "America/New_York"

    @pytest.mark.asyncio
    async def test_update_profile_partial(self, db_session: AsyncSession, test_user: User):
        """Test partial profile update."""
        service = UserService(db_session)
        original_timezone = test_user.timezone

        await service.update_profile(
            user=test_user,
            full_name="Only Name Changed",
        )
        await db_session.commit()

        assert test_user.full_name == "Only Name Changed"
        assert test_user.timezone == original_timezone

    @pytest.mark.asyncio
    async def test_store_api_key(self, db_session: AsyncSession, test_user: User):
        """Test storing an API key."""
        service = UserService(db_session)

        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.DEEPSEEK,
            api_key="sk-test-new-key-12345",
        )
        await db_session.commit()

        # Retrieve the key
        key = await service.get_api_key(test_user.id, APIKeyType.DEEPSEEK)
        assert key is not None
        assert key.key_type == APIKeyType.DEEPSEEK
        assert key.is_valid is True
        assert "2345" in key.key_hint  # key_hint shows last 4 chars

    @pytest.mark.asyncio
    async def test_store_api_key_update_existing(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test updating an existing API key."""
        service = UserService(db_session)

        # Store initial key
        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.DEEPSEEK,
            api_key="sk-initial-key",
        )
        await db_session.commit()

        # Store updated key
        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.DEEPSEEK,
            api_key="sk-updated-key-67890",
        )
        await db_session.commit()

        # Should have updated, not created new
        key = await service.get_api_key(test_user.id, APIKeyType.DEEPSEEK)
        assert key is not None
        assert "7890" in key.key_hint  # key_hint shows last 4 chars

    @pytest.mark.asyncio
    async def test_get_api_key(self, db_session: AsyncSession, test_user: User, api_key):
        """Test getting an API key."""
        service = UserService(db_session)

        key = await service.get_api_key(test_user.id, APIKeyType.DEEPSEEK)
        assert key is not None
        assert key.key_type == APIKeyType.DEEPSEEK

    @pytest.mark.asyncio
    async def test_get_api_key_not_found(self, db_session: AsyncSession, test_user: User):
        """Test getting non-existent API key."""
        service = UserService(db_session)

        key = await service.get_api_key(test_user.id, APIKeyType.TAVILY)
        assert key is None

    @pytest.mark.asyncio
    async def test_get_decrypted_api_key(
        self, db_session: AsyncSession, test_user: User, api_key
    ):
        """Test getting decrypted API key."""
        service = UserService(db_session)

        decrypted = await service.get_decrypted_api_key(test_user.id, APIKeyType.DEEPSEEK)
        assert decrypted is not None
        assert "sk-test-deepseek-key" in decrypted

    @pytest.mark.asyncio
    async def test_get_decrypted_api_key_not_found(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test getting non-existent decrypted API key."""
        service = UserService(db_session)

        decrypted = await service.get_decrypted_api_key(test_user.id, APIKeyType.TAVILY)
        assert decrypted is None

    @pytest.mark.asyncio
    async def test_delete_api_key(self, db_session: AsyncSession, test_user: User, api_key):
        """Test deleting an API key."""
        service = UserService(db_session)

        success = await service.delete_api_key(test_user.id, APIKeyType.DEEPSEEK)
        await db_session.commit()

        assert success is True

        # Verify it's deleted
        key = await service.get_api_key(test_user.id, APIKeyType.DEEPSEEK)
        assert key is None

    @pytest.mark.asyncio
    async def test_delete_api_key_not_found(self, db_session: AsyncSession, test_user: User):
        """Test deleting non-existent API key."""
        service = UserService(db_session)

        success = await service.delete_api_key(test_user.id, APIKeyType.TAVILY)
        assert success is False

    @pytest.mark.asyncio
    async def test_update_default_prompt_settings(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test updating default prompt settings."""
        service = UserService(db_session)

        await service.update_default_prompt_settings(
            user=test_user,
            default_prompt_template="Custom prompt {tone_instructions}",
            default_tone="viral",
        )
        await db_session.commit()

        assert test_user.default_prompt_template == "Custom prompt {tone_instructions}"
        assert test_user.default_tone == "viral"

    @pytest.mark.asyncio
    async def test_update_default_prompt_settings_tone_only(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test updating only tone setting."""
        service = UserService(db_session)
        original_prompt = test_user.default_prompt_template

        await service.update_default_prompt_settings(
            user=test_user,
            default_tone="casual",
        )
        await db_session.commit()

        assert test_user.default_prompt_template == original_prompt
        assert test_user.default_tone == "casual"

    @pytest.mark.asyncio
    async def test_store_tavily_api_key(self, db_session: AsyncSession, test_user: User):
        """Test storing Tavily API key."""
        service = UserService(db_session)

        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.TAVILY,
            api_key="tvly-test-key-abcdef",
        )
        await db_session.commit()

        key = await service.get_api_key(test_user.id, APIKeyType.TAVILY)
        assert key is not None
        assert key.key_type == APIKeyType.TAVILY
        assert "cdef" in key.key_hint  # key_hint shows last 4 chars

    @pytest.mark.asyncio
    async def test_multiple_api_keys_different_types(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test storing multiple API keys of different types."""
        service = UserService(db_session)

        # Store DeepSeek key
        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.DEEPSEEK,
            api_key="sk-deepseek-key",
        )

        # Store Tavily key
        await service.store_api_key(
            user=test_user,
            key_type=APIKeyType.TAVILY,
            api_key="tvly-tavily-key",
        )
        await db_session.commit()

        deepseek_key = await service.get_api_key(test_user.id, APIKeyType.DEEPSEEK)
        tavily_key = await service.get_api_key(test_user.id, APIKeyType.TAVILY)

        assert deepseek_key is not None
        assert tavily_key is not None
        assert deepseek_key.key_type == APIKeyType.DEEPSEEK
        assert tavily_key.key_type == APIKeyType.TAVILY
