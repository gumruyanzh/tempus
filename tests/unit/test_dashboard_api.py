"""Tests for dashboard API routes."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import ScheduledTweet, TweetDraft, TweetStatus
from app.models.user import User
from app.services.auth import AuthService


@pytest.fixture
def auth_cookies(test_user: User) -> dict:
    """Create auth cookies for test user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
async def pending_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a pending tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Pending tweet content",
        scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
        status=TweetStatus.PENDING,
        timezone="UTC",
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest.fixture
async def posted_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a posted tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Posted tweet content",
        scheduled_for=datetime.now(timezone.utc) - timedelta(hours=2),
        posted_at=datetime.now(timezone.utc) - timedelta(hours=2),
        status=TweetStatus.POSTED,
        timezone="UTC",
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest.fixture
async def failed_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a failed tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Failed tweet content",
        scheduled_for=datetime.now(timezone.utc) - timedelta(hours=1),
        status=TweetStatus.FAILED,
        timezone="UTC",
        last_error="API error",
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest.fixture
async def tweet_draft(db_session: AsyncSession, test_user: User) -> TweetDraft:
    """Create a tweet draft."""
    draft = TweetDraft(
        id=uuid4(),
        user_id=test_user.id,
        content="Draft tweet content",
    )
    db_session.add(draft)
    await db_session.commit()
    await db_session.refresh(draft)
    return draft


class TestDashboardAPI:
    """Tests for dashboard API endpoints."""

    @pytest.mark.asyncio
    async def test_dashboard_unauthenticated(self, async_client: AsyncClient):
        """Test that dashboard requires authentication."""
        response = await async_client.get("/dashboard")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_dashboard_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test dashboard for authenticated user."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_shows_stats(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        pending_tweet: ScheduledTweet,
        posted_tweet: ScheduledTweet,
    ):
        """Test dashboard shows tweet statistics."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_shows_upcoming_tweets(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        pending_tweet: ScheduledTweet,
    ):
        """Test dashboard shows upcoming tweets."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200
        assert b"Pending" in response.content or pending_tweet.content.encode() in response.content

    @pytest.mark.asyncio
    async def test_dashboard_shows_failed_tweets(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        failed_tweet: ScheduledTweet,
    ):
        """Test dashboard shows failed tweets."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_empty(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test dashboard with no tweets."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tweet_history_unauthenticated(self, async_client: AsyncClient):
        """Test that tweet history requires authentication."""
        response = await async_client.get("/dashboard/history")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_tweet_history_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test tweet history for authenticated user."""
        response = await async_client.get(
            "/dashboard/history",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tweet_history_with_tweets(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
        pending_tweet: ScheduledTweet,
    ):
        """Test tweet history shows all tweets."""
        response = await async_client.get(
            "/dashboard/history",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tweet_history_pagination(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test tweet history pagination."""
        response = await async_client.get(
            "/dashboard/history",
            params={"page": 2},
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tweet_history_first_page(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
    ):
        """Test tweet history first page."""
        response = await async_client.get(
            "/dashboard/history",
            params={"page": 1},
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_drafts_unauthenticated(self, async_client: AsyncClient):
        """Test that drafts page requires authentication."""
        response = await async_client.get("/dashboard/drafts")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_drafts_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test drafts page for authenticated user."""
        response = await async_client.get(
            "/dashboard/drafts",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_drafts_with_content(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        tweet_draft: TweetDraft,
    ):
        """Test drafts page shows drafts."""
        response = await async_client.get(
            "/dashboard/drafts",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_drafts_empty(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test drafts page with no drafts."""
        response = await async_client.get(
            "/dashboard/drafts",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_with_success_message(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test dashboard with success query param."""
        response = await async_client.get(
            "/dashboard?success=Tweet+scheduled",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_with_multiple_tweet_types(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        pending_tweet: ScheduledTweet,
        posted_tweet: ScheduledTweet,
        failed_tweet: ScheduledTweet,
    ):
        """Test dashboard shows all tweet types."""
        response = await async_client.get(
            "/dashboard",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_history_with_status_filter(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test history with status filter."""
        response = await async_client.get(
            "/dashboard/history?status=posted",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_history_with_invalid_page(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test history with invalid page number."""
        response = await async_client.get(
            "/dashboard/history?page=0",
            cookies=auth_cookies,
        )
        # Should handle gracefully
        assert response.status_code in [200, 302]

    @pytest.mark.asyncio
    async def test_history_large_page(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test history with large page number."""
        response = await async_client.get(
            "/dashboard/history?page=1000",
            cookies=auth_cookies,
        )
        assert response.status_code == 200
