"""Tests for settings API routes."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value
from app.models.user import APIKeyType, EncryptedAPIKey, User
from app.services.auth import AuthService


@pytest.fixture
def auth_cookies(test_user: User) -> dict:
    """Create auth cookies for test user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
async def deepseek_api_key(db_session: AsyncSession, test_user: User) -> EncryptedAPIKey:
    """Create a DeepSeek API key for the test user."""
    key = EncryptedAPIKey(
        id=uuid4(),
        user_id=test_user.id,
        key_type=APIKeyType.DEEPSEEK,
        encrypted_key=encrypt_value("sk-test-deepseek-key"),
        key_hint="key",
        is_valid=True,
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)
    return key


@pytest.fixture
async def tavily_api_key(db_session: AsyncSession, test_user: User) -> EncryptedAPIKey:
    """Create a Tavily API key for the test user."""
    key = EncryptedAPIKey(
        id=uuid4(),
        user_id=test_user.id,
        key_type=APIKeyType.TAVILY,
        encrypted_key=encrypt_value("tvly-test-tavily-key"),
        key_hint="key",
        is_valid=True,
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)
    return key


class TestSettingsAPI:
    """Tests for settings API endpoints."""

    @pytest.mark.asyncio
    async def test_settings_page_unauthenticated(self, async_client: AsyncClient):
        """Test that settings page requires authentication."""
        response = await async_client.get("/settings")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_settings_page_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test settings page for authenticated user."""
        response = await async_client.get(
            "/settings",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_page_shows_api_keys(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test settings page shows API key info."""
        response = await async_client.get(
            "/settings",
            cookies=auth_cookies,
        )
        assert response.status_code == 200
        # Should show key hint
        assert b"key" in response.content.lower()

    @pytest.mark.asyncio
    async def test_update_profile(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating user profile."""
        response = await async_client.post(
            "/settings/profile",
            cookies=auth_cookies,
            data={
                "full_name": "New Name",
                "timezone": "America/New_York",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_profile_partial(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test partial profile update."""
        response = await async_client.post(
            "/settings/profile",
            cookies=auth_cookies,
            data={
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_change_password_mismatch(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test password change with mismatched passwords."""
        response = await async_client.post(
            "/settings/password",
            cookies=auth_cookies,
            data={
                "current_password": "OldPassword123!",
                "new_password": "NewPassword123!",
                "confirm_password": "DifferentPassword123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test password change with wrong current password."""
        response = await async_client.post(
            "/settings/password",
            cookies=auth_cookies,
            data={
                "current_password": "WrongPassword123!",
                "new_password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        # Should redirect with error

    @pytest.mark.asyncio
    async def test_update_deepseek_key_invalid(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating DeepSeek API key with invalid key."""
        with patch("app.api.settings.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.validate_api_key = AsyncMock(return_value=False)
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/settings/deepseek-key",
                cookies=auth_cookies,
                data={"api_key": "sk-invalid-key"},
                follow_redirects=False,
            )

            assert response.status_code == 302
            assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_deepseek_key_valid(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating DeepSeek API key with valid key."""
        with patch("app.api.settings.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.validate_api_key = AsyncMock(return_value=True)
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/settings/deepseek-key",
                cookies=auth_cookies,
                data={"api_key": "sk-valid-key-12345"},
                follow_redirects=False,
            )

            assert response.status_code == 302
            assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_deepseek_key_rotates_existing(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test rotating existing DeepSeek API key."""
        with patch("app.api.settings.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.validate_api_key = AsyncMock(return_value=True)
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/settings/deepseek-key",
                cookies=auth_cookies,
                data={"api_key": "sk-new-valid-key-67890"},
                follow_redirects=False,
            )

            assert response.status_code == 302
            assert "rotated" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_delete_deepseek_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test deleting DeepSeek API key."""
        response = await async_client.post(
            "/settings/deepseek-key/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_tavily_key_invalid_format(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating Tavily API key with invalid format."""
        response = await async_client.post(
            "/settings/tavily-key",
            cookies=auth_cookies,
            data={"api_key": "invalid-format-key"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_tavily_key_valid(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating Tavily API key with valid format."""
        response = await async_client.post(
            "/settings/tavily-key",
            cookies=auth_cookies,
            data={"api_key": "tvly-valid-key-abcdef"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_delete_tavily_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        tavily_api_key: EncryptedAPIKey,
    ):
        """Test deleting Tavily API key."""
        response = await async_client.post(
            "/settings/tavily-key/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_prompt_defaults(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating prompt defaults."""
        response = await async_client.post(
            "/settings/prompt-defaults",
            cookies=auth_cookies,
            data={
                "default_tone": "viral",
                "default_prompt": "Custom prompt template",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_prompt_defaults_tone_only(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating only tone setting."""
        response = await async_client.post(
            "/settings/prompt-defaults",
            cookies=auth_cookies,
            data={
                "default_tone": "casual",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_settings_page_with_success_message(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test settings page with success query param."""
        response = await async_client.get(
            "/settings?success=API+key+saved",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_page_with_error_message(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test settings page with error query param."""
        response = await async_client.get(
            "/settings?error=Invalid+key",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test successful password change."""
        response = await async_client.post(
            "/settings/password",
            cookies=auth_cookies,
            data={
                "current_password": "TestPass123!",  # Matches fixture
                "new_password": "NewPassword456!",
                "confirm_password": "NewPassword456!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_delete_nonexistent_deepseek_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test deleting DeepSeek key that doesn't exist."""
        response = await async_client.post(
            "/settings/deepseek-key/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_delete_nonexistent_tavily_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test deleting Tavily key that doesn't exist."""
        response = await async_client.post(
            "/settings/tavily-key/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_update_profile_with_empty_name(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating profile with empty name."""
        response = await async_client.post(
            "/settings/profile",
            cookies=auth_cookies,
            data={
                "full_name": "",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_settings_page_with_both_keys(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
        tavily_api_key: EncryptedAPIKey,
    ):
        """Test settings page with both API keys configured."""
        response = await async_client.get(
            "/settings",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_deepseek_key_validation_error(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating DeepSeek key when validation throws exception."""
        with patch("app.api.settings.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.validate_api_key = AsyncMock(side_effect=Exception("Connection error"))
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/settings/deepseek-key",
                cookies=auth_cookies,
                data={"api_key": "sk-test-key"},
                follow_redirects=False,
            )

            assert response.status_code == 302
            assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_update_prompt_defaults_empty(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test updating prompt defaults with minimal values."""
        response = await async_client.post(
            "/settings/prompt-defaults",
            cookies=auth_cookies,
            data={
                "default_tone": "professional",
                "default_prompt": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_update_tavily_key_rotates_existing(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        tavily_api_key: EncryptedAPIKey,
    ):
        """Test rotating existing Tavily API key."""
        response = await async_client.post(
            "/settings/tavily-key",
            cookies=auth_cookies,
            data={"api_key": "tvly-new-valid-key"},
            follow_redirects=False,
        )
        assert response.status_code == 302
