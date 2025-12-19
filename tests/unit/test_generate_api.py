"""Tests for generate API routes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value
from app.models.tweet import TweetTone
from app.models.user import APIKeyType, EncryptedAPIKey, User
from app.services.auth import AuthService


@pytest.fixture
def auth_cookies(test_user: User) -> dict:
    """Create auth cookies for test user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
async def deepseek_api_key(db_session: AsyncSession, test_user: User) -> EncryptedAPIKey:
    """Create a DeepSeek API key for the test user."""
    key = EncryptedAPIKey(
        id=uuid4(),
        user_id=test_user.id,
        key_type=APIKeyType.DEEPSEEK,
        encrypted_key=encrypt_value("sk-test-deepseek-key"),
        key_hint="key",
        is_valid=True,
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)
    return key


class TestGenerateAPI:
    """Tests for generate API endpoints."""

    @pytest.mark.asyncio
    async def test_generate_page_unauthenticated(self, async_client: AsyncClient):
        """Test that generate page requires authentication."""
        response = await async_client.get("/generate")
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_generate_page_authenticated(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test generate page for authenticated user."""
        response = await async_client.get(
            "/generate",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_generate_page_shows_api_key_warning(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test generate page shows warning when no API key."""
        response = await async_client.get(
            "/generate",
            cookies=auth_cookies,
        )
        assert response.status_code == 200
        # Should mention API key configuration
        assert b"API" in response.content or b"key" in response.content.lower()

    @pytest.mark.asyncio
    async def test_generate_tweet_no_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test generating tweet without API key."""
        response = await async_client.post(
            "/generate/tweet",
            cookies=auth_cookies,
            data={
                "prompt": "Write about AI",
                "tone": "professional",
            },
            follow_redirects=False,
        )
        # Should redirect with error
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_generate_tweet_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test successful tweet generation."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.generate_tweet = AsyncMock(
                return_value="This is a generated tweet about AI!"
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/tweet",
                cookies=auth_cookies,
                data={
                    "prompt": "Write about AI",
                    "tone": "professional",
                },
            )

            assert response.status_code == 200
            assert b"generated" in response.content.lower() or b"tweet" in response.content.lower()

    @pytest.mark.asyncio
    async def test_generate_tweet_with_instructions(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test tweet generation with custom instructions."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.generate_tweet = AsyncMock(
                return_value="Tweet with emojis! 🚀"
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/tweet",
                cookies=auth_cookies,
                data={
                    "prompt": "Write about AI",
                    "tone": "viral",
                    "instructions": "Add emojis",
                },
            )

            assert response.status_code == 200
            # Verify instructions were passed
            mock_service.generate_tweet.assert_called_once()
            call_kwargs = mock_service.generate_tweet.call_args.kwargs
            assert call_kwargs["instructions"] == "Add emojis"

    @pytest.mark.asyncio
    async def test_generate_thread_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test successful thread generation."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.generate_thread = AsyncMock(
                return_value=["First tweet", "Second tweet", "Third tweet"]
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/thread",
                cookies=auth_cookies,
                data={
                    "prompt": "Write a thread about AI",
                    "num_tweets": "3",
                    "tone": "professional",
                },
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_generate_thread_with_instructions(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test thread generation with custom instructions."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.generate_thread = AsyncMock(
                return_value=["Tweet 1 🎯", "Tweet 2 🔥"]
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/thread",
                cookies=auth_cookies,
                data={
                    "prompt": "AI thread",
                    "num_tweets": "2",
                    "tone": "viral",
                    "instructions": "Include emojis",
                },
            )

            assert response.status_code == 200
            mock_service.generate_thread.assert_called_once()
            call_kwargs = mock_service.generate_thread.call_args.kwargs
            assert call_kwargs["instructions"] == "Include emojis"

    @pytest.mark.asyncio
    async def test_generate_thread_no_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test thread generation without API key."""
        response = await async_client.post(
            "/generate/thread",
            cookies=auth_cookies,
            data={
                "prompt": "Write a thread",
                "num_tweets": "3",
                "tone": "professional",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_improve_tweet_success(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test successful tweet improvement."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.improve_tweet = AsyncMock(
                return_value="Improved version of the tweet!"
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/improve",
                cookies=auth_cookies,
                data={
                    "content": "Original tweet content",
                    "tone": "viral",
                },
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_improve_tweet_with_feedback(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test tweet improvement with feedback."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.improve_tweet = AsyncMock(
                return_value="More engaging tweet!"
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/improve",
                cookies=auth_cookies,
                data={
                    "content": "Original tweet",
                    "tone": "viral",
                    "feedback": "Make it more engaging",
                },
            )

            assert response.status_code == 200
            mock_service.improve_tweet.assert_called_once()
            call_kwargs = mock_service.improve_tweet.call_args.kwargs
            assert call_kwargs["feedback"] == "Make it more engaging"

    @pytest.mark.asyncio
    async def test_improve_tweet_no_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test tweet improvement without API key."""
        response = await async_client.post(
            "/generate/improve",
            cookies=auth_cookies,
            data={
                "content": "Original tweet",
                "tone": "professional",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_save_draft(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test saving generated content as draft."""
        with patch("app.api.generate.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.models.tweet import TweetDraft

            mock_draft = TweetDraft(
                id=uuid4(),
                user_id=test_user.id,
                content="Draft content",
            )
            mock_service.create_draft = AsyncMock(return_value=mock_draft)

            response = await async_client.post(
                "/generate/save-draft",
                cookies=auth_cookies,
                data={
                    "content": "Generated tweet content",
                    "is_thread": "false",
                    "prompt_used": "AI prompt",
                    "tone_used": "professional",
                },
                follow_redirects=False,
            )

            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_generate_api_error_handling(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test API error handling in generation."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.services.deepseek import DeepSeekAPIError

            mock_service.generate_tweet = AsyncMock(
                side_effect=DeepSeekAPIError("API error")
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/tweet",
                cookies=auth_cookies,
                data={
                    "prompt": "Test",
                    "tone": "professional",
                },
                follow_redirects=False,
            )

            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_generate_thread_api_error(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test API error handling in thread generation."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.services.deepseek import DeepSeekAPIError

            mock_service.generate_thread = AsyncMock(
                side_effect=DeepSeekAPIError("Thread API error")
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/thread",
                cookies=auth_cookies,
                data={
                    "prompt": "Test thread",
                    "num_tweets": "3",
                    "tone": "professional",
                },
                follow_redirects=False,
            )

            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_improve_tweet_api_error(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test API error handling in tweet improvement."""
        with patch("app.api.generate.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.services.deepseek import DeepSeekAPIError

            mock_service.improve_tweet = AsyncMock(
                side_effect=DeepSeekAPIError("Improve API error")
            )
            mock_service.close = AsyncMock()

            response = await async_client.post(
                "/generate/improve",
                cookies=auth_cookies,
                data={
                    "content": "Original tweet",
                    "tone": "professional",
                },
                follow_redirects=False,
            )

            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_save_draft_thread(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test saving thread content as draft."""
        with patch("app.api.generate.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.models.tweet import TweetDraft

            mock_draft = TweetDraft(
                id=uuid4(),
                user_id=test_user.id,
                content="Thread content",
                is_thread=True,
            )
            mock_service.create_draft = AsyncMock(return_value=mock_draft)

            response = await async_client.post(
                "/generate/save-draft",
                cookies=auth_cookies,
                data={
                    "content": "Thread content",
                    "is_thread": "true",
                    "thread_content": "Tweet 1\n---\nTweet 2\n---\nTweet 3",
                    "prompt_used": "Thread prompt",
                    "tone_used": "viral",
                },
                follow_redirects=False,
            )

            assert response.status_code == 302
            mock_service.create_draft.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_draft_error(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test save draft error handling."""
        with patch("app.api.generate.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.create_draft = AsyncMock(side_effect=Exception("DB error"))

            response = await async_client.post(
                "/generate/save-draft",
                cookies=auth_cookies,
                data={
                    "content": "Draft content",
                    "is_thread": "false",
                },
                follow_redirects=False,
            )

            # Should redirect with error
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_generate_tweet_all_tones(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test tweet generation with all available tones."""
        tones = ["professional", "casual", "viral", "thought_leadership"]

        for tone in tones:
            with patch("app.api.generate.DeepSeekService") as mock_service_class:
                mock_service = AsyncMock()
                mock_service_class.return_value = mock_service
                mock_service.generate_tweet = AsyncMock(
                    return_value=f"Generated {tone} tweet"
                )
                mock_service.close = AsyncMock()

                response = await async_client.post(
                    "/generate/tweet",
                    cookies=auth_cookies,
                    data={
                        "prompt": "Test prompt",
                        "tone": tone,
                    },
                )

                assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_save_draft_no_tone(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test saving draft without specifying tone."""
        with patch("app.api.generate.TweetService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service

            from app.models.tweet import TweetDraft

            mock_draft = TweetDraft(
                id=uuid4(),
                user_id=test_user.id,
                content="Draft without tone",
            )
            mock_service.create_draft = AsyncMock(return_value=mock_draft)

            response = await async_client.post(
                "/generate/save-draft",
                cookies=auth_cookies,
                data={
                    "content": "Draft content",
                    "is_thread": "false",
                },
                follow_redirects=False,
            )

            assert response.status_code == 302
