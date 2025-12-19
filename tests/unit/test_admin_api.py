"""Tests for admin API routes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditAction, AuditLog
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User, UserRole
from app.services.auth import AuthService


@pytest.fixture
def admin_cookies(admin_user: User) -> dict:
    """Create auth cookies for admin user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(admin_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
def user_cookies(test_user: User) -> dict:
    """Create auth cookies for regular user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Create an admin user."""
    user = User(
        id=uuid4(),
        email="admin@example.com",
        hashed_password="hashed_password",
        full_name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def another_user(db_session: AsyncSession) -> User:
    """Create another regular user."""
    user = User(
        id=uuid4(),
        email="another@example.com",
        hashed_password="hashed_password",
        full_name="Another User",
        role=UserRole.USER,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def scheduled_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a scheduled tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Test tweet content",
        scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
        status=TweetStatus.PENDING,
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest.fixture
async def audit_log(db_session: AsyncSession, test_user: User) -> AuditLog:
    """Create an audit log entry."""
    log = AuditLog(
        id=uuid4(),
        user_id=test_user.id,
        action=AuditAction.USER_LOGIN,
        resource_type="user",
        details={"test": "data"},
        ip_address="127.0.0.1",
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


class TestAdminAPI:
    """Tests for admin API endpoints."""

    @pytest.mark.asyncio
    async def test_admin_dashboard_unauthenticated(self, async_client: AsyncClient):
        """Test that admin dashboard requires authentication."""
        response = await async_client.get("/admin")
        assert response.status_code in [302, 401, 403]

    @pytest.mark.asyncio
    async def test_admin_dashboard_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
    ):
        """Test that regular users cannot access admin dashboard."""
        response = await async_client.get(
            "/admin",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_admin_dashboard_authenticated(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin dashboard for admin user."""
        response = await async_client.get(
            "/admin",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_dashboard_shows_stats(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test admin dashboard shows statistics."""
        response = await async_client.get(
            "/admin",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_users_list(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
    ):
        """Test admin users list page."""
        response = await async_client.get(
            "/admin/users",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_users_list_with_search(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
    ):
        """Test admin users list with search."""
        response = await async_client.get(
            "/admin/users",
            params={"search": test_user.email},
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_users_list_pagination(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin users list pagination."""
        response = await async_client.get(
            "/admin/users",
            params={"page": 2},
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_user_detail(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
    ):
        """Test admin user detail page."""
        response = await async_client.get(
            f"/admin/users/{test_user.id}",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_user_detail_not_found(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin user detail for non-existent user."""
        response = await async_client.get(
            f"/admin/users/{uuid4()}",
            cookies=admin_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_user_active_deactivate(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        another_user: User,
    ):
        """Test deactivating a user."""
        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-active",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "deactivated" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_toggle_user_active_cannot_self(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test that admin cannot deactivate themselves."""
        response = await async_client.post(
            f"/admin/users/{admin_user.id}/toggle-active",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_toggle_user_active_not_found(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test toggling non-existent user."""
        response = await async_client.post(
            f"/admin/users/{uuid4()}/toggle-active",
            cookies=admin_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_user_role_to_admin(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        another_user: User,
    ):
        """Test promoting user to admin."""
        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-role",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "admin" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_toggle_user_role_cannot_self(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test that admin cannot change own role."""
        response = await async_client.post(
            f"/admin/users/{admin_user.id}/toggle-role",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_toggle_user_role_not_found(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test toggling role for non-existent user."""
        response = await async_client.post(
            f"/admin/users/{uuid4()}/toggle-role",
            cookies=admin_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_audit_logs(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        audit_log: AuditLog,
    ):
        """Test admin audit logs page."""
        response = await async_client.get(
            "/admin/audit-logs",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_audit_logs_with_filter(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        audit_log: AuditLog,
    ):
        """Test admin audit logs with action filter."""
        response = await async_client.get(
            "/admin/audit-logs",
            params={"action_filter": "user_login"},
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_audit_logs_pagination(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin audit logs pagination."""
        response = await async_client.get(
            "/admin/audit-logs",
            params={"page": 2},
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_audit_logs_invalid_filter(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin audit logs with invalid filter."""
        response = await async_client.get(
            "/admin/audit-logs",
            params={"action_filter": "invalid_action"},
            cookies=admin_cookies,
        )
        # Should still return 200 (invalid filter is ignored)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_toggle_user_active_activate(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        another_user: User,
        db_session: AsyncSession,
    ):
        """Test activating a deactivated user."""
        # First deactivate the user
        another_user.is_active = False
        await db_session.commit()

        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-active",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "activated" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_toggle_user_role_demote_admin(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        another_user: User,
        db_session: AsyncSession,
    ):
        """Test demoting admin to user."""
        # First promote to admin
        another_user.role = UserRole.ADMIN
        await db_session.commit()

        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-role",
            cookies=admin_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "user" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_admin_user_detail_with_tweets(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test admin user detail page with tweet data."""
        response = await async_client.get(
            f"/admin/users/{test_user.id}",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_dashboard_with_multiple_data(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        test_user: User,
        another_user: User,
        scheduled_tweet: ScheduledTweet,
        audit_log: AuditLog,
        db_session: AsyncSession,
    ):
        """Test admin dashboard with multiple users and data."""
        # Create additional tweets with different statuses
        posted_tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Posted tweet",
            scheduled_for=datetime.now(timezone.utc) - timedelta(days=1),
            status=TweetStatus.POSTED,
        )
        db_session.add(posted_tweet)
        await db_session.commit()

        response = await async_client.get(
            "/admin",
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_users_list_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
    ):
        """Test that regular users cannot access users list."""
        response = await async_client.get(
            "/admin/users",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_admin_user_detail_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
    ):
        """Test that regular users cannot access user details."""
        response = await async_client.get(
            f"/admin/users/{test_user.id}",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_admin_audit_logs_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
    ):
        """Test that regular users cannot access audit logs."""
        response = await async_client.get(
            "/admin/audit-logs",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_toggle_active_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
        another_user: User,
    ):
        """Test that regular users cannot toggle user active status."""
        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-active",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_toggle_role_non_admin(
        self,
        async_client: AsyncClient,
        test_user: User,
        user_cookies: dict,
        another_user: User,
    ):
        """Test that regular users cannot toggle user roles."""
        response = await async_client.post(
            f"/admin/users/{another_user.id}/toggle-role",
            cookies=user_cookies,
        )
        assert response.status_code in [302, 403]

    @pytest.mark.asyncio
    async def test_admin_users_search_no_results(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
    ):
        """Test admin users list search with no results."""
        response = await async_client.get(
            "/admin/users",
            params={"search": "nonexistentemail@nowhere.com"},
            cookies=admin_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_audit_logs_with_valid_filter(
        self,
        async_client: AsyncClient,
        admin_user: User,
        admin_cookies: dict,
        audit_log: AuditLog,
    ):
        """Test admin audit logs with valid action filter."""
        response = await async_client.get(
            "/admin/audit-logs",
            params={"action_filter": "user_login"},
            cookies=admin_cookies,
        )
        assert response.status_code == 200
