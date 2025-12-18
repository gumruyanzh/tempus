"""Tests for tweet service."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import ScheduledTweet, TweetStatus, TweetTone
from app.models.user import User
from app.services.tweet import TweetService, TweetServiceError


class TestTweetServiceCreate:
    """Tests for tweet creation."""

    @pytest.mark.asyncio
    async def test_schedule_tweet(self, db_session: AsyncSession, test_user: User):
        """Test scheduling a tweet."""
        service = TweetService(db_session)
        scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)

        tweet = await service.schedule_tweet(
            user_id=test_user.id,
            content="Test scheduled tweet #testing",
            scheduled_for=scheduled_time,
        )
        await db_session.commit()

        assert tweet is not None
        assert tweet.content == "Test scheduled tweet #testing"
        assert tweet.status == TweetStatus.PENDING
        # Compare without microseconds due to potential db rounding
        assert tweet.scheduled_for.replace(tzinfo=timezone.utc) >= scheduled_time.replace(microsecond=0)
        assert tweet.user_id == test_user.id

    @pytest.mark.asyncio
    async def test_schedule_tweet_with_thread(self, db_session: AsyncSession, test_user: User):
        """Test scheduling a tweet thread."""
        service = TweetService(db_session)
        scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)

        tweet = await service.schedule_tweet(
            user_id=test_user.id,
            content="Thread start",
            scheduled_for=scheduled_time,
            is_thread=True,
            thread_contents=["First tweet", "Second tweet", "Third tweet"],
        )
        await db_session.commit()

        assert tweet.is_thread is True
        assert tweet.thread_contents == ["First tweet", "Second tweet", "Third tweet"]

    @pytest.mark.asyncio
    async def test_create_draft(self, db_session: AsyncSession, test_user: User):
        """Test creating a draft tweet."""
        service = TweetService(db_session)

        draft = await service.create_draft(
            user_id=test_user.id,
            content="Draft tweet content",
            tone_used=TweetTone.CASUAL,
            generated_by_ai=True,
            prompt_used="Write a casual tweet about coffee",
        )
        await db_session.commit()

        assert draft is not None
        assert draft.content == "Draft tweet content"
        assert draft.generated_by_ai is True
        assert draft.prompt_used == "Write a casual tweet about coffee"


class TestTweetServiceQuery:
    """Tests for tweet queries."""

    @pytest.mark.asyncio
    async def test_get_scheduled_tweet_by_id(
        self, db_session: AsyncSession, test_user: User, scheduled_tweet: ScheduledTweet
    ):
        """Test getting a tweet by ID."""
        service = TweetService(db_session)

        tweet = await service.get_scheduled_tweet(scheduled_tweet.id, test_user.id)

        assert tweet is not None
        assert tweet.id == scheduled_tweet.id
        assert tweet.content == "Test tweet content #testing"

    @pytest.mark.asyncio
    async def test_get_scheduled_tweet_wrong_user(
        self, db_session: AsyncSession, admin_user: User, scheduled_tweet: ScheduledTweet
    ):
        """Test that users can't access other users' tweets."""
        service = TweetService(db_session)

        tweet = await service.get_scheduled_tweet(scheduled_tweet.id, admin_user.id)

        assert tweet is None

    @pytest.mark.asyncio
    async def test_get_user_scheduled_tweets(
        self, db_session: AsyncSession, test_user: User, scheduled_tweet: ScheduledTweet
    ):
        """Test getting user's scheduled tweets."""
        service = TweetService(db_session)

        tweets = await service.get_user_scheduled_tweets(
            user_id=test_user.id,
            status=TweetStatus.PENDING,
        )

        assert len(tweets) >= 1
        assert any(t.id == scheduled_tweet.id for t in tweets)

    @pytest.mark.asyncio
    async def test_get_user_scheduled_tweets_pagination(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test pagination of scheduled tweets."""
        service = TweetService(db_session)

        # Create multiple tweets
        for i in range(10):
            tweet = ScheduledTweet(
                id=uuid4(),
                user_id=test_user.id,
                content=f"Tweet {i}",
                status=TweetStatus.PENDING,
                scheduled_for=datetime.now(timezone.utc) + timedelta(hours=i + 1),
            )
            db_session.add(tweet)
        await db_session.commit()

        tweets = await service.get_user_scheduled_tweets(
            user_id=test_user.id,
            status=TweetStatus.PENDING,
            limit=5,
        )

        assert len(tweets) == 5

    @pytest.mark.asyncio
    async def test_get_pending_tweets(self, db_session: AsyncSession, test_user: User):
        """Test getting tweets that are due for posting."""
        service = TweetService(db_session)

        # Create a tweet due now
        due_tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Due tweet",
            status=TweetStatus.PENDING,
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db_session.add(due_tweet)
        await db_session.commit()

        due_tweets = await service.get_pending_tweets()

        assert len(due_tweets) >= 1
        assert any(t.id == due_tweet.id for t in due_tweets)

    @pytest.mark.asyncio
    async def test_get_tweet_stats(
        self,
        db_session: AsyncSession,
        test_user: User,
        scheduled_tweet: ScheduledTweet,
        posted_tweet: ScheduledTweet,
    ):
        """Test getting tweet statistics."""
        service = TweetService(db_session)

        stats = await service.get_tweet_stats(test_user.id)

        assert "pending" in stats
        assert "posted" in stats
        assert "failed" in stats
        assert "drafts" in stats
        assert stats["pending"] >= 1
        assert stats["posted"] >= 1


class TestTweetValidation:
    """Tests for tweet validation."""

    @pytest.mark.asyncio
    async def test_tweet_content_length_validation(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that tweet content length is validated."""
        service = TweetService(db_session)

        # Content too long (>280 chars)
        long_content = "x" * 300

        with pytest.raises(TweetServiceError):
            await service.schedule_tweet(
                user_id=test_user.id,
                content=long_content,
                scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
            )

    @pytest.mark.asyncio
    async def test_scheduled_time_in_past_validation(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that scheduled time cannot be in the past."""
        service = TweetService(db_session)

        past_time = datetime.now(timezone.utc) - timedelta(hours=1)

        with pytest.raises(TweetServiceError):
            await service.schedule_tweet(
                user_id=test_user.id,
                content="Test tweet",
                scheduled_for=past_time,
            )
