"""Tests for rate limiter utility."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.utils.rate_limiter import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_init_default_values(self):
        """Test rate limiter initializes with default values."""
        limiter = RateLimiter()
        assert limiter.requests_limit is not None
        assert limiter.window_seconds is not None
        assert limiter._redis is None

    def test_init_custom_values(self):
        """Test rate limiter with custom values."""
        limiter = RateLimiter(
            redis_url="redis://custom:6379",
            requests_limit=100,
            window_seconds=60,
        )
        assert limiter.redis_url == "redis://custom:6379"
        assert limiter.requests_limit == 100
        assert limiter.window_seconds == 60

    @pytest.mark.asyncio
    async def test_get_redis_creates_connection(self):
        """Test get_redis creates a connection."""
        limiter = RateLimiter(redis_url="redis://localhost:6379")

        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = MagicMock()
            mock_from_url.return_value = mock_redis

            client = await limiter.get_redis()

            assert client is not None
            mock_from_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_redis_reuses_connection(self):
        """Test get_redis reuses existing connection."""
        limiter = RateLimiter(redis_url="redis://localhost:6379")

        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = MagicMock()
            mock_from_url.return_value = mock_redis

            client1 = await limiter.get_redis()
            client2 = await limiter.get_redis()

            assert client1 is client2
            assert mock_from_url.call_count == 1

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing Redis connection."""
        limiter = RateLimiter(redis_url="redis://localhost:6379")

        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = AsyncMock()
            mock_from_url.return_value = mock_redis

            await limiter.get_redis()
            await limiter.close()

            mock_redis.close.assert_called_once()
            assert limiter._redis is None

    @pytest.mark.asyncio
    async def test_close_no_connection(self):
        """Test closing when no connection exists."""
        limiter = RateLimiter()

        # Should not raise
        await limiter.close()

    @pytest.mark.asyncio
    async def test_is_rate_limited_first_request(self):
        """Test rate limiting on first request."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.return_value = None
            mock_redis.setex = AsyncMock()

            is_limited, remaining, reset = await limiter.is_rate_limited("test_key")

            assert is_limited is False
            assert remaining == 9
            assert reset == 60
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_rate_limited_subsequent_request(self):
        """Test rate limiting on subsequent requests."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.return_value = "5"  # Already 5 requests
            mock_redis.ttl.return_value = 30
            mock_redis.incr = AsyncMock()

            is_limited, remaining, reset = await limiter.is_rate_limited("test_key")

            assert is_limited is False
            assert remaining == 4  # 10 - 5 - 1 = 4
            assert reset == 30
            mock_redis.incr.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_rate_limited_at_limit(self):
        """Test rate limiting when at limit."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.return_value = "10"  # At limit
            mock_redis.ttl.return_value = 45

            is_limited, remaining, reset = await limiter.is_rate_limited("test_key")

            assert is_limited is True
            assert remaining == 0
            assert reset == 45

    @pytest.mark.asyncio
    async def test_is_rate_limited_over_limit(self):
        """Test rate limiting when over limit."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.return_value = "15"  # Over limit
            mock_redis.ttl.return_value = 20

            is_limited, remaining, reset = await limiter.is_rate_limited("test_key")

            assert is_limited is True
            assert remaining == 0
            assert reset == 20

    @pytest.mark.asyncio
    async def test_is_rate_limited_custom_limits(self):
        """Test rate limiting with custom limits."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.return_value = None
            mock_redis.setex = AsyncMock()

            is_limited, remaining, reset = await limiter.is_rate_limited(
                "test_key",
                limit=5,
                window=30,
            )

            assert is_limited is False
            assert remaining == 4  # 5 - 1 = 4
            assert reset == 30

    @pytest.mark.asyncio
    async def test_is_rate_limited_redis_error(self):
        """Test rate limiting fails open on Redis error."""
        limiter = RateLimiter(requests_limit=10, window_seconds=60)

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.get.side_effect = Exception("Redis error")

            is_limited, remaining, reset = await limiter.is_rate_limited("test_key")

            # Should fail open
            assert is_limited is False
            assert remaining == 10
            assert reset == 60

    @pytest.mark.asyncio
    async def test_reset(self):
        """Test resetting rate limit."""
        limiter = RateLimiter()

        with patch.object(limiter, "get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            mock_redis.delete = AsyncMock()

            await limiter.reset("test_key")

            mock_redis.delete.assert_called_once_with("rate_limit:test_key")
