"""Rate limiting utility using Redis."""

from typing import Optional

import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Redis-based rate limiter."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        requests_limit: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ) -> None:
        self.redis_url = redis_url or settings.redis_url
        self.requests_limit = requests_limit or settings.rate_limit_requests
        self.window_seconds = window_seconds or settings.rate_limit_window_seconds
        self._redis: Optional[redis.Redis] = None

    async def get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def is_rate_limited(
        self,
        key: str,
        limit: Optional[int] = None,
        window: Optional[int] = None,
    ) -> tuple[bool, int, int]:
        """
        Check if a key is rate limited.

        Returns:
            Tuple of (is_limited, remaining_requests, reset_time_seconds)
        """
        limit = limit or self.requests_limit
        window = window or self.window_seconds

        redis_client = await self.get_redis()
        rate_key = f"rate_limit:{key}"

        try:
            # Get current count
            current = await redis_client.get(rate_key)

            if current is None:
                # First request - set counter with expiry
                await redis_client.setex(rate_key, window, 1)
                return False, limit - 1, window

            current_count = int(current)

            if current_count >= limit:
                # Rate limited
                ttl = await redis_client.ttl(rate_key)
                return True, 0, ttl

            # Increment counter
            await redis_client.incr(rate_key)
            ttl = await redis_client.ttl(rate_key)

            return False, limit - current_count - 1, ttl

        except Exception as e:
            logger.error("Rate limiter error", error=str(e))
            # Fail open - don't block on Redis errors
            return False, limit, window

    async def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        redis_client = await self.get_redis()
        rate_key = f"rate_limit:{key}"
        await redis_client.delete(rate_key)


# Global rate limiter instance
rate_limiter = RateLimiter()
