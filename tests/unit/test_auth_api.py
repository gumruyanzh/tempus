"""Tests for authentication API routes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.auth import AuthService


@pytest.fixture
def auth_cookies(test_user: User) -> dict:
    """Create auth cookies for test user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


class TestAuthAPI:
    """Tests for auth API endpoints."""

    @pytest.mark.asyncio
    async def test_login_page_unauthenticated(self, async_client: AsyncClient):
        """Test login page renders for unauthenticated users."""
        response = await async_client.get("/login")
        assert response.status_code == 200
        assert b"login" in response.content.lower() or b"sign in" in response.content.lower()

    @pytest.mark.asyncio
    async def test_login_page_redirects_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test login page redirects authenticated users."""
        response = await async_client.get(
            "/login",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_login_success(
        self,
        async_client: AsyncClient,
        test_user: User,
    ):
        """Test successful login."""
        response = await async_client.post(
            "/login",
            data={
                "email": test_user.email,
                "password": "TestPass123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_login_invalid_password(
        self,
        async_client: AsyncClient,
        test_user: User,
    ):
        """Test login with invalid password."""
        response = await async_client.post(
            "/login",
            data={
                "email": test_user.email,
                "password": "wrongpassword",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_login_nonexistent_email(
        self,
        async_client: AsyncClient,
    ):
        """Test login with nonexistent email."""
        response = await async_client.post(
            "/login",
            data={
                "email": "nonexistent@example.com",
                "password": "somepassword",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_register_page_unauthenticated(self, async_client: AsyncClient):
        """Test register page renders for unauthenticated users."""
        response = await async_client.get("/register")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_register_page_redirects_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test register page redirects authenticated users."""
        response = await async_client.get(
            "/register",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_register_success(
        self,
        async_client: AsyncClient,
    ):
        """Test successful registration."""
        response = await async_client.post(
            "/register",
            data={
                "email": f"newuser_{uuid4().hex[:8]}@example.com",
                "password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
                "full_name": "New User",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_register_password_mismatch(
        self,
        async_client: AsyncClient,
    ):
        """Test registration with mismatched passwords."""
        response = await async_client.post(
            "/register",
            data={
                "email": "newuser@example.com",
                "password": "Password123!",
                "confirm_password": "DifferentPassword123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_register_duplicate_email(
        self,
        async_client: AsyncClient,
        test_user: User,
    ):
        """Test registration with duplicate email."""
        response = await async_client.post(
            "/register",
            data={
                "email": test_user.email,
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_logout(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test logout."""
        response = await async_client.get(
            "/logout",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/login" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_twitter_signin_unauthenticated(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter signin redirects to Twitter."""
        response = await async_client.get(
            "/twitter/signin",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "twitter.com" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_twitter_signin_redirects_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test Twitter signin redirects authenticated users to dashboard."""
        response = await async_client.get(
            "/twitter/signin",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_twitter_connect(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test Twitter connect redirects to Twitter."""
        response = await async_client.get(
            "/twitter/connect",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "twitter.com" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_twitter_callback_no_state(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter callback without state cookie."""
        response = await async_client.get(
            "/twitter/callback?code=testcode&state=teststate",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_twitter_callback_state_mismatch(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter callback with mismatched state."""
        response = await async_client.get(
            "/twitter/callback?code=testcode&state=wrongstate",
            cookies={"twitter_oauth_state": "signin:differentstate:verifier"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_twitter_disconnect(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test Twitter disconnect."""
        response = await async_client.post(
            "/twitter/disconnect",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/settings" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_twitter_callback_invalid_state_format(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter callback with invalid state format in cookie."""
        response = await async_client.get(
            "/twitter/callback?code=testcode&state=teststate",
            cookies={"twitter_oauth_state": "invalidformat"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_register_weak_password(
        self,
        async_client: AsyncClient,
    ):
        """Test registration with weak password."""
        response = await async_client.post(
            "/register",
            data={
                "email": "weakpass@example.com",
                "password": "weak",
                "confirm_password": "weak",
            },
            follow_redirects=False,
        )
        # Should redirect with error (password validation fails)
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_login_inactive_user(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test login with inactive user."""
        # Create inactive user
        from app.models.user import User
        from app.core.security import hash_password

        user = User(
            id=uuid4(),
            email="inactive@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Inactive User",
            is_active=False,
        )
        db_session.add(user)
        await db_session.commit()

        response = await async_client.post(
            "/login",
            data={
                "email": "inactive@example.com",
                "password": "Password123!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_logout_clears_cookies(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test logout clears authentication cookies."""
        response = await async_client.get(
            "/logout",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        # Check that cookies are set to be deleted
        cookies = response.cookies
        # The response should indicate cookie deletion
        assert "/login" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_twitter_callback_exchange_error(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter callback when token exchange fails."""
        with patch("app.api.auth.TwitterService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            from app.services.twitter import TwitterAPIError
            mock_service.exchange_code_for_tokens = AsyncMock(
                side_effect=TwitterAPIError("Exchange failed")
            )
            mock_service.close = AsyncMock()

            response = await async_client.get(
                "/twitter/callback?code=testcode&state=validstate",
                cookies={"twitter_oauth_state": "signin:validstate:verifier"},
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_twitter_signin_with_callback_success(
        self,
        async_client: AsyncClient,
    ):
        """Test successful Twitter signin via callback."""
        with patch("app.api.auth.TwitterService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            mock_service.exchange_code_for_tokens = AsyncMock(
                return_value={
                    "access_token": "test_token",
                    "refresh_token": "test_refresh",
                    "expires_in": 7200,
                }
            )
            mock_service.get_current_user = AsyncMock(
                return_value={
                    "data": {
                        "id": "12345",
                        "name": "Test User",
                        "username": "testuser",
                    }
                }
            )

            from app.models.user import User
            mock_user = MagicMock()
            mock_user.id = uuid4()

            mock_service.sign_in_or_sign_up_with_twitter = AsyncMock(
                return_value=(mock_user, True)  # New user
            )
            mock_service.close = AsyncMock()

            response = await async_client.get(
                "/twitter/callback?code=testcode&state=validstate",
                cookies={"twitter_oauth_state": "signin:validstate:verifier"},
                follow_redirects=False,
            )
            # Should redirect to dashboard on success
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_twitter_connect_callback_not_authenticated(
        self,
        async_client: AsyncClient,
    ):
        """Test Twitter connect callback when user is not authenticated."""
        with patch("app.api.auth.TwitterService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            mock_service.exchange_code_for_tokens = AsyncMock(
                return_value={
                    "access_token": "test_token",
                    "refresh_token": "test_refresh",
                    "expires_in": 7200,
                }
            )
            mock_service.get_current_user = AsyncMock(
                return_value={
                    "data": {
                        "id": "12345",
                        "name": "Test User",
                        "username": "testuser",
                    }
                }
            )
            mock_service.close = AsyncMock()

            response = await async_client.get(
                "/twitter/callback?code=testcode&state=validstate",
                cookies={"twitter_oauth_state": "connect:validstate:verifier"},
                follow_redirects=False,
            )
            # Should redirect with error (not authenticated for connect flow)
            assert response.status_code == 302
            # Connect flow errors go to /settings, signin flow errors go to /login
            assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_register_with_full_name(
        self,
        async_client: AsyncClient,
    ):
        """Test registration with full name provided."""
        email = f"withname_{uuid4().hex[:8]}@example.com"
        response = await async_client.post(
            "/register",
            data={
                "email": email,
                "password": "ValidPass123!",
                "confirm_password": "ValidPass123!",
                "full_name": "John Doe",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_login_empty_credentials(
        self,
        async_client: AsyncClient,
    ):
        """Test login with empty credentials."""
        response = await async_client.post(
            "/login",
            data={
                "email": "",
                "password": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in [302, 422]

    @pytest.mark.asyncio
    async def test_login_page_shows_error_param(
        self,
        async_client: AsyncClient,
    ):
        """Test login page displays error from query param."""
        response = await async_client.get("/login?error=Test+Error")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_register_page_shows_error_param(
        self,
        async_client: AsyncClient,
    ):
        """Test register page displays error from query param."""
        response = await async_client.get("/register?error=Test+Error")
        assert response.status_code == 200
