"""Tests for authentication service."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole
from app.services.auth import AuthService, AuthError


class TestAuthService:
    """Tests for AuthService."""

    @pytest.mark.asyncio
    async def test_authenticate_user_success(self, db_session: AsyncSession, test_user: User):
        """Test successful user authentication."""
        auth_service = AuthService(db_session)

        user = await auth_service.authenticate_user("test@example.com", "TestPass123!")

        assert user is not None
        assert user.id == test_user.id
        assert user.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_authenticate_user_wrong_password(self, db_session: AsyncSession, test_user: User):
        """Test authentication with wrong password."""
        auth_service = AuthService(db_session)

        user = await auth_service.authenticate_user("test@example.com", "WrongPassword123!")

        assert user is None

    @pytest.mark.asyncio
    async def test_authenticate_user_not_found(self, db_session: AsyncSession):
        """Test authentication with non-existent user."""
        auth_service = AuthService(db_session)

        user = await auth_service.authenticate_user("nonexistent@example.com", "SomePass123!")

        assert user is None

    @pytest.mark.asyncio
    async def test_authenticate_user_inactive(self, db_session: AsyncSession):
        """Test authentication with inactive user."""
        # Create inactive user
        user = User(
            id=uuid4(),
            email="inactive@example.com",
            hashed_password=hash_password("TestPass123!"),
            is_active=False,
            is_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        auth_service = AuthService(db_session)
        result = await auth_service.authenticate_user("inactive@example.com", "TestPass123!")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_tokens(self, db_session: AsyncSession, test_user: User):
        """Test JWT token creation."""
        auth_service = AuthService(db_session)

        tokens = auth_service.create_tokens(test_user)

        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert isinstance(tokens["access_token"], str)
        assert isinstance(tokens["refresh_token"], str)
        assert len(tokens["access_token"]) > 50
        assert len(tokens["refresh_token"]) > 50

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, db_session: AsyncSession, test_user: User):
        """Test getting user by email."""
        auth_service = AuthService(db_session)

        user = await auth_service.get_user_by_email("test@example.com")

        assert user is not None
        assert user.id == test_user.id

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self, db_session: AsyncSession):
        """Test getting non-existent user by email."""
        auth_service = AuthService(db_session)

        user = await auth_service.get_user_by_email("nonexistent@example.com")

        assert user is None

    @pytest.mark.asyncio
    async def test_register_user_success(self, db_session: AsyncSession):
        """Test successful user registration."""
        auth_service = AuthService(db_session)

        user = await auth_service.register_user(
            email="newuser@example.com",
            password="NewPass123!",
            full_name="New User",
        )

        assert user is not None
        assert user.email == "newuser@example.com"
        assert user.full_name == "New User"
        assert user.is_active is True
        assert user.role == UserRole.USER
        assert verify_password("NewPass123!", user.hashed_password)

    @pytest.mark.asyncio
    async def test_register_user_duplicate_email(self, db_session: AsyncSession, test_user: User):
        """Test registration with duplicate email."""
        auth_service = AuthService(db_session)

        with pytest.raises(AuthError) as exc_info:
            await auth_service.register_user(
                email="test@example.com",  # Already exists
                password="NewPass123!",
            )

        assert "already registered" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_register_user_weak_password(self, db_session: AsyncSession):
        """Test registration with weak password."""
        auth_service = AuthService(db_session)

        with pytest.raises(AuthError) as exc_info:
            await auth_service.register_user(
                email="weakpass@example.com",
                password="weak",  # Too weak
            )

        assert "password" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_change_password_success(self, db_session: AsyncSession, test_user: User):
        """Test successful password change."""
        auth_service = AuthService(db_session)

        result = await auth_service.change_password(
            user=test_user,
            current_password="TestPass123!",
            new_password="NewTestPass456!",
        )

        assert result is True

        # Verify new password works
        await db_session.refresh(test_user)
        assert verify_password("NewTestPass456!", test_user.hashed_password)

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, db_session: AsyncSession, test_user: User):
        """Test password change with wrong current password."""
        auth_service = AuthService(db_session)

        with pytest.raises(AuthError) as exc_info:
            await auth_service.change_password(
                user=test_user,
                current_password="WrongCurrent123!",
                new_password="NewTestPass456!",
            )

        assert "incorrect" in str(exc_info.value).lower()


class TestPasswordValidation:
    """Tests for password validation."""

    def test_password_hashing(self):
        """Test password hashing and verification."""
        password = "SecurePass123!"
        hashed = hash_password(password)

        assert hashed != password
        assert verify_password(password, hashed)
        assert not verify_password("WrongPassword", hashed)

    def test_password_hash_uniqueness(self):
        """Test that same password produces different hashes."""
        password = "SecurePass123!"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        # Hashes should be different due to random salt
        assert hash1 != hash2
        # But both should verify
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)


class TestTokens:
    """Tests for JWT token handling."""

    def test_access_token_decoding(self, test_user: User):
        """Test access token can be decoded."""
        from app.core.security import decode_token

        auth_service = AuthService(None)
        tokens = auth_service.create_tokens(test_user)

        payload = decode_token(tokens["access_token"])

        assert payload is not None
        assert payload["sub"] == str(test_user.id)
        assert payload["type"] == "access"

    def test_refresh_token_decoding(self, test_user: User):
        """Test refresh token can be decoded."""
        from app.core.security import decode_token

        auth_service = AuthService(None)
        tokens = auth_service.create_tokens(test_user)

        payload = decode_token(tokens["refresh_token"])

        assert payload is not None
        assert payload["sub"] == str(test_user.id)
        assert payload["type"] == "refresh"

    def test_invalid_token_returns_none(self):
        """Test that invalid token returns None."""
        from app.core.security import decode_token

        payload = decode_token("invalid-token")

        assert payload is None
