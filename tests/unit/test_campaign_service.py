"""Tests for campaign service."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.tweet import ScheduledTweet, TweetStatus, TweetTone
from app.models.user import User
from app.services.campaign import CampaignConfig, CampaignService, CampaignServiceError


class TestCampaignConfig:
    """Tests for CampaignConfig dataclass."""

    def test_campaign_config_defaults(self):
        """Test CampaignConfig with minimal parameters."""
        config = CampaignConfig(
            topic="AI Development",
            frequency_per_day=2,
            duration_days=7,
            tone=TweetTone.PROFESSIONAL,
        )
        assert config.topic == "AI Development"
        assert config.frequency_per_day == 2
        assert config.duration_days == 7
        assert config.tone == TweetTone.PROFESSIONAL
        assert config.start_date is None
        assert config.custom_instructions is None
        assert config.search_keywords is None

    def test_campaign_config_full(self):
        """Test CampaignConfig with all parameters."""
        start = datetime.now(timezone.utc)
        config = CampaignConfig(
            topic="Machine Learning",
            frequency_per_day=4,
            duration_days=30,
            tone=TweetTone.VIRAL,
            start_date=start,
            custom_instructions="Make it engaging",
            search_keywords=["ML", "AI", "deep learning"],
        )
        assert config.start_date == start
        assert config.custom_instructions == "Make it engaging"
        assert len(config.search_keywords) == 3


class TestCampaignService:
    """Tests for CampaignService."""

    @pytest.mark.asyncio
    async def test_parse_campaign_prompt_success(self, db_session: AsyncSession):
        """Test successful campaign prompt parsing."""
        service = CampaignService(db_session)

        # Mock DeepSeek service
        mock_deepseek = AsyncMock()
        mock_deepseek._call_api = AsyncMock(return_value=json.dumps({
            "topic": "AI and agentic development",
            "frequency_per_day": 4,
            "duration_days": 30,
            "tone": "viral",
            "search_keywords": ["AI", "agents"],
        }))

        config = await service.parse_campaign_prompt(
            "schedule 4 times a day tweets for a month about AI and agentic development make it viral",
            mock_deepseek,
        )

        assert config.topic == "AI and agentic development"
        assert config.frequency_per_day == 4
        assert config.duration_days == 30
        assert config.tone == TweetTone.VIRAL
        assert "AI" in config.search_keywords

    @pytest.mark.asyncio
    async def test_parse_campaign_prompt_with_markdown(self, db_session: AsyncSession):
        """Test parsing when response has markdown code blocks."""
        service = CampaignService(db_session)

        mock_deepseek = AsyncMock()
        mock_deepseek._call_api = AsyncMock(return_value="""```json
{
    "topic": "Python programming",
    "frequency_per_day": 2,
    "duration_days": 14,
    "tone": "professional"
}
```""")

        config = await service.parse_campaign_prompt(
            "tweet twice daily for 2 weeks about Python",
            mock_deepseek,
        )

        assert config.topic == "Python programming"
        assert config.frequency_per_day == 2
        assert config.duration_days == 14
        assert config.tone == TweetTone.PROFESSIONAL

    @pytest.mark.asyncio
    async def test_parse_campaign_prompt_invalid_json(self, db_session: AsyncSession):
        """Test fallback when JSON parsing fails."""
        service = CampaignService(db_session)

        mock_deepseek = AsyncMock()
        mock_deepseek._call_api = AsyncMock(return_value="This is not valid JSON")

        config = await service.parse_campaign_prompt(
            "tweet about technology",
            mock_deepseek,
        )

        # Should fall back to defaults
        assert config.topic == "tweet about technology"
        assert config.frequency_per_day == 1
        assert config.duration_days == 7
        assert config.tone == TweetTone.PROFESSIONAL

    @pytest.mark.asyncio
    async def test_parse_campaign_prompt_bounds(self, db_session: AsyncSession):
        """Test that frequency and duration are bounded."""
        service = CampaignService(db_session)

        mock_deepseek = AsyncMock()
        mock_deepseek._call_api = AsyncMock(return_value=json.dumps({
            "topic": "Test",
            "frequency_per_day": 100,  # Should be capped at 10
            "duration_days": 500,  # Should be capped at 90
            "tone": "casual",
        }))

        config = await service.parse_campaign_prompt("test", mock_deepseek)

        assert config.frequency_per_day == 10
        assert config.duration_days == 90

    def test_generate_time_slots_basic(self, db_session: AsyncSession):
        """Test basic time slot generation."""
        service = CampaignService(db_session)

        start_date = datetime.now(timezone.utc) + timedelta(days=1)
        slots = service.generate_time_slots(
            start_date=start_date,
            duration_days=3,
            frequency_per_day=2,
            posting_start_hour=9,
            posting_end_hour=21,
            user_timezone="UTC",
        )

        # Should have 6 slots (3 days * 2 per day)
        assert len(slots) == 6

        # All slots should be in the future
        for slot in slots:
            assert slot > datetime.now(timezone.utc)

    def test_generate_time_slots_single_tweet_per_day(self, db_session: AsyncSession):
        """Test time slot generation with 1 tweet per day."""
        service = CampaignService(db_session)

        start_date = datetime.now(timezone.utc) + timedelta(days=1)
        slots = service.generate_time_slots(
            start_date=start_date,
            duration_days=5,
            frequency_per_day=1,
            posting_start_hour=9,
            posting_end_hour=21,
            user_timezone="UTC",
        )

        assert len(slots) == 5

    def test_generate_time_slots_different_timezone(self, db_session: AsyncSession):
        """Test time slot generation with different timezone."""
        service = CampaignService(db_session)

        start_date = datetime.now(timezone.utc) + timedelta(days=1)
        slots = service.generate_time_slots(
            start_date=start_date,
            duration_days=2,
            frequency_per_day=2,
            posting_start_hour=9,
            posting_end_hour=17,
            user_timezone="America/New_York",
        )

        assert len(slots) == 4
        # All slots should be in UTC
        for slot in slots:
            assert slot.tzinfo is not None

    @pytest.mark.asyncio
    async def test_create_campaign(self, db_session: AsyncSession, test_user: User):
        """Test campaign creation."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="AI Testing",
            frequency_per_day=2,
            duration_days=3,
            tone=TweetTone.PROFESSIONAL,
        )

        campaign = await service.create_campaign(
            user_id=test_user.id,
            config=config,
            user_timezone="UTC",
        )

        assert campaign is not None
        assert campaign.user_id == test_user.id
        assert campaign.topic == "AI Testing"
        assert campaign.frequency_per_day == 2
        assert campaign.duration_days == 3
        assert campaign.total_tweets == 6
        assert campaign.tweets_posted == 0
        assert campaign.status == CampaignStatus.ACTIVE
        assert campaign.web_search_enabled is True

    @pytest.mark.asyncio
    async def test_create_campaign_with_custom_instructions(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test campaign creation with custom instructions."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Python Tips",
            frequency_per_day=1,
            duration_days=7,
            tone=TweetTone.CASUAL,
            custom_instructions="Include code examples",
            search_keywords=["Python", "programming"],
        )

        campaign = await service.create_campaign(
            user_id=test_user.id,
            config=config,
        )

        assert campaign.custom_instructions == "Include code examples"
        assert campaign.search_keywords == ["Python", "programming"]

    @pytest.mark.asyncio
    async def test_get_campaign(self, db_session: AsyncSession, test_user: User):
        """Test getting a campaign by ID."""
        service = CampaignService(db_session)

        # Create a campaign first
        config = CampaignConfig(
            topic="Test Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        created = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        # Retrieve it
        campaign = await service.get_campaign(created.id, test_user.id)
        assert campaign is not None
        assert campaign.id == created.id
        assert campaign.topic == "Test Campaign"

    @pytest.mark.asyncio
    async def test_get_campaign_wrong_user(self, db_session: AsyncSession, test_user: User):
        """Test that users can't access other users' campaigns."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Private Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        created = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        # Try to get with different user ID
        campaign = await service.get_campaign(created.id, uuid4())
        assert campaign is None

    @pytest.mark.asyncio
    async def test_get_user_campaigns(self, db_session: AsyncSession, test_user: User):
        """Test getting all campaigns for a user."""
        service = CampaignService(db_session)

        # Create multiple campaigns
        for i in range(3):
            config = CampaignConfig(
                topic=f"Campaign {i}",
                frequency_per_day=1,
                duration_days=5,
                tone=TweetTone.PROFESSIONAL,
            )
            await service.create_campaign(test_user.id, config)

        await db_session.commit()

        campaigns = await service.get_user_campaigns(test_user.id)
        assert len(campaigns) == 3

    @pytest.mark.asyncio
    async def test_pause_campaign(self, db_session: AsyncSession, test_user: User):
        """Test pausing a campaign."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Pausable Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        paused = await service.pause_campaign(campaign)
        assert paused.status == CampaignStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_non_active_campaign_fails(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that pausing a non-active campaign fails."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Test",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        campaign.status = CampaignStatus.COMPLETED
        await db_session.commit()

        with pytest.raises(CampaignServiceError):
            await service.pause_campaign(campaign)

    @pytest.mark.asyncio
    async def test_resume_campaign(self, db_session: AsyncSession, test_user: User):
        """Test resuming a paused campaign."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Resumable Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await service.pause_campaign(campaign)
        await db_session.commit()

        resumed = await service.resume_campaign(campaign)
        assert resumed.status == CampaignStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_resume_non_paused_campaign_fails(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that resuming a non-paused campaign fails."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Test",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        with pytest.raises(CampaignServiceError):
            await service.resume_campaign(campaign)

    @pytest.mark.asyncio
    async def test_cancel_campaign(self, db_session: AsyncSession, test_user: User):
        """Test cancelling a campaign."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Cancellable Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        cancelled = await service.cancel_campaign(campaign)
        assert cancelled.status == CampaignStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_delete_campaign(self, db_session: AsyncSession, test_user: User):
        """Test soft deleting a campaign."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Deletable Campaign",
            frequency_per_day=1,
            duration_days=5,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        await service.delete_campaign(campaign)
        assert campaign.deleted_at is not None

    @pytest.mark.asyncio
    async def test_get_campaign_tweets(self, db_session: AsyncSession, test_user: User):
        """Test getting tweets for a campaign."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Tweet Test Campaign",
            frequency_per_day=2,
            duration_days=3,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        tweets = await service.get_campaign_tweets(campaign.id)
        # Should have scheduled tweets
        assert len(tweets) > 0
        for tweet in tweets:
            assert tweet.campaign_id == campaign.id
            assert tweet.is_campaign_tweet is True

    @pytest.mark.asyncio
    async def test_get_campaign_stats(self, db_session: AsyncSession, test_user: User):
        """Test getting campaign statistics."""
        service = CampaignService(db_session)

        config = CampaignConfig(
            topic="Stats Campaign",
            frequency_per_day=2,
            duration_days=3,
            tone=TweetTone.PROFESSIONAL,
        )
        campaign = await service.create_campaign(test_user.id, config)
        await db_session.commit()

        stats = await service.get_campaign_stats(campaign.id)
        assert stats["total"] == 6
        assert stats["posted"] == 0
        assert stats["failed"] == 0
        assert stats["status"] == "active"
        assert "progress_percentage" in stats

    @pytest.mark.asyncio
    async def test_get_pending_campaign_tweets(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test getting pending campaign tweets."""
        service = CampaignService(db_session)

        # Create campaign with past scheduled times
        campaign = AutoCampaign(
            user_id=test_user.id,
            name="Test Campaign",
            original_prompt="test",
            topic="test",
            tone=TweetTone.PROFESSIONAL,
            frequency_per_day=1,
            duration_days=1,
            total_tweets=1,
            tweets_posted=0,
            tweets_failed=0,
            start_date=datetime.now(timezone.utc) - timedelta(hours=1),
            end_date=datetime.now(timezone.utc) + timedelta(days=1),
            posting_start_hour=9,
            posting_end_hour=21,
            timezone="UTC",
            status=CampaignStatus.ACTIVE,
            web_search_enabled=True,
        )
        db_session.add(campaign)
        await db_session.flush()

        # Create a tweet that's due
        tweet = ScheduledTweet(
            user_id=test_user.id,
            campaign_id=campaign.id,
            content="",
            is_campaign_tweet=True,
            content_generated=False,
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
            status=TweetStatus.AWAITING_GENERATION,
        )
        db_session.add(tweet)
        await db_session.commit()

        pending = await service.get_pending_campaign_tweets()
        assert len(pending) >= 1
