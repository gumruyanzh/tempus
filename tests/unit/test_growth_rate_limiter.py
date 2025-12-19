"""Tests for growth strategy rate limiter service."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.growth_strategy import RateLimitTracker
from app.services.rate_limiter import (
    EngagementRateLimiter,
    RateLimitError,
    RateLimiter,
)


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_limits_defined(self):
        """Test that rate limits are defined."""
        assert RateLimiter.LIMITS["follow"] == 400
        assert RateLimiter.LIMITS["unfollow"] == 500
        assert RateLimiter.LIMITS["like"] == 1000
        assert RateLimiter.LIMITS["post"] == 100

    def test_safety_margin(self):
        """Test safety margin is applied."""
        assert RateLimiter.SAFETY_MARGIN == 0.95

    @pytest.mark.asyncio
    async def test_get_limit_with_safety_margin(self, db_session: AsyncSession):
        """Test get_limit applies safety margin."""
        limiter = RateLimiter(db_session)

        # 400 * 0.95 = 380
        assert limiter.get_limit("follow") == 380
        # 500 * 0.95 = 475
        assert limiter.get_limit("unfollow") == 475
        # 1000 * 0.95 = 950
        assert limiter.get_limit("like") == 950
        # 100 * 0.95 = 95
        assert limiter.get_limit("post") == 95

    @pytest.mark.asyncio
    async def test_get_limit_unknown_action(self, db_session: AsyncSession):
        """Test get_limit returns 0 for unknown action."""
        limiter = RateLimiter(db_session)
        assert limiter.get_limit("unknown") == 0

    @pytest.mark.asyncio
    async def test_get_or_create_tracker_creates_new(self, db_session: AsyncSession, test_user):
        """Test creating a new rate limit tracker."""
        limiter = RateLimiter(db_session)

        tracker = await limiter.get_or_create_tracker(test_user.id)

        assert tracker is not None
        assert tracker.user_id == test_user.id
        assert tracker.date == date.today()
        assert tracker.follows_count == 0
        assert tracker.likes_count == 0
        assert tracker.unfollows_count == 0
        assert tracker.posts_count == 0

    @pytest.mark.asyncio
    async def test_get_or_create_tracker_reuses_existing(self, db_session: AsyncSession, test_user):
        """Test that get_or_create_tracker reuses existing tracker for today."""
        limiter = RateLimiter(db_session)

        tracker1 = await limiter.get_or_create_tracker(test_user.id)
        tracker1.follows_count = 10
        await db_session.flush()

        tracker2 = await limiter.get_or_create_tracker(test_user.id)

        assert tracker1.id == tracker2.id
        assert tracker2.follows_count == 10

    @pytest.mark.asyncio
    async def test_can_perform_under_limit(self, db_session: AsyncSession, test_user):
        """Test can_perform returns True when under limit."""
        limiter = RateLimiter(db_session)

        # Fresh tracker should be under limit
        can_perform = await limiter.can_perform(test_user.id, "follow")
        assert can_perform is True

    @pytest.mark.asyncio
    async def test_can_perform_at_limit(self, db_session: AsyncSession, test_user):
        """Test can_perform returns False when at limit."""
        limiter = RateLimiter(db_session)

        # Get tracker and set to limit
        tracker = await limiter.get_or_create_tracker(test_user.id)
        tracker.follows_count = 380  # At limit (400 * 0.95)
        await db_session.flush()

        can_perform = await limiter.can_perform(test_user.id, "follow")
        assert can_perform is False

    @pytest.mark.asyncio
    async def test_can_perform_unknown_action(self, db_session: AsyncSession, test_user):
        """Test can_perform returns True for unknown action."""
        limiter = RateLimiter(db_session)

        can_perform = await limiter.can_perform(test_user.id, "unknown_action")
        assert can_perform is True

    @pytest.mark.asyncio
    async def test_record_action_increments_count(self, db_session: AsyncSession, test_user):
        """Test record_action increments the count."""
        limiter = RateLimiter(db_session)

        await limiter.record_action(test_user.id, "follow")

        tracker = await limiter.get_or_create_tracker(test_user.id)
        assert tracker.follows_count == 1

    @pytest.mark.asyncio
    async def test_record_action_all_types(self, db_session: AsyncSession, test_user):
        """Test record_action for all action types."""
        limiter = RateLimiter(db_session)

        await limiter.record_action(test_user.id, "follow")
        await limiter.record_action(test_user.id, "unfollow")
        await limiter.record_action(test_user.id, "like")
        await limiter.record_action(test_user.id, "post")

        tracker = await limiter.get_or_create_tracker(test_user.id)
        assert tracker.follows_count == 1
        assert tracker.unfollows_count == 1
        assert tracker.likes_count == 1
        assert tracker.posts_count == 1

    @pytest.mark.asyncio
    async def test_get_remaining(self, db_session: AsyncSession, test_user):
        """Test get_remaining calculates correctly."""
        limiter = RateLimiter(db_session)

        # Initial remaining
        remaining = await limiter.get_remaining(test_user.id, "follow")
        assert remaining == 380  # 400 * 0.95

        # After some actions
        await limiter.record_action(test_user.id, "follow")
        remaining = await limiter.get_remaining(test_user.id, "follow")
        assert remaining == 379

    @pytest.mark.asyncio
    async def test_get_all_remaining(self, db_session: AsyncSession, test_user):
        """Test get_all_remaining returns all limits."""
        limiter = RateLimiter(db_session)

        remaining = await limiter.get_all_remaining(test_user.id)

        assert remaining["follow"] == 380
        assert remaining["unfollow"] == 475
        assert remaining["like"] == 950
        assert remaining["post"] == 95

    @pytest.mark.asyncio
    async def test_get_usage(self, db_session: AsyncSession, test_user):
        """Test get_usage returns detailed stats."""
        limiter = RateLimiter(db_session)

        await limiter.record_action(test_user.id, "follow")
        await limiter.record_action(test_user.id, "follow")

        usage = await limiter.get_usage(test_user.id)

        assert usage["follow"]["used"] == 2
        assert usage["follow"]["limit"] == 380
        assert usage["follow"]["remaining"] == 378
        assert usage["follow"]["percentage"] > 0

    @pytest.mark.asyncio
    async def test_check_and_record_success(self, db_session: AsyncSession, test_user):
        """Test check_and_record succeeds under limit."""
        limiter = RateLimiter(db_session)

        result = await limiter.check_and_record(test_user.id, "follow")

        assert result is True
        tracker = await limiter.get_or_create_tracker(test_user.id)
        assert tracker.follows_count == 1

    @pytest.mark.asyncio
    async def test_check_and_record_raises_error(self, db_session: AsyncSession, test_user):
        """Test check_and_record raises error at limit."""
        limiter = RateLimiter(db_session)

        # Set to limit
        tracker = await limiter.get_or_create_tracker(test_user.id)
        tracker.follows_count = 380
        await db_session.flush()

        with pytest.raises(RateLimitError) as exc_info:
            await limiter.check_and_record(test_user.id, "follow")

        assert exc_info.value.action == "follow"
        assert exc_info.value.remaining == 0


class TestEngagementRateLimiter:
    """Tests for EngagementRateLimiter class."""

    def test_action_delays_defined(self):
        """Test that action delays are defined."""
        assert EngagementRateLimiter.ACTION_DELAYS["follow"] == (60, 180)
        assert EngagementRateLimiter.ACTION_DELAYS["like"] == (15, 60)
        assert EngagementRateLimiter.ACTION_DELAYS["reply"] == (60, 300)

    @pytest.mark.asyncio
    async def test_get_recommended_delay(self, db_session: AsyncSession):
        """Test get_recommended_delay returns correct range."""
        limiter = EngagementRateLimiter(db_session)

        assert limiter.get_recommended_delay("follow") == (60, 180)
        assert limiter.get_recommended_delay("unknown") == (30, 120)  # Default

    @pytest.mark.asyncio
    async def test_get_safe_daily_quota(self, db_session: AsyncSession, test_user):
        """Test get_safe_daily_quota respects limits."""
        limiter = EngagementRateLimiter(db_session)

        # Request more than available
        quota = await limiter.get_safe_daily_quota(test_user.id, "follow", 500)
        assert quota == 380  # Capped at limit

        # Request less than available
        quota = await limiter.get_safe_daily_quota(test_user.id, "follow", 100)
        assert quota == 100

    @pytest.mark.asyncio
    async def test_should_pause_low_usage(self, db_session: AsyncSession, test_user):
        """Test should_pause returns False at low usage."""
        limiter = EngagementRateLimiter(db_session)

        should_pause = await limiter.should_pause(test_user.id)
        assert should_pause is False

    @pytest.mark.asyncio
    async def test_should_pause_high_usage(self, db_session: AsyncSession, test_user):
        """Test should_pause returns True at high usage."""
        limiter = EngagementRateLimiter(db_session)

        # Set high usage (> 80%)
        tracker = await limiter.get_or_create_tracker(test_user.id)
        tracker.follows_count = 320  # > 80% of 380
        await db_session.flush()

        should_pause = await limiter.should_pause(test_user.id)
        assert should_pause is True

    @pytest.mark.asyncio
    async def test_get_next_available_slot_available(self, db_session: AsyncSession, test_user):
        """Test get_next_available_slot when available."""
        limiter = EngagementRateLimiter(db_session)

        slot = await limiter.get_next_available_slot(test_user.id, "follow")
        assert slot is None  # Available now

    @pytest.mark.asyncio
    async def test_get_next_available_slot_tomorrow(self, db_session: AsyncSession, test_user):
        """Test get_next_available_slot when at limit."""
        limiter = EngagementRateLimiter(db_session)

        # Set at limit
        tracker = await limiter.get_or_create_tracker(test_user.id)
        tracker.follows_count = 380
        await db_session.flush()

        slot = await limiter.get_next_available_slot(test_user.id, "follow")
        assert slot is not None
        assert slot.date() > date.today()


class TestRateLimitError:
    """Tests for RateLimitError exception."""

    def test_rate_limit_error_message(self):
        """Test RateLimitError message format."""
        error = RateLimitError(action="follow", remaining=0)
        assert "follow" in str(error)
        assert "Remaining: 0" in str(error)

    def test_rate_limit_error_attributes(self):
        """Test RateLimitError stores attributes."""
        reset_time = datetime.now(timezone.utc)
        error = RateLimitError(action="like", remaining=5, reset_at=reset_time)

        assert error.action == "like"
        assert error.remaining == 5
        assert error.reset_at == reset_time
