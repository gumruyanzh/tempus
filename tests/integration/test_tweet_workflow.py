"""Integration tests for tweet scheduling workflow."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth import OAuthAccount
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User
from app.services.tweet import TweetService
from app.services.twitter import TwitterService


class TestTweetSchedulingWorkflow:
    """Integration tests for the full tweet scheduling workflow."""

    @pytest.mark.asyncio
    async def test_full_tweet_scheduling_workflow(
        self,
        db_session: AsyncSession,
        test_user: User,
    ):
        """Test the complete workflow: schedule -> verify status."""
        tweet_service = TweetService(db_session)

        # Step 1: Schedule a tweet
        scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        tweet = await tweet_service.schedule_tweet(
            user_id=test_user.id,
            content="Integration test tweet #testing",
            scheduled_for=scheduled_time,
        )
        await db_session.commit()

        assert tweet.status == TweetStatus.PENDING
        assert tweet.id is not None

        # Step 2: Verify tweet can be retrieved
        retrieved = await tweet_service.get_scheduled_tweet(tweet.id, test_user.id)
        assert retrieved is not None
        assert retrieved.content == "Integration test tweet #testing"

    @pytest.mark.asyncio
    async def test_draft_creation_workflow(
        self,
        db_session: AsyncSession,
        test_user: User,
    ):
        """Test creating and retrieving drafts."""
        tweet_service = TweetService(db_session)

        # Create draft
        draft = await tweet_service.create_draft(
            user_id=test_user.id,
            content="This is a draft that will be scheduled",
            generated_by_ai=True,
            prompt_used="Write about productivity",
        )
        await db_session.commit()

        assert draft is not None
        assert draft.generated_by_ai is True

        # Retrieve draft
        retrieved = await tweet_service.get_draft(draft.id, test_user.id)
        assert retrieved is not None
        assert retrieved.content == "This is a draft that will be scheduled"


class TestUserOnboardingWorkflow:
    """Integration tests for user onboarding workflow."""

    @pytest.mark.asyncio
    async def test_twitter_oauth_signup_workflow(self, db_session: AsyncSession):
        """Test the Twitter OAuth sign-up workflow."""
        twitter_service = TwitterService(db_session)

        # Simulate OAuth data from Twitter
        token_data = {
            "access_token": "new-user-access-token",
            "refresh_token": "new-user-refresh-token",
            "expires_in": 7200,
        }
        user_data = {
            "data": {
                "id": "new-twitter-id-12345",
                "name": "New Twitter User",
                "username": "newtwitteruser",
                "profile_image_url": "https://example.com/profile.jpg",
            }
        }

        # Step 1: Check user doesn't exist
        existing = await twitter_service.find_user_by_twitter_id("new-twitter-id-12345")
        assert existing is None

        # Step 2: Sign in (creates new user)
        user, is_new = await twitter_service.sign_in_or_sign_up_with_twitter(
            token_data, user_data
        )
        await db_session.commit()

        assert is_new is True
        assert user is not None
        assert user.full_name == "New Twitter User"

        # Step 3: Subsequent sign-in should find existing user
        user2, is_new2 = await twitter_service.sign_in_or_sign_up_with_twitter(
            token_data, user_data
        )

        assert is_new2 is False
        assert user2.id == user.id


class TestMultipleUsersWorkflow:
    """Integration tests for multi-user scenarios."""

    @pytest.mark.asyncio
    async def test_users_cant_access_each_others_tweets(
        self, db_session: AsyncSession, test_user: User, admin_user: User
    ):
        """Test that users cannot access other users' tweets."""
        tweet_service = TweetService(db_session)

        # User 1 creates a tweet
        user1_tweet = await tweet_service.schedule_tweet(
            user_id=test_user.id,
            content="User 1's private tweet",
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        await db_session.commit()

        # User 2 tries to access it
        accessed_tweet = await tweet_service.get_scheduled_tweet(
            tweet_id=user1_tweet.id,
            user_id=admin_user.id,  # Different user
        )

        assert accessed_tweet is None

    @pytest.mark.asyncio
    async def test_each_user_has_own_stats(
        self, db_session: AsyncSession, test_user: User, admin_user: User
    ):
        """Test that tweet stats are per-user."""
        tweet_service = TweetService(db_session)

        # Create tweets for user 1
        for i in range(3):
            await tweet_service.schedule_tweet(
                user_id=test_user.id,
                content=f"User 1 tweet {i}",
                scheduled_for=datetime.now(timezone.utc) + timedelta(hours=i + 1),
            )

        # Create tweets for user 2
        for i in range(2):
            await tweet_service.schedule_tweet(
                user_id=admin_user.id,
                content=f"User 2 tweet {i}",
                scheduled_for=datetime.now(timezone.utc) + timedelta(hours=i + 1),
            )

        await db_session.commit()

        # Check stats are separate
        user1_stats = await tweet_service.get_tweet_stats(test_user.id)
        user2_stats = await tweet_service.get_tweet_stats(admin_user.id)

        assert user1_stats["pending"] >= 3
        assert user2_stats["pending"] >= 2
