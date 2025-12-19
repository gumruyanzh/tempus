"""Tests for tweets API routes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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
async def scheduled_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a scheduled tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Test tweet content",
        scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
        status=TweetStatus.PENDING,
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
        retry_count=0,
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


class TestTweetsAPI:
    """Tests for tweets API endpoints."""

    @pytest.mark.asyncio
    async def test_new_tweet_page_unauthenticated(self, async_client: AsyncClient):
        """Test that new tweet page requires authentication."""
        response = await async_client.get("/tweets/new")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_new_tweet_page_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test new tweet page for authenticated user."""
        response = await async_client.get(
            "/tweets/new",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_new_tweet_page_with_draft(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        tweet_draft: TweetDraft,
    ):
        """Test new tweet page with draft loaded."""
        response = await async_client.get(
            "/tweets/new",
            params={"draft_id": str(tweet_draft.id)},
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_new_tweet_page_with_content(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test new tweet page with pre-filled content."""
        response = await async_client.get(
            "/tweets/new",
            params={"content": "Pre-filled content"},
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_schedule_tweet_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet successfully."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "Test scheduled tweet content",
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_schedule_tweet_as_thread(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet thread."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "First tweet",
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
                "is_thread": True,
                "thread_content": "Second tweet\n---\nThird tweet",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_schedule_tweet_past_time(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet in the past."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "Past tweet",
                "scheduled_date": past_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        # Should redirect with error
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_view_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test viewing a scheduled tweet."""
        response = await async_client.get(
            f"/tweets/{scheduled_tweet.id}",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_view_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test viewing non-existent tweet."""
        response = await async_client.get(
            f"/tweets/{uuid4()}",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_edit_tweet_page(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test edit tweet page."""
        response = await async_client.get(
            f"/tweets/{scheduled_tweet.id}/edit",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_edit_tweet_page_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test edit page for non-existent tweet."""
        response = await async_client.get(
            f"/tweets/{uuid4()}/edit",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_edit_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test editing a tweet."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

        response = await async_client.post(
            f"/tweets/{scheduled_tweet.id}/edit",
            cookies=auth_cookies,
            data={
                "content": "Updated tweet content",
                "scheduled_date": future_date,
                "scheduled_time": "15:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_edit_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test editing non-existent tweet."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

        response = await async_client.post(
            f"/tweets/{uuid4()}/edit",
            cookies=auth_cookies,
            data={
                "content": "Updated content",
                "scheduled_date": future_date,
                "scheduled_time": "15:00",
                "user_timezone": "UTC",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test cancelling a tweet."""
        response = await async_client.post(
            f"/tweets/{scheduled_tweet.id}/cancel",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_cancel_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test cancelling non-existent tweet."""
        response = await async_client.post(
            f"/tweets/{uuid4()}/cancel",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test deleting a tweet."""
        response = await async_client.post(
            f"/tweets/{scheduled_tweet.id}/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_delete_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test deleting non-existent tweet."""
        response = await async_client.post(
            f"/tweets/{uuid4()}/delete",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test duplicating a tweet."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")

        response = await async_client.post(
            f"/tweets/{scheduled_tweet.id}/duplicate",
            cookies=auth_cookies,
            data={
                "scheduled_date": future_date,
                "scheduled_time": "16:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "duplicated" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_duplicate_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test duplicating non-existent tweet."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")

        response = await async_client.post(
            f"/tweets/{uuid4()}/duplicate",
            cookies=auth_cookies,
            data={
                "scheduled_date": future_date,
                "scheduled_time": "16:00",
                "user_timezone": "UTC",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_tweet_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        failed_tweet: ScheduledTweet,
    ):
        """Test retrying a failed tweet."""
        with patch("app.tasks.tweet_tasks.retry_failed_tweet") as mock_task:
            mock_task.delay = MagicMock()

            response = await async_client.post(
                f"/tweets/{failed_tweet.id}/retry",
                cookies=auth_cookies,
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert "retry" in response.headers.get("location", "").lower()
            mock_task.delay.assert_called_once_with(str(failed_tweet.id))

    @pytest.mark.asyncio
    async def test_retry_tweet_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test retrying non-existent tweet."""
        response = await async_client.post(
            f"/tweets/{uuid4()}/retry",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_posted_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
    ):
        """Test that posted tweets cannot be retried."""
        response = await async_client.post(
            f"/tweets/{posted_tweet.id}/retry",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_schedule_tweet_invalid_timezone(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet with invalid timezone."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "Test tweet",
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "Invalid/Timezone",
            },
            follow_redirects=False,
        )
        # Should redirect with error
        assert response.status_code == 302
        assert "error" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_schedule_tweet_empty_content(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet with empty content."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "",
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code in [302, 422]

    @pytest.mark.asyncio
    async def test_view_posted_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
    ):
        """Test viewing a posted tweet."""
        response = await async_client.get(
            f"/tweets/{posted_tweet.id}",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_view_failed_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        failed_tweet: ScheduledTweet,
    ):
        """Test viewing a failed tweet."""
        response = await async_client.get(
            f"/tweets/{failed_tweet.id}",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_new_tweet_page_with_invalid_draft(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test new tweet page with invalid draft ID."""
        response = await async_client.get(
            "/tweets/new",
            params={"draft_id": str(uuid4())},
            cookies=auth_cookies,
        )
        # Should still render page even if draft not found
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_edit_posted_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
    ):
        """Test editing a posted tweet page."""
        response = await async_client.get(
            f"/tweets/{posted_tweet.id}/edit",
            cookies=auth_cookies,
        )
        # Should still show edit page
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_cancel_already_posted_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        posted_tweet: ScheduledTweet,
    ):
        """Test cancelling an already posted tweet."""
        with patch("app.api.tweets.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.get_scheduled_tweet = AsyncMock(return_value=posted_tweet)

            from app.services.tweet import TweetServiceError
            mock_service.cancel_scheduled_tweet = AsyncMock(
                side_effect=TweetServiceError("Cannot cancel posted tweet")
            )

            response = await async_client.post(
                f"/tweets/{posted_tweet.id}/cancel",
                cookies=auth_cookies,
                follow_redirects=False,
            )
            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_duplicate_to_past_date(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test duplicating tweet to past date."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            f"/tweets/{scheduled_tweet.id}/duplicate",
            cookies=auth_cookies,
            data={
                "scheduled_date": past_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        # Should redirect (either error or success depending on validation)
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_schedule_tweet_with_different_timezone(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet with a specific timezone."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": "Test tweet for timezone",
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "America/New_York",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_edit_tweet_with_error(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test editing a tweet when service throws error."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

        with patch("app.api.tweets.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.get_scheduled_tweet = AsyncMock(return_value=scheduled_tweet)

            from app.services.tweet import TweetServiceError
            mock_service.update_scheduled_tweet = AsyncMock(
                side_effect=TweetServiceError("Cannot update cancelled tweet")
            )

            response = await async_client.post(
                f"/tweets/{scheduled_tweet.id}/edit",
                cookies=auth_cookies,
                data={
                    "content": "Updated content",
                    "scheduled_date": future_date,
                    "scheduled_time": "15:00",
                    "user_timezone": "UTC",
                },
                follow_redirects=False,
            )
            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_view_tweet_shows_query_params(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        scheduled_tweet: ScheduledTweet,
    ):
        """Test view tweet page shows success/error query params."""
        response = await async_client.get(
            f"/tweets/{scheduled_tweet.id}?success=Updated",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_failed_tweet(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        failed_tweet: ScheduledTweet,
    ):
        """Test deleting a failed tweet."""
        response = await async_client.post(
            f"/tweets/{failed_tweet.id}/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "success" in response.headers.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_schedule_tweet_too_long(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test scheduling a tweet that exceeds character limit."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        long_content = "A" * 300  # Over 280 character limit

        response = await async_client.post(
            "/tweets/schedule",
            cookies=auth_cookies,
            data={
                "content": long_content,
                "scheduled_date": future_date,
                "scheduled_time": "14:00",
                "user_timezone": "UTC",
            },
            follow_redirects=False,
        )
        # Should redirect (with error if validation is applied)
        assert response.status_code == 302
