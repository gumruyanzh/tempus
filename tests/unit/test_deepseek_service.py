"""Tests for DeepSeek service."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.tweet import TweetTone
from app.services.deepseek import DeepSeekAPIError, DeepSeekService


class TestDeepSeekService:
    """Tests for DeepSeekService."""

    def test_init(self):
        """Test service initialization."""
        service = DeepSeekService("test-api-key")
        assert service.api_key == "test-api-key"
        assert service._client is None

    @pytest.mark.asyncio
    async def test_get_client(self):
        """Test HTTP client creation."""
        service = DeepSeekService("test-api-key")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance

            client = await service.get_client()
            assert client is not None

            await service.close()

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test HTTP client cleanup."""
        service = DeepSeekService("test-api-key")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance

            await service.get_client()
            await service.close()
            mock_instance.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_tweet_success(self):
        """Test successful tweet generation."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "This is a generated tweet!"}}]
            }
            mock_client.post.return_value = mock_response

            tweet = await service.generate_tweet(
                prompt="Write about AI",
                tone=TweetTone.PROFESSIONAL,
            )

            assert tweet == "This is a generated tweet!"

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_tweet_with_instructions(self):
        """Test tweet generation with custom instructions."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Tweet with emojis! 🚀"}}]
            }
            mock_client.post.return_value = mock_response

            tweet = await service.generate_tweet(
                prompt="Write about AI",
                tone=TweetTone.VIRAL,
                instructions="Add emojis",
            )

            assert "🚀" in tweet

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_tweet_truncates_long_content(self):
        """Test that long tweets are truncated."""
        service = DeepSeekService("test-api-key")

        long_content = "A" * 300  # More than 280 characters

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            # First call returns too long, second returns still too long
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": long_content}}]
            }
            mock_client.post.return_value = mock_response

            tweet = await service.generate_tweet(
                prompt="Write something long",
                tone=TweetTone.PROFESSIONAL,
            )

            assert len(tweet) <= 280
            assert tweet.endswith("...")

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_tweet_cleans_response(self):
        """Test that responses are properly cleaned."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            # Response with quotes and numbering
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": '"1. This is the tweet"'}}]
            }
            mock_client.post.return_value = mock_response

            tweet = await service.generate_tweet(
                prompt="Write about AI",
                tone=TweetTone.PROFESSIONAL,
            )

            assert not tweet.startswith('"')
            assert not tweet.startswith("1.")

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_thread_success(self):
        """Test successful thread generation."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{
                    "message": {
                        "content": "1. First tweet\n2. Second tweet\n3. Third tweet"
                    }
                }]
            }
            mock_client.post.return_value = mock_response

            tweets = await service.generate_thread(
                prompt="Write a thread about AI",
                num_tweets=3,
                tone=TweetTone.PROFESSIONAL,
            )

            assert len(tweets) == 3
            assert "First tweet" in tweets[0]
            assert "Second tweet" in tweets[1]
            assert "Third tweet" in tweets[2]

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_thread_bounds(self):
        """Test thread generation respects bounds."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "1. Tweet one\n2. Tweet two"}}]
            }
            mock_client.post.return_value = mock_response

            # Request 1 tweet, should be bumped to 2
            tweets = await service.generate_thread(
                prompt="Test",
                num_tweets=1,
                tone=TweetTone.PROFESSIONAL,
            )
            assert len(tweets) == 2

        await service.close()

    @pytest.mark.asyncio
    async def test_generate_thread_with_instructions(self):
        """Test thread generation with custom instructions."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{
                    "message": {
                        "content": "1. Tweet one 🚀\n2. Tweet two 🔥"
                    }
                }]
            }
            mock_client.post.return_value = mock_response

            tweets = await service.generate_thread(
                prompt="Test",
                num_tweets=2,
                tone=TweetTone.VIRAL,
                instructions="Add emojis",
            )
            assert len(tweets) == 2

        await service.close()

    @pytest.mark.asyncio
    async def test_improve_tweet_success(self):
        """Test tweet improvement."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Improved version of the tweet!"}}]
            }
            mock_client.post.return_value = mock_response

            improved = await service.improve_tweet(
                original_tweet="Original tweet",
                tone=TweetTone.PROFESSIONAL,
            )

            assert improved == "Improved version of the tweet!"

        await service.close()

    @pytest.mark.asyncio
    async def test_improve_tweet_with_feedback(self):
        """Test tweet improvement with feedback."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "More engaging tweet!"}}]
            }
            mock_client.post.return_value = mock_response

            improved = await service.improve_tweet(
                original_tweet="Original tweet",
                tone=TweetTone.VIRAL,
                feedback="Make it more engaging",
            )

            assert improved == "More engaging tweet!"

        await service.close()

    @pytest.mark.asyncio
    async def test_validate_api_key_success(self):
        """Test API key validation success."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.post.return_value = mock_response

            is_valid = await service.validate_api_key()
            assert is_valid is True

        await service.close()

    @pytest.mark.asyncio
    async def test_validate_api_key_failure(self):
        """Test API key validation failure."""
        service = DeepSeekService("invalid-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_client.post.return_value = mock_response

            is_valid = await service.validate_api_key()
            assert is_valid is False

        await service.close()

    @pytest.mark.asyncio
    async def test_validate_api_key_exception(self):
        """Test API key validation with exception."""
        service = DeepSeekService("test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            mock_client.post.side_effect = Exception("Network error")

            is_valid = await service.validate_api_key()
            assert is_valid is False

        await service.close()

    @pytest.mark.asyncio
    async def test_call_api_error(self):
        """Test API call error handling."""
        service = DeepSeekService("test-api-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_client.post.return_value = mock_response

            with pytest.raises(DeepSeekAPIError):
                await service._call_api("system", "user")

        await service.close()

    def test_build_system_prompt_professional(self):
        """Test system prompt building for professional tone."""
        service = DeepSeekService("test-key")
        prompt = service._build_system_prompt(TweetTone.PROFESSIONAL)
        assert "professional" in prompt.lower() or "business" in prompt.lower()

    def test_build_system_prompt_viral(self):
        """Test system prompt building for viral tone."""
        service = DeepSeekService("test-key")
        prompt = service._build_system_prompt(TweetTone.VIRAL)
        assert "viral" in prompt.lower() or "engagement" in prompt.lower()

    def test_build_system_prompt_casual(self):
        """Test system prompt building for casual tone."""
        service = DeepSeekService("test-key")
        prompt = service._build_system_prompt(TweetTone.CASUAL)
        assert "casual" in prompt.lower() or "friendly" in prompt.lower()

    def test_build_system_prompt_thought_leadership(self):
        """Test system prompt building for thought leadership tone."""
        service = DeepSeekService("test-key")
        prompt = service._build_system_prompt(TweetTone.THOUGHT_LEADERSHIP)
        assert "thought" in prompt.lower() or "leader" in prompt.lower()

    def test_build_system_prompt_custom(self):
        """Test system prompt building with custom prompt."""
        service = DeepSeekService("test-key")
        custom = "Custom prompt with {tone_instructions}"
        prompt = service._build_system_prompt(TweetTone.PROFESSIONAL, custom)
        assert "Custom prompt" in prompt

    def test_clean_tweet_response_quotes(self):
        """Test cleaning quoted responses."""
        assert DeepSeekService._clean_tweet_response('"Test tweet"') == "Test tweet"
        assert DeepSeekService._clean_tweet_response("'Test tweet'") == "Test tweet"

    def test_clean_tweet_response_numbering(self):
        """Test cleaning numbered responses."""
        assert DeepSeekService._clean_tweet_response("1. Test tweet") == "Test tweet"
        assert DeepSeekService._clean_tweet_response("  2. Test tweet") == "Test tweet"

    def test_clean_tweet_response_whitespace(self):
        """Test cleaning whitespace."""
        assert DeepSeekService._clean_tweet_response("  Test tweet  ") == "Test tweet"

    def test_parse_thread_response(self):
        """Test parsing thread response."""
        response = "1. First tweet\n2. Second tweet\n3. Third tweet"
        tweets = DeepSeekService._parse_thread_response(response)
        assert len(tweets) == 3
        assert tweets[0] == "First tweet"
        assert tweets[1] == "Second tweet"
        assert tweets[2] == "Third tweet"

    def test_parse_thread_response_with_quotes(self):
        """Test parsing thread response with quotes."""
        response = '1. "First tweet"\n2. "Second tweet"'
        tweets = DeepSeekService._parse_thread_response(response)
        assert len(tweets) == 2
        assert tweets[0] == "First tweet"
        assert tweets[1] == "Second tweet"

    def test_parse_thread_response_empty_lines(self):
        """Test parsing thread response with empty lines."""
        response = "1. First tweet\n\n2. Second tweet\n\n\n3. Third tweet"
        tweets = DeepSeekService._parse_thread_response(response)
        assert len(tweets) == 3
