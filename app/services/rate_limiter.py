"""Rate limiter service for Twitter API limits."""

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.growth_strategy import RateLimitTracker

logger = get_logger(__name__)


class RateLimitError(Exception):
    """Rate limit exceeded error."""

    def __init__(self, action: str, remaining: int = 0, reset_at: Optional[datetime] = None):
        self.action = action
        self.remaining = remaining
        self.reset_at = reset_at
        super().__init__(f"Rate limit exceeded for {action}. Remaining: {remaining}")


class RateLimiter:
    """Service for tracking and enforcing Twitter API rate limits.

    Twitter API v2 Daily Limits:
    - Follows: 400/day per user
    - Unfollows: 500/day per app
    - Likes: 1000/day per app
    - Posts (tweets + retweets + replies): 100/24 hours
    """

    # Twitter API rate limits per day
    LIMITS = {
        "follow": 400,
        "unfollow": 500,
        "like": 1000,
        "post": 100,  # Includes tweets, retweets, replies
    }

    # Safety margin - don't use 100% of limits
    SAFETY_MARGIN = 0.95

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_tracker(self, user_id: UUID) -> RateLimitTracker:
        """Get or create rate limit tracker for today."""
        today = date.today()

        stmt = select(RateLimitTracker).where(
            RateLimitTracker.user_id == user_id,
            RateLimitTracker.date == today,
        )
        result = await self.db.execute(stmt)
        tracker = result.scalar_one_or_none()

        if tracker is None:
            tracker = RateLimitTracker(
                user_id=user_id,
                date=today,
                last_reset=datetime.now(timezone.utc),
            )
            self.db.add(tracker)
            await self.db.flush()
            await self.db.refresh(tracker)
            logger.info("Created new rate limit tracker", user_id=str(user_id))

        return tracker

    def get_limit(self, action: str) -> int:
        """Get the limit for an action with safety margin."""
        base_limit = self.LIMITS.get(action, 0)
        return int(base_limit * self.SAFETY_MARGIN)

    async def can_perform(self, user_id: UUID, action: str) -> bool:
        """Check if an action can be performed within rate limits.

        Args:
            user_id: User ID to check
            action: Action type ('follow', 'unfollow', 'like', 'post')

        Returns:
            True if action can be performed, False otherwise
        """
        if action not in self.LIMITS:
            logger.warning("Unknown rate limit action", action=action)
            return True

        tracker = await self.get_or_create_tracker(user_id)
        current_count = self._get_count(tracker, action)
        limit = self.get_limit(action)

        return current_count < limit

    async def record_action(self, user_id: UUID, action: str) -> None:
        """Record that an action was performed.

        Args:
            user_id: User ID
            action: Action type ('follow', 'unfollow', 'like', 'post')
        """
        if action not in self.LIMITS:
            return

        tracker = await self.get_or_create_tracker(user_id)
        self._increment_count(tracker, action)
        await self.db.flush()

        logger.debug(
            "Rate limit action recorded",
            user_id=str(user_id),
            action=action,
            new_count=self._get_count(tracker, action),
        )

    async def get_remaining(self, user_id: UUID, action: str) -> int:
        """Get remaining actions allowed today.

        Args:
            user_id: User ID
            action: Action type

        Returns:
            Number of remaining actions allowed
        """
        if action not in self.LIMITS:
            return 0

        tracker = await self.get_or_create_tracker(user_id)
        current_count = self._get_count(tracker, action)
        limit = self.get_limit(action)

        return max(0, limit - current_count)

    async def get_all_remaining(self, user_id: UUID) -> dict[str, int]:
        """Get remaining counts for all action types.

        Args:
            user_id: User ID

        Returns:
            Dict mapping action types to remaining counts
        """
        tracker = await self.get_or_create_tracker(user_id)

        return {
            "follow": max(0, self.get_limit("follow") - tracker.follows_count),
            "unfollow": max(0, self.get_limit("unfollow") - tracker.unfollows_count),
            "like": max(0, self.get_limit("like") - tracker.likes_count),
            "post": max(0, self.get_limit("post") - tracker.posts_count),
        }

    async def get_usage(self, user_id: UUID) -> dict[str, dict]:
        """Get detailed usage statistics.

        Args:
            user_id: User ID

        Returns:
            Dict with usage stats for each action type
        """
        tracker = await self.get_or_create_tracker(user_id)

        return {
            "follow": {
                "used": tracker.follows_count,
                "limit": self.get_limit("follow"),
                "remaining": max(0, self.get_limit("follow") - tracker.follows_count),
                "percentage": (tracker.follows_count / self.get_limit("follow") * 100)
                if self.get_limit("follow") > 0 else 0,
            },
            "unfollow": {
                "used": tracker.unfollows_count,
                "limit": self.get_limit("unfollow"),
                "remaining": max(0, self.get_limit("unfollow") - tracker.unfollows_count),
                "percentage": (tracker.unfollows_count / self.get_limit("unfollow") * 100)
                if self.get_limit("unfollow") > 0 else 0,
            },
            "like": {
                "used": tracker.likes_count,
                "limit": self.get_limit("like"),
                "remaining": max(0, self.get_limit("like") - tracker.likes_count),
                "percentage": (tracker.likes_count / self.get_limit("like") * 100)
                if self.get_limit("like") > 0 else 0,
            },
            "post": {
                "used": tracker.posts_count,
                "limit": self.get_limit("post"),
                "remaining": max(0, self.get_limit("post") - tracker.posts_count),
                "percentage": (tracker.posts_count / self.get_limit("post") * 100)
                if self.get_limit("post") > 0 else 0,
            },
        }

    async def check_and_record(self, user_id: UUID, action: str) -> bool:
        """Check if action can be performed and record it if so.

        This is an atomic operation that checks and records in one step.

        Args:
            user_id: User ID
            action: Action type

        Returns:
            True if action was recorded, False if rate limit exceeded

        Raises:
            RateLimitError: If rate limit is exceeded
        """
        if not await self.can_perform(user_id, action):
            remaining = await self.get_remaining(user_id, action)
            raise RateLimitError(action=action, remaining=remaining)

        await self.record_action(user_id, action)
        return True

    def _get_count(self, tracker: RateLimitTracker, action: str) -> int:
        """Get the current count for an action from tracker."""
        counts = {
            "follow": tracker.follows_count,
            "unfollow": tracker.unfollows_count,
            "like": tracker.likes_count,
            "post": tracker.posts_count,
        }
        return counts.get(action, 0)

    def _increment_count(self, tracker: RateLimitTracker, action: str) -> None:
        """Increment the count for an action in tracker."""
        if action == "follow":
            tracker.follows_count += 1
        elif action == "unfollow":
            tracker.unfollows_count += 1
        elif action == "like":
            tracker.likes_count += 1
        elif action == "post":
            tracker.posts_count += 1

    async def reset_daily_counts(self) -> int:
        """Reset all rate limit counters (called daily at midnight).

        This is typically called by a scheduled task.

        Returns:
            Number of trackers reset
        """
        # Old trackers are automatically irrelevant since we filter by date
        # This method can be used to clean up old records if needed
        from sqlalchemy import delete

        yesterday = date.today()
        # Delete trackers older than 7 days to keep the table clean
        from datetime import timedelta
        cutoff_date = yesterday - timedelta(days=7)

        stmt = delete(RateLimitTracker).where(
            RateLimitTracker.date < cutoff_date
        )
        result = await self.db.execute(stmt)
        await self.db.flush()

        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info("Cleaned up old rate limit trackers", deleted=deleted_count)

        return deleted_count


class EngagementRateLimiter(RateLimiter):
    """Extended rate limiter with engagement-specific features."""

    # Recommended delays between actions (in seconds) for natural behavior
    ACTION_DELAYS = {
        "follow": (60, 180),      # 1-3 minutes between follows
        "unfollow": (30, 120),    # 30s-2min between unfollows
        "like": (15, 60),         # 15-60s between likes
        "retweet": (30, 120),     # 30s-2min between retweets
        "reply": (60, 300),       # 1-5min between replies
    }

    def get_recommended_delay(self, action: str) -> tuple[int, int]:
        """Get recommended delay range for an action type.

        Returns a tuple of (min_seconds, max_seconds) for random delay.
        """
        return self.ACTION_DELAYS.get(action, (30, 120))

    async def get_safe_daily_quota(
        self,
        user_id: UUID,
        action: str,
        desired: int,
    ) -> int:
        """Get the safe number of actions that can be performed.

        Takes into account current usage and desired amount.

        Args:
            user_id: User ID
            action: Action type
            desired: Desired number of actions

        Returns:
            Actual safe number of actions (may be less than desired)
        """
        remaining = await self.get_remaining(user_id, action)
        return min(desired, remaining)

    async def should_pause(self, user_id: UUID) -> bool:
        """Check if engagement should be paused due to high rate limit usage.

        Returns True if more than 80% of any limit is used.
        """
        usage = await self.get_usage(user_id)

        for action, stats in usage.items():
            if stats["percentage"] >= 80:
                logger.warning(
                    "High rate limit usage detected",
                    user_id=str(user_id),
                    action=action,
                    percentage=stats["percentage"],
                )
                return True

        return False

    async def get_next_available_slot(self, user_id: UUID, action: str) -> Optional[datetime]:
        """Get when the next action slot will be available.

        If rate limit is exceeded, returns tomorrow midnight.
        Otherwise returns None (action available now).
        """
        if await self.can_perform(user_id, action):
            return None

        # Rate limit exceeded - next slot is tomorrow
        tomorrow = date.today()
        from datetime import timedelta
        tomorrow = tomorrow + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
