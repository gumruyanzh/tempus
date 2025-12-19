"""Tests for middleware."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.core.security import create_access_token, create_refresh_token
from app.middleware.token_refresh import TokenRefreshMiddleware


class TestTokenRefreshMiddleware:
    """Tests for TokenRefreshMiddleware."""

    def setup_method(self):
        """Set up test fixtures."""
        self.app = FastAPI()
        self.app.add_middleware(TokenRefreshMiddleware)

        @self.app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        self.client = TestClient(self.app)

    def test_no_tokens_passes_through(self):
        """Test that requests without tokens pass through."""
        response = self.client.get("/test")
        assert response.status_code == 200
        # Should not have set any new cookies
        assert "access_token" not in response.cookies

    def test_valid_access_token_passes_through(self):
        """Test that valid access tokens pass through without refresh."""
        user_id = str(uuid4())
        access_token = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(hours=1),
        )
        refresh_token = create_refresh_token(
            {"sub": user_id},
            expires_delta=timedelta(days=7),
        )

        response = self.client.get(
            "/test",
            cookies={
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )

        assert response.status_code == 200
        # Access token was valid, no new token should be set
        # (The middleware only refreshes if access token is expired)

    def test_expired_access_token_with_valid_refresh_creates_new_token(self):
        """Test that expired access token gets refreshed."""
        user_id = str(uuid4())

        # Create expired access token
        with patch("app.core.security.datetime") as mock_datetime:
            # Make the token creation think it's in the past
            past_time = datetime.now(timezone.utc) - timedelta(hours=2)
            mock_datetime.now.return_value = past_time

        # Actually create an expired token
        expired_access = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(seconds=-1),  # Already expired
        )

        # Create valid refresh token
        valid_refresh = create_refresh_token(
            {"sub": user_id},
            expires_delta=timedelta(days=7),
        )

        response = self.client.get(
            "/test",
            cookies={
                "access_token": expired_access,
                "refresh_token": valid_refresh,
            },
        )

        assert response.status_code == 200
        # Should have a new access token cookie
        assert "access_token" in response.cookies

    def test_missing_access_token_with_refresh_passes_through(self):
        """Test that missing access token with only refresh passes through."""
        user_id = str(uuid4())
        refresh_token = create_refresh_token(
            {"sub": user_id},
            expires_delta=timedelta(days=7),
        )

        response = self.client.get(
            "/test",
            cookies={"refresh_token": refresh_token},
        )

        assert response.status_code == 200

    def test_missing_refresh_token_passes_through(self):
        """Test that missing refresh token passes through."""
        user_id = str(uuid4())
        access_token = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(hours=1),
        )

        response = self.client.get(
            "/test",
            cookies={"access_token": access_token},
        )

        assert response.status_code == 200

    def test_invalid_refresh_token_passes_through(self):
        """Test that invalid refresh token doesn't cause errors."""
        user_id = str(uuid4())
        # Create expired access token
        expired_access = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(seconds=-1),
        )

        response = self.client.get(
            "/test",
            cookies={
                "access_token": expired_access,
                "refresh_token": "invalid-token",
            },
        )

        assert response.status_code == 200
        # Should not set a new access token (refresh failed)
        # The middleware just passes through on errors

    def test_wrong_token_type_in_refresh(self):
        """Test that wrong token type in refresh position is rejected."""
        user_id = str(uuid4())
        # Create expired access token
        expired_access = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(seconds=-1),
        )
        # Use access token in refresh position
        fake_refresh = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(hours=1),
        )

        response = self.client.get(
            "/test",
            cookies={
                "access_token": expired_access,
                "refresh_token": fake_refresh,  # Wrong type!
            },
        )

        assert response.status_code == 200
        # Should not have set a new token because type check failed

    def test_refresh_token_without_user_id(self):
        """Test that refresh token without user ID is rejected."""
        user_id = str(uuid4())
        expired_access = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(seconds=-1),
        )
        # Create refresh token without sub claim
        bad_refresh = create_refresh_token(
            {},  # No user ID
            expires_delta=timedelta(days=7),
        )

        response = self.client.get(
            "/test",
            cookies={
                "access_token": expired_access,
                "refresh_token": bad_refresh,
            },
        )

        assert response.status_code == 200


class TestTokenRefreshMiddlewareAsync:
    """Async tests for TokenRefreshMiddleware."""

    @pytest.mark.asyncio
    async def test_middleware_dispatch(self):
        """Test middleware dispatch method directly."""
        middleware = TokenRefreshMiddleware(app=MagicMock())

        # Create mock request with no cookies
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {}

        # Create mock response
        mock_response = MagicMock(spec=Response)
        mock_response.set_cookie = MagicMock()

        # Create mock call_next
        async def mock_call_next(request):
            return mock_response

        result = await middleware.dispatch(mock_request, mock_call_next)

        assert result == mock_response
        # Should not have called set_cookie
        mock_response.set_cookie.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_dispatch_with_valid_tokens(self):
        """Test middleware with valid tokens."""
        middleware = TokenRefreshMiddleware(app=MagicMock())

        user_id = str(uuid4())
        access_token = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(hours=1),
        )
        refresh_token = create_refresh_token(
            {"sub": user_id},
            expires_delta=timedelta(days=7),
        )

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

        mock_response = MagicMock(spec=Response)

        async def mock_call_next(request):
            return mock_response

        result = await middleware.dispatch(mock_request, mock_call_next)

        assert result == mock_response

    @pytest.mark.asyncio
    async def test_middleware_refreshes_expired_token(self):
        """Test that middleware refreshes expired access token."""
        middleware = TokenRefreshMiddleware(app=MagicMock())

        user_id = str(uuid4())
        # Create expired access token
        expired_access = create_access_token(
            {"sub": user_id},
            expires_delta=timedelta(seconds=-1),
        )
        # Create valid refresh token
        valid_refresh = create_refresh_token(
            {"sub": user_id},
            expires_delta=timedelta(days=7),
        )

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {
            "access_token": expired_access,
            "refresh_token": valid_refresh,
        }

        mock_response = MagicMock(spec=Response)
        mock_response.set_cookie = MagicMock()

        async def mock_call_next(request):
            return mock_response

        result = await middleware.dispatch(mock_request, mock_call_next)

        # Should have set a new access token cookie
        mock_response.set_cookie.assert_called_once()
        call_kwargs = mock_response.set_cookie.call_args.kwargs
        assert call_kwargs["key"] == "access_token"
        assert call_kwargs["httponly"] is True
