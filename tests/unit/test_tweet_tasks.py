"""Tests for tweet Celery tasks."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User
from app.tasks.tweet_tasks import (
    _post_scheduled_tweet_async,
    _process_pending_tweets_async,
    _retry_failed_tweet_async,
)


@pytest.fixture
async def pending_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a pending tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Pending tweet content",
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
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
async def cancelled_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a cancelled tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Cancelled tweet content",
        scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
        status=TweetStatus.CANCELLED,
        timezone="UTC",
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


class TestPostScheduledTweet:
    """Tests for post_scheduled_tweet task."""

    @pytest.mark.asyncio
    async def test_post_tweet_not_found(self):
        """Test posting non-existent tweet."""
        mock_task = MagicMock()

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _post_scheduled_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is False
            assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_post_tweet_already_posted(self):
        """Test posting already posted tweet."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.status = TweetStatus.POSTED

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _post_scheduled_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is True
            assert "already" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_post_tweet_cancelled(self):
        """Test posting cancelled tweet."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.status = TweetStatus.CANCELLED

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _post_scheduled_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is False
            assert "cancelled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_post_tweet_no_access_token(self):
        """Test posting tweet without Twitter access token."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.status = TweetStatus.PENDING
        mock_tweet.user_id = uuid4()
        mock_tweet.id = uuid4()
        mock_tweet.is_thread = False

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.tasks.tweet_tasks.TweetService") as mock_tweet_svc:
                mock_tweet_svc.return_value.create_execution_log = AsyncMock(
                    return_value=MagicMock()
                )

                with patch("app.tasks.tweet_tasks.TwitterService") as mock_twitter:
                    mock_twitter_instance = AsyncMock()
                    mock_twitter.return_value = mock_twitter_instance
                    mock_twitter_instance.get_valid_access_token = AsyncMock(return_value=None)
                    mock_twitter_instance.close = AsyncMock()

                    with patch("app.tasks.tweet_tasks.AuditService") as mock_audit:
                        mock_audit_instance = AsyncMock()
                        mock_audit.return_value = mock_audit_instance
                        mock_audit_instance.log_tweet_failed = AsyncMock()

                        result = await _post_scheduled_tweet_async(mock_task, str(mock_tweet.id))

                        assert result["success"] is False
                        assert "token" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_post_tweet_success(self):
        """Test successful tweet posting."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.status = TweetStatus.PENDING
        mock_tweet.user_id = uuid4()
        mock_tweet.id = uuid4()
        mock_tweet.is_thread = False
        mock_tweet.content = "Test tweet"

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.tasks.tweet_tasks.TweetService") as mock_tweet_svc:
                mock_exec_log = MagicMock()
                mock_tweet_svc.return_value.create_execution_log = AsyncMock(
                    return_value=mock_exec_log
                )

                with patch("app.tasks.tweet_tasks.TwitterService") as mock_twitter:
                    mock_twitter_instance = AsyncMock()
                    mock_twitter.return_value = mock_twitter_instance
                    mock_twitter_instance.get_valid_access_token = AsyncMock(
                        return_value="access_token"
                    )
                    mock_twitter_instance.post_tweet = AsyncMock(
                        return_value={"data": {"id": "12345"}}
                    )
                    mock_twitter_instance.close = AsyncMock()

                    with patch("app.tasks.tweet_tasks.AuditService") as mock_audit:
                        mock_audit_instance = AsyncMock()
                        mock_audit.return_value = mock_audit_instance
                        mock_audit_instance.log_tweet_posted = AsyncMock()

                        result = await _post_scheduled_tweet_async(mock_task, str(mock_tweet.id))

                        assert result["success"] is True
                        assert result["twitter_tweet_id"] == "12345"

    @pytest.mark.asyncio
    async def test_post_thread_success(self):
        """Test successful thread posting."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.status = TweetStatus.PENDING
        mock_tweet.user_id = uuid4()
        mock_tweet.id = uuid4()
        mock_tweet.is_thread = True
        mock_tweet.thread_contents = ["Tweet 1", "Tweet 2", "Tweet 3"]

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.tasks.tweet_tasks.TweetService") as mock_tweet_svc:
                mock_exec_log = MagicMock()
                mock_tweet_svc.return_value.create_execution_log = AsyncMock(
                    return_value=mock_exec_log
                )

                with patch("app.tasks.tweet_tasks.TwitterService") as mock_twitter:
                    mock_twitter_instance = AsyncMock()
                    mock_twitter.return_value = mock_twitter_instance
                    mock_twitter_instance.get_valid_access_token = AsyncMock(
                        return_value="access_token"
                    )
                    mock_twitter_instance.post_thread = AsyncMock(
                        return_value=[
                            {"data": {"id": "1"}},
                            {"data": {"id": "2"}},
                            {"data": {"id": "3"}},
                        ]
                    )
                    mock_twitter_instance.close = AsyncMock()

                    with patch("app.tasks.tweet_tasks.AuditService") as mock_audit:
                        mock_audit_instance = AsyncMock()
                        mock_audit.return_value = mock_audit_instance
                        mock_audit_instance.log_tweet_posted = AsyncMock()

                        result = await _post_scheduled_tweet_async(mock_task, str(mock_tweet.id))

                        assert result["success"] is True


class TestProcessPendingTweets:
    """Tests for process_pending_tweets task."""

    @pytest.mark.asyncio
    async def test_no_pending_tweets(self):
        """Test processing when no pending tweets."""
        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch("app.tasks.tweet_tasks.TweetService") as mock_svc:
                mock_svc.return_value.get_pending_tweets = AsyncMock(return_value=[])

                result = await _process_pending_tweets_async()

                assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_process_pending_tweets(self):
        """Test processing pending tweets."""
        mock_tweets = [
            MagicMock(id=uuid4()),
            MagicMock(id=uuid4()),
            MagicMock(id=uuid4()),
        ]

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch("app.tasks.tweet_tasks.TweetService") as mock_svc:
                mock_svc.return_value.get_pending_tweets = AsyncMock(return_value=mock_tweets)

                with patch("app.tasks.tweet_tasks.post_scheduled_tweet") as mock_task:
                    mock_task.delay = MagicMock()

                    result = await _process_pending_tweets_async()

                    assert result["processed"] == 3
                    assert mock_task.delay.call_count == 3


class TestRetryFailedTweet:
    """Tests for retry_failed_tweet task."""

    @pytest.mark.asyncio
    async def test_retry_tweet_not_found(self):
        """Test retrying non-existent tweet."""
        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _retry_failed_tweet_async(str(uuid4()))

            assert result["success"] is False
            assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_retry_tweet_cannot_retry(self):
        """Test retrying tweet that cannot be retried."""
        mock_tweet = MagicMock()
        mock_tweet.can_retry = False

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _retry_failed_tweet_async(str(uuid4()))

            assert result["success"] is False
            assert "cannot be retried" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_retry_tweet_success(self):
        """Test successful tweet retry."""
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.can_retry = True

        with patch("app.tasks.tweet_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.tasks.tweet_tasks.post_scheduled_tweet") as mock_task:
                mock_task.delay = MagicMock()

                result = await _retry_failed_tweet_async(str(mock_tweet.id))

                assert result["success"] is True
                mock_task.delay.assert_called_once()
