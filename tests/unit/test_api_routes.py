"""Tests for API routes."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User


class TestHomeRoute:
    """Tests for home page route."""

    def test_home_page_unauthenticated(self, app_client: TestClient):
        """Test home page shows landing page when not authenticated."""
        response = app_client.get("/")

        assert response.status_code == 200
        assert "Tempus" in response.text


class TestAuthRoutes:
    """Tests for authentication routes."""

    def test_login_page(self, app_client: TestClient):
        """Test login page renders."""
        response = app_client.get("/login")

        assert response.status_code == 200
        assert "Sign in" in response.text or "Log in" in response.text

    def test_register_page(self, app_client: TestClient):
        """Test register page renders."""
        response = app_client.get("/register")

        assert response.status_code == 200
        assert "Create" in response.text or "Sign up" in response.text

    @pytest.mark.asyncio
    async def test_login_success(
        self, async_client: AsyncClient, test_user: User, db_session: AsyncSession
    ):
        """Test successful login."""
        # Get CSRF token first
        response = await async_client.get("/login")
        # Extract csrf_token from cookies
        csrf_token = response.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/login",
            data={
                "email": "test@example.com",
                "password": "TestPass123!",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, async_client: AsyncClient):
        """Test login with invalid credentials."""
        response = await async_client.get("/login")
        csrf_token = response.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/login",
            data={
                "email": "nonexistent@example.com",
                "password": "WrongPassword!",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_register_success(self, async_client: AsyncClient):
        """Test successful registration."""
        response = await async_client.get("/register")
        csrf_token = response.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/register",
            data={
                "email": "newregistered@example.com",
                "password": "NewPass123!",
                "confirm_password": "NewPass123!",
                "full_name": "New Registered User",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_register_password_mismatch(self, async_client: AsyncClient):
        """Test registration with mismatched passwords."""
        response = await async_client.get("/register")
        csrf_token = response.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/register",
            data={
                "email": "mismatch@example.com",
                "password": "Password123!",
                "confirm_password": "DifferentPassword123!",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302


class TestDashboardRoutes:
    """Tests for dashboard routes."""

    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self, async_client: AsyncClient):
        """Test dashboard requires authentication."""
        response = await async_client.get("/dashboard", follow_redirects=False)

        # Should redirect to login
        assert response.status_code == 302
        assert "/login" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_dashboard_authenticated(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test dashboard loads for authenticated user."""
        response = await async_client.get("/dashboard", headers=auth_headers)

        assert response.status_code == 200
        assert "Dashboard" in response.text


class TestTweetRoutes:
    """Tests for tweet routes."""

    @pytest.mark.asyncio
    async def test_new_tweet_page(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test new tweet page loads."""
        response = await async_client.get("/tweets/new", headers=auth_headers)

        assert response.status_code == 200
        assert "Schedule" in response.text

    @pytest.mark.asyncio
    async def test_new_tweet_page_with_content(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test new tweet page pre-fills content from query param."""
        response = await async_client.get(
            "/tweets/new?content=Pre-filled%20content",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "Pre-filled content" in response.text

    @pytest.mark.asyncio
    async def test_schedule_tweet(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test scheduling a tweet."""
        # Get page for CSRF token
        page = await async_client.get("/tweets/new", headers=auth_headers)
        csrf_token = page.cookies.get("csrf_token", "test-csrf")

        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            headers=auth_headers,
            data={
                "content": "Test scheduled tweet from tests",
                "scheduled_date": future_date,
                "scheduled_time": "12:00",
                "user_timezone": "UTC",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302


class TestGenerateRoutes:
    """Tests for AI generation routes."""

    @pytest.mark.asyncio
    async def test_generate_page_requires_auth(self, async_client: AsyncClient):
        """Test generate page requires authentication."""
        response = await async_client.get("/generate", follow_redirects=False)

        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_generate_page_loads(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test generate page loads for authenticated user."""
        response = await async_client.get("/generate", headers=auth_headers)

        assert response.status_code == 200
        assert "Generate" in response.text

    @pytest.mark.asyncio
    async def test_generate_tweet_without_api_key(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test generating tweet without API key shows error."""
        page = await async_client.get("/generate", headers=auth_headers)
        csrf_token = page.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/generate/tweet",
            headers=auth_headers,
            data={
                "prompt": "Write a tweet about testing",
                "tone": "professional",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302


class TestSettingsRoutes:
    """Tests for settings routes."""

    @pytest.mark.asyncio
    async def test_settings_page_requires_auth(self, async_client: AsyncClient):
        """Test settings page requires authentication."""
        response = await async_client.get("/settings", follow_redirects=False)

        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_settings_page_loads(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test settings page loads for authenticated user."""
        response = await async_client.get("/settings", headers=auth_headers)

        assert response.status_code == 200
        assert "Settings" in response.text

    @pytest.mark.asyncio
    async def test_update_profile(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test updating user profile."""
        page = await async_client.get("/settings", headers=auth_headers)
        csrf_token = page.cookies.get("csrf_token", "test-csrf")

        response = await async_client.post(
            "/settings/profile",
            headers=auth_headers,
            data={
                "full_name": "Updated Name",
                "timezone": "America/New_York",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302


class TestAdminRoutes:
    """Tests for admin routes."""

    @pytest.mark.asyncio
    async def test_admin_requires_admin_role(
        self, async_client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test admin routes require admin role."""
        response = await async_client.get("/admin", headers=auth_headers, follow_redirects=False)

        # Regular user should be forbidden
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_admin_accessible_by_admin(
        self, async_client: AsyncClient, admin_user: User, admin_auth_headers: dict
    ):
        """Test admin routes accessible by admin."""
        response = await async_client.get("/admin", headers=admin_auth_headers)

        assert response.status_code == 200
        assert "Admin" in response.text


class TestHealthRoute:
    """Tests for health check route."""

    def test_health_check(self, app_client: TestClient):
        """Test health check endpoint."""
        response = app_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestErrorHandling:
    """Tests for error handling."""

    def test_404_error_page(self, app_client: TestClient):
        """Test 404 error page."""
        response = app_client.get("/nonexistent-page")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_401_redirects_to_login(self, async_client: AsyncClient):
        """Test 401 errors redirect to login for HTML requests."""
        response = await async_client.get(
            "/dashboard",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/login" in response.headers.get("location", "")
