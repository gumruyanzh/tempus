"""Tests for campaign Celery tasks."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.tweet import ScheduledTweet, TweetStatus, TweetTone
from app.models.user import User
from app.tasks.campaign_tasks import (
    _check_completed_campaigns_async,
    _generate_and_post_campaign_tweet_async,
    _process_campaign_tweets_async,
)


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
        web_search_enabled=False,
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    return campaign


@pytest.fixture
async def campaign_tweet(
    db_session: AsyncSession, test_user: User, campaign: AutoCampaign
) -> ScheduledTweet:
    """Create a campaign tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        campaign_id=campaign.id,
        content="",  # Content not generated yet
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
        status=TweetStatus.AWAITING_GENERATION,
        is_campaign_tweet=True,
        timezone="UTC",
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


class TestProcessCampaignTweets:
    """Tests for process_campaign_tweets task."""

    @pytest.mark.asyncio
    async def test_no_pending_campaign_tweets(self):
        """Test processing when no pending campaign tweets."""
        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch("app.tasks.campaign_tasks.CampaignService") as mock_svc:
                mock_svc.return_value.get_pending_campaign_tweets = AsyncMock(return_value=[])

                result = await _process_campaign_tweets_async()

                assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_process_campaign_tweets(self):
        """Test processing pending campaign tweets."""
        mock_tweets = [
            MagicMock(id=uuid4()),
            MagicMock(id=uuid4()),
        ]

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch("app.tasks.campaign_tasks.CampaignService") as mock_svc:
                mock_svc.return_value.get_pending_campaign_tweets = AsyncMock(
                    return_value=mock_tweets
                )

                with patch("app.tasks.campaign_tasks.generate_and_post_campaign_tweet") as mock_task:
                    mock_task.delay = MagicMock()

                    result = await _process_campaign_tweets_async()

                    assert result["processed"] == 2
                    assert mock_task.delay.call_count == 2


class TestGenerateAndPostCampaignTweet:
    """Tests for generate_and_post_campaign_tweet task."""

    @pytest.mark.asyncio
    async def test_tweet_not_found(self):
        """Test processing non-existent tweet."""
        mock_task = MagicMock()

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            # Mock execute to return an async result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _generate_and_post_campaign_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is False
            assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_not_campaign_tweet(self):
        """Test processing non-campaign tweet."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.is_campaign_tweet = False

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _generate_and_post_campaign_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is False
            assert "not a campaign tweet" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_status(self):
        """Test processing tweet with wrong status."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.POSTED

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tweet
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _generate_and_post_campaign_tweet_async(mock_task, str(uuid4()))

            assert result["success"] is False
            assert "invalid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_campaign_not_found(self):
        """Test processing tweet with missing campaign."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.AWAITING_GENERATION
        mock_tweet.campaign_id = uuid4()

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            # First call returns tweet, second returns None (no campaign)
            mock_result1 = MagicMock()
            mock_result1.scalar_one_or_none.return_value = mock_tweet
            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

            result = await _generate_and_post_campaign_tweet_async(mock_task, str(mock_tweet.id))

            assert result["success"] is False
            assert "campaign not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_campaign_not_active(self):
        """Test processing tweet for paused campaign."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.AWAITING_GENERATION
        mock_tweet.campaign_id = uuid4()

        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.PAUSED

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result1 = MagicMock()
            mock_result1.scalar_one_or_none.return_value = mock_tweet
            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = mock_campaign
            mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

            result = await _generate_and_post_campaign_tweet_async(mock_task, str(mock_tweet.id))

            assert result["success"] is False
            assert "not active" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_deepseek_key(self):
        """Test processing without DeepSeek API key."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.AWAITING_GENERATION
        mock_tweet.campaign_id = uuid4()

        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.ACTIVE
        mock_campaign.user_id = uuid4()

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result1 = MagicMock()
            mock_result1.scalar_one_or_none.return_value = mock_tweet
            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = mock_campaign
            mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

            with patch("app.tasks.campaign_tasks.UserService") as mock_user_svc:
                mock_user_svc.return_value.get_decrypted_api_key = AsyncMock(return_value=None)

                result = await _generate_and_post_campaign_tweet_async(
                    mock_task, str(mock_tweet.id)
                )

                assert result["success"] is False
                assert "deepseek" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_successful_generation_and_post(self):
        """Test successful tweet generation and posting."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.AWAITING_GENERATION
        mock_tweet.campaign_id = uuid4()
        mock_tweet.user_id = uuid4()

        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.ACTIVE
        mock_campaign.user_id = mock_tweet.user_id
        mock_campaign.topic = "AI Development"
        mock_campaign.web_search_enabled = False
        mock_campaign.tone = TweetTone.PROFESSIONAL
        mock_campaign.custom_instructions = None

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result1 = MagicMock()
            mock_result1.scalar_one_or_none.return_value = mock_tweet
            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = mock_campaign
            mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

            with patch("app.tasks.campaign_tasks.UserService") as mock_user_svc:
                mock_user_svc.return_value.get_decrypted_api_key = AsyncMock(
                    return_value="sk-test-key"
                )

                with patch("app.tasks.campaign_tasks.DeepSeekService") as mock_deepseek:
                    mock_deepseek_instance = AsyncMock()
                    mock_deepseek.return_value = mock_deepseek_instance
                    mock_deepseek_instance.generate_tweet = AsyncMock(
                        return_value="Generated tweet content!"
                    )
                    mock_deepseek_instance.close = AsyncMock()

                    with patch("app.tasks.campaign_tasks.CampaignService") as mock_campaign_svc:
                        mock_campaign_svc.return_value.get_campaign_tweets = AsyncMock(
                            return_value=[]
                        )

                        with patch("app.tasks.campaign_tasks.TwitterService") as mock_twitter:
                            mock_twitter_instance = AsyncMock()
                            mock_twitter.return_value = mock_twitter_instance
                            mock_twitter_instance.get_valid_access_token = AsyncMock(
                                return_value="access_token"
                            )
                            mock_twitter_instance.post_tweet = AsyncMock(
                                return_value={"data": {"id": "12345"}}
                            )
                            mock_twitter_instance.close = AsyncMock()

                            with patch("app.tasks.campaign_tasks.TweetService") as mock_tweet_svc:
                                mock_tweet_svc.return_value.create_execution_log = AsyncMock(
                                    return_value=MagicMock()
                                )

                                with patch("app.tasks.campaign_tasks.AuditService") as mock_audit:
                                    mock_audit_instance = AsyncMock()
                                    mock_audit.return_value = mock_audit_instance
                                    mock_audit_instance.log_tweet_posted = AsyncMock()

                                    result = await _generate_and_post_campaign_tweet_async(
                                        mock_task, str(mock_tweet.id)
                                    )

                                    assert result["success"] is True
                                    assert result["twitter_tweet_id"] == "12345"

    @pytest.mark.asyncio
    async def test_with_web_search(self):
        """Test tweet generation with web search enabled."""
        mock_task = MagicMock()
        mock_tweet = MagicMock()
        mock_tweet.id = uuid4()
        mock_tweet.is_campaign_tweet = True
        mock_tweet.status = TweetStatus.AWAITING_GENERATION
        mock_tweet.campaign_id = uuid4()
        mock_tweet.user_id = uuid4()

        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.ACTIVE
        mock_campaign.user_id = mock_tweet.user_id
        mock_campaign.topic = "AI Development"
        mock_campaign.web_search_enabled = True
        mock_campaign.search_keywords = ["machine learning", "neural networks"]
        mock_campaign.tone = TweetTone.VIRAL
        mock_campaign.custom_instructions = None

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result1 = MagicMock()
            mock_result1.scalar_one_or_none.return_value = mock_tweet
            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = mock_campaign
            mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

            with patch("app.tasks.campaign_tasks.UserService") as mock_user_svc:
                # Return both DeepSeek and Tavily keys
                mock_user_svc.return_value.get_decrypted_api_key = AsyncMock(
                    side_effect=["sk-deepseek-key", "tvly-tavily-key"]
                )

                with patch("app.tasks.campaign_tasks.WebSearchService") as mock_web_search:
                    mock_search_instance = AsyncMock()
                    mock_web_search.return_value = mock_search_instance
                    mock_search_instance.search_news = AsyncMock(
                        return_value=[MagicMock(title="News Article", content="Content")]
                    )
                    mock_search_instance.format_results_for_prompt = MagicMock(
                        return_value="News context"
                    )
                    mock_search_instance.close = AsyncMock()

                    with patch("app.tasks.campaign_tasks.DeepSeekService") as mock_deepseek:
                        mock_deepseek_instance = AsyncMock()
                        mock_deepseek.return_value = mock_deepseek_instance
                        mock_deepseek_instance.generate_tweet = AsyncMock(
                            return_value="Trending AI news tweet!"
                        )
                        mock_deepseek_instance.close = AsyncMock()

                        with patch(
                            "app.tasks.campaign_tasks.CampaignService"
                        ) as mock_campaign_svc:
                            mock_campaign_svc.return_value.get_campaign_tweets = AsyncMock(
                                return_value=[]
                            )

                            with patch(
                                "app.tasks.campaign_tasks.TwitterService"
                            ) as mock_twitter:
                                mock_twitter_instance = AsyncMock()
                                mock_twitter.return_value = mock_twitter_instance
                                mock_twitter_instance.get_valid_access_token = AsyncMock(
                                    return_value="access_token"
                                )
                                mock_twitter_instance.post_tweet = AsyncMock(
                                    return_value={"data": {"id": "67890"}}
                                )
                                mock_twitter_instance.close = AsyncMock()

                                with patch(
                                    "app.tasks.campaign_tasks.TweetService"
                                ) as mock_tweet_svc:
                                    mock_tweet_svc.return_value.create_execution_log = AsyncMock(
                                        return_value=MagicMock()
                                    )

                                    with patch("app.tasks.campaign_tasks.AuditService") as mock_audit:
                                        mock_audit_instance = AsyncMock()
                                        mock_audit.return_value = mock_audit_instance
                                        mock_audit_instance.log_tweet_posted = AsyncMock()

                                        result = await _generate_and_post_campaign_tweet_async(
                                            mock_task, str(mock_tweet.id)
                                        )

                                        assert result["success"] is True
                                        mock_search_instance.search_news.assert_called_once()


class TestCheckCompletedCampaigns:
    """Tests for check_completed_campaigns task."""

    @pytest.mark.asyncio
    async def test_no_completed_campaigns(self):
        """Test when no campaigns are complete."""
        mock_campaign = MagicMock()
        mock_campaign.is_complete = False

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_campaign]
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _check_completed_campaigns_async()

            assert result["completed"] == 0

    @pytest.mark.asyncio
    async def test_mark_completed_campaigns(self):
        """Test marking completed campaigns."""
        mock_campaign1 = MagicMock()
        mock_campaign1.is_complete = True
        mock_campaign1.tweets_posted = 14
        mock_campaign1.tweets_failed = 0
        mock_campaign1.id = uuid4()

        mock_campaign2 = MagicMock()
        mock_campaign2.is_complete = False

        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [
                mock_campaign1,
                mock_campaign2,
            ]
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _check_completed_campaigns_async()

            assert result["completed"] == 1
            mock_campaign1.mark_completed.assert_called_once()
            mock_campaign2.mark_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_active_campaigns(self):
        """Test when no active campaigns exist."""
        with patch("app.tasks.campaign_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _check_completed_campaigns_async()

            assert result["completed"] == 0
