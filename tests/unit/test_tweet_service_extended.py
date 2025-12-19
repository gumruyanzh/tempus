"""Extended tests for tweet service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import ScheduledTweet, TweetDraft, TweetStatus, TweetTone
from app.models.user import User
from app.services.tweet import TweetService


class TestTweetServiceExtended:
    """Extended tests for TweetService."""

    @pytest.mark.asyncio
    async def test_cancel_scheduled_tweet(self, db_session: AsyncSession, test_user: User):
        """Test canceling a scheduled tweet."""
        service = TweetService(db_session)

        # Create a pending tweet
        tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Tweet to cancel",
            scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
            status=TweetStatus.PENDING,
            timezone="UTC",
        )
        db_session.add(tweet)
        await db_session.commit()

        # Cancel it
        cancelled = await service.cancel_scheduled_tweet(tweet)
        await db_session.commit()

        assert cancelled.status == TweetStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_delete_scheduled_tweet(self, db_session: AsyncSession, test_user: User):
        """Test deleting a tweet."""
        service = TweetService(db_session)

        # Create a tweet
        tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Tweet to delete",
            scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
            status=TweetStatus.PENDING,
            timezone="UTC",
        )
        db_session.add(tweet)
        await db_session.commit()

        tweet_id = tweet.id

        # Delete it
        await service.delete_scheduled_tweet(tweet)
        await db_session.commit()

        # Verify deleted
        found = await service.get_scheduled_tweet(tweet_id, test_user.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_update_scheduled_tweet_content(self, db_session: AsyncSession, test_user: User):
        """Test updating tweet content."""
        service = TweetService(db_session)

        # Create a tweet
        tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Original content",
            scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
            status=TweetStatus.PENDING,
            timezone="UTC",
        )
        db_session.add(tweet)
        await db_session.commit()

        # Update it
        updated = await service.update_scheduled_tweet(
            tweet=tweet,
            content="Updated content",
        )
        await db_session.commit()

        assert updated.content == "Updated content"

    @pytest.mark.asyncio
    async def test_update_scheduled_tweet_schedule(self, db_session: AsyncSession, test_user: User):
        """Test updating tweet schedule."""
        service = TweetService(db_session)

        # Create a tweet
        original_time = datetime.now(timezone.utc) + timedelta(days=1)
        tweet = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Tweet content",
            scheduled_for=original_time,
            status=TweetStatus.PENDING,
            timezone="UTC",
        )
        db_session.add(tweet)
        await db_session.commit()

        # Update schedule
        new_time = datetime.now(timezone.utc) + timedelta(days=2)
        updated = await service.update_scheduled_tweet(
            tweet=tweet,
            scheduled_for=new_time,
        )
        await db_session.commit()

        assert updated.scheduled_for != original_time

    @pytest.mark.asyncio
    async def test_get_user_drafts(self, db_session: AsyncSession, test_user: User):
        """Test getting user drafts."""
        service = TweetService(db_session)

        # Create drafts
        for i in range(3):
            draft = TweetDraft(
                id=uuid4(),
                user_id=test_user.id,
                content=f"Draft {i}",
            )
            db_session.add(draft)
        await db_session.commit()

        drafts = await service.get_user_drafts(test_user.id)

        assert len(drafts) >= 3

    @pytest.mark.asyncio
    async def test_get_draft(self, db_session: AsyncSession, test_user: User):
        """Test getting draft by ID."""
        service = TweetService(db_session)

        # Create draft
        draft = TweetDraft(
            id=uuid4(),
            user_id=test_user.id,
            content="Test draft",
        )
        db_session.add(draft)
        await db_session.commit()

        found = await service.get_draft(draft.id, test_user.id)

        assert found is not None
        assert found.content == "Test draft"

    @pytest.mark.asyncio
    async def test_get_draft_wrong_user(self, db_session: AsyncSession, test_user: User):
        """Test getting draft by ID with wrong user."""
        service = TweetService(db_session)

        # Create draft
        draft = TweetDraft(
            id=uuid4(),
            user_id=test_user.id,
            content="Test draft",
        )
        db_session.add(draft)
        await db_session.commit()

        found = await service.get_draft(draft.id, uuid4())

        assert found is None

    @pytest.mark.asyncio
    async def test_delete_draft(self, db_session: AsyncSession, test_user: User):
        """Test deleting a draft."""
        service = TweetService(db_session)

        # Create draft
        draft = TweetDraft(
            id=uuid4(),
            user_id=test_user.id,
            content="Draft to delete",
        )
        db_session.add(draft)
        await db_session.commit()

        draft_id = draft.id

        await service.delete_draft(draft)
        await db_session.commit()

        # Verify deleted
        found = await service.get_draft(draft_id, test_user.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_schedule_tweet_basic(self, db_session: AsyncSession, test_user: User):
        """Test scheduling a basic tweet."""
        service = TweetService(db_session)

        tweet = await service.schedule_tweet(
            user_id=test_user.id,
            content="Professional tweet content here",
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
            timezone_str="UTC",
        )
        await db_session.commit()

        assert tweet is not None
        assert tweet.content == "Professional tweet content here"
        assert tweet.status == TweetStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_recent_tweets(self, db_session: AsyncSession, test_user: User):
        """Test getting recent tweets for history."""
        service = TweetService(db_session)

        # Create some posted tweets
        for i in range(5):
            tweet = ScheduledTweet(
                id=uuid4(),
                user_id=test_user.id,
                content=f"Posted tweet {i}",
                scheduled_for=datetime.now(timezone.utc) - timedelta(hours=i+1),
                posted_at=datetime.now(timezone.utc) - timedelta(hours=i+1),
                status=TweetStatus.POSTED,
                timezone="UTC",
            )
            db_session.add(tweet)
        await db_session.commit()

        tweets = await service.get_user_scheduled_tweets(
            user_id=test_user.id,
            limit=10,
            offset=0,
        )

        assert len(tweets) >= 5

    @pytest.mark.asyncio
    async def test_duplicate_scheduled_tweet(self, db_session: AsyncSession, test_user: User):
        """Test duplicating a scheduled tweet."""
        service = TweetService(db_session)

        # Create original tweet
        original = ScheduledTweet(
            id=uuid4(),
            user_id=test_user.id,
            content="Original tweet",
            scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
            status=TweetStatus.POSTED,
            timezone="UTC",
        )
        db_session.add(original)
        await db_session.commit()

        # Duplicate it
        new_time = datetime.now(timezone.utc) + timedelta(days=2)
        duplicate = await service.duplicate_scheduled_tweet(
            tweet=original,
            new_scheduled_for=new_time,
        )
        await db_session.commit()

        assert duplicate is not None
        assert duplicate.id != original.id
        assert duplicate.content == original.content
        assert duplicate.status == TweetStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_tweet_stats(self, db_session: AsyncSession, test_user: User):
        """Test getting tweet statistics."""
        service = TweetService(db_session)

        # Create various tweets
        statuses = [TweetStatus.PENDING, TweetStatus.POSTED, TweetStatus.FAILED]
        for status in statuses:
            tweet = ScheduledTweet(
                id=uuid4(),
                user_id=test_user.id,
                content=f"Tweet with status {status.value}",
                scheduled_for=datetime.now(timezone.utc) - timedelta(hours=1),
                status=status,
                timezone="UTC",
            )
            db_session.add(tweet)
        await db_session.commit()

        stats = await service.get_tweet_stats(test_user.id)

        assert "pending" in stats
        assert "posted" in stats
        assert "failed" in stats
