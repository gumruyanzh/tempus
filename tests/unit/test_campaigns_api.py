"""Tests for campaigns API routes."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value
from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.tweet import ScheduledTweet, TweetStatus, TweetTone
from app.models.user import APIKeyType, EncryptedAPIKey, User
from app.services.auth import AuthService


@pytest.fixture
def auth_cookies(test_user: User) -> dict:
    """Create auth cookies for test user."""
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)
    return {"access_token": tokens["access_token"]}


@pytest.fixture
async def campaign(db_session: AsyncSession, test_user: User) -> AutoCampaign:
    """Create a test campaign."""
    campaign = AutoCampaign(
        id=uuid4(),
        user_id=test_user.id,
        name="Test Campaign",
        original_prompt="test prompt",
        topic="AI Testing",
        tone=TweetTone.PROFESSIONAL,
        frequency_per_day=2,
        duration_days=7,
        total_tweets=14,
        tweets_posted=0,
        tweets_failed=0,
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=7),
        posting_start_hour=9,
        posting_end_hour=21,
        timezone="UTC",
        status=CampaignStatus.ACTIVE,
        web_search_enabled=True,
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    return campaign


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


class TestCampaignsAPI:
    """Tests for campaigns API endpoints."""

    @pytest.mark.asyncio
    async def test_campaigns_list_unauthenticated(self, async_client: AsyncClient):
        """Test that campaigns list requires authentication."""
        response = await async_client.get("/campaigns")
        # Should redirect to login
        assert response.status_code in [302, 401]

    @pytest.mark.asyncio
    async def test_campaigns_list_empty(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test campaigns list with no campaigns."""
        response = await async_client.get(
            "/campaigns",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_campaigns_list_with_campaigns(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
    ):
        """Test campaigns list with existing campaigns."""
        response = await async_client.get(
            "/campaigns",
            cookies=auth_cookies,
        )
        assert response.status_code == 200
        assert b"Test Campaign" in response.content or "Test Campaign" in response.text

    @pytest.mark.asyncio
    async def test_new_campaign_page(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test new campaign page."""
        response = await async_client.get(
            "/campaigns/new",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_view_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
    ):
        """Test viewing a specific campaign."""
        response = await async_client.get(
            f"/campaigns/{campaign.id}",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_view_campaign_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test viewing non-existent campaign."""
        response = await async_client.get(
            f"/campaigns/{uuid4()}",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_campaign_tweets(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
        db_session: AsyncSession,
    ):
        """Test viewing campaign tweets."""
        # Create a tweet for the campaign
        tweet = ScheduledTweet(
            user_id=test_user.id,
            campaign_id=campaign.id,
            content="Test tweet",
            is_campaign_tweet=True,
            scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
            status=TweetStatus.AWAITING_GENERATION,
        )
        db_session.add(tweet)
        await db_session.commit()

        response = await async_client.get(
            f"/campaigns/{campaign.id}/tweets",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_pause_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
    ):
        """Test pausing a campaign."""
        response = await async_client.post(
            f"/campaigns/{campaign.id}/pause",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302  # Redirect after action

    @pytest.mark.asyncio
    async def test_resume_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
        db_session: AsyncSession,
    ):
        """Test resuming a paused campaign."""
        # First pause it
        campaign.status = CampaignStatus.PAUSED
        await db_session.commit()

        response = await async_client.post(
            f"/campaigns/{campaign.id}/resume",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_cancel_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
    ):
        """Test cancelling a campaign."""
        response = await async_client.post(
            f"/campaigns/{campaign.id}/cancel",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_delete_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
    ):
        """Test deleting a campaign."""
        response = await async_client.post(
            f"/campaigns/{campaign.id}/delete",
            cookies=auth_cookies,
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_preview_campaign_no_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test campaign preview without API key."""
        response = await async_client.post(
            "/campaigns/preview",
            cookies=auth_cookies,
            data={"prompt": "test campaign"},
            follow_redirects=False,
        )
        # Should redirect to new campaign page with error
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_preview_campaign_with_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test campaign preview with API key."""
        with patch("app.api.campaigns.DeepSeekService") as mock_service_class:
            mock_service = AsyncMock()
            mock_service_class.return_value = mock_service
            mock_service.close = AsyncMock()

            # Mock the campaign service parsing
            with patch("app.api.campaigns.CampaignService") as mock_campaign_class:
                mock_campaign_service = AsyncMock()
                mock_campaign_class.return_value = mock_campaign_service

                from app.services.campaign import CampaignConfig

                mock_campaign_service.parse_campaign_prompt = AsyncMock(
                    return_value=CampaignConfig(
                        topic="AI Development",
                        frequency_per_day=2,
                        duration_days=7,
                        tone=TweetTone.PROFESSIONAL,
                    )
                )

                response = await async_client.post(
                    "/campaigns/preview",
                    cookies=auth_cookies,
                    data={"prompt": "schedule 2 tweets per day for a week about AI"},
                )
                assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_campaign(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test creating a campaign."""
        response = await async_client.post(
            "/campaigns/create",
            cookies=auth_cookies,
            data={
                "prompt": "test",
                "topic": "AI Development",
                "frequency_per_day": "2",
                "duration_days": "7",
                "tone": "professional",
                "posting_start_hour": "9",
                "posting_end_hour": "21",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_create_campaign_no_api_key(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test creating a campaign without API key."""
        response = await async_client.post(
            "/campaigns/create",
            cookies=auth_cookies,
            data={
                "prompt": "test",
                "topic": "AI Development",
                "frequency_per_day": "2",
                "duration_days": "7",
                "tone": "professional",
            },
            follow_redirects=False,
        )
        # Should redirect with error
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_campaign_tweets_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test viewing tweets for non-existent campaign."""
        response = await async_client.get(
            f"/campaigns/{uuid4()}/tweets",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_pause_campaign_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test pausing non-existent campaign."""
        response = await async_client.post(
            f"/campaigns/{uuid4()}/pause",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_campaign_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test resuming non-existent campaign."""
        response = await async_client.post(
            f"/campaigns/{uuid4()}/resume",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_campaign_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test cancelling non-existent campaign."""
        response = await async_client.post(
            f"/campaigns/{uuid4()}/cancel",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_campaign_not_found(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
    ):
        """Test deleting non-existent campaign."""
        response = await async_client.post(
            f"/campaigns/{uuid4()}/delete",
            cookies=auth_cookies,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_campaign_tweets_with_status_filter(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        campaign: AutoCampaign,
        db_session: AsyncSession,
    ):
        """Test viewing campaign tweets with status filter."""
        response = await async_client.get(
            f"/campaigns/{campaign.id}/tweets?status_filter=pending",
            cookies=auth_cookies,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_campaign_with_all_tones(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test creating campaigns with different tones."""
        for tone in ["casual", "viral", "thought_leadership"]:
            response = await async_client.post(
                "/campaigns/create",
                cookies=auth_cookies,
                data={
                    "prompt": "test",
                    "topic": f"Test {tone}",
                    "frequency_per_day": "1",
                    "duration_days": "1",
                    "tone": tone,
                    "posting_start_hour": "9",
                    "posting_end_hour": "21",
                    "timezone": "UTC",
                },
                follow_redirects=False,
            )
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_create_campaign_with_keywords(
        self,
        async_client: AsyncClient,
        test_user: User,
        auth_cookies: dict,
        deepseek_api_key: EncryptedAPIKey,
    ):
        """Test creating a campaign with search keywords."""
        response = await async_client.post(
            "/campaigns/create",
            cookies=auth_cookies,
            data={
                "prompt": "test",
                "topic": "AI Development",
                "frequency_per_day": "2",
                "duration_days": "7",
                "tone": "professional",
                "posting_start_hour": "9",
                "posting_end_hour": "21",
                "timezone": "UTC",
                "search_keywords": "AI, machine learning, deep learning",
                "custom_instructions": "Keep it technical",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
