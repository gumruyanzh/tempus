"""Campaign management service for automated tweet scheduling."""

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

import pytz
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.tweet import ScheduledTweet, TweetStatus, TweetTone
from app.services.deepseek import DeepSeekService

logger = get_logger(__name__)


@dataclass
class CampaignConfig:
    """Parsed campaign configuration from user prompt."""

    topic: str
    frequency_per_day: int
    duration_days: int
    tone: TweetTone
    start_date: Optional[datetime] = None
    custom_instructions: Optional[str] = None
    search_keywords: Optional[List[str]] = None


class CampaignServiceError(Exception):
    """Campaign service error."""

    pass


class CampaignService:
    """Service for campaign management operations."""

    # System prompt for parsing campaign instructions
    PARSE_PROMPT = """You are a campaign configuration parser. Extract scheduling parameters from the user's natural language input.

Output ONLY a valid JSON object with these fields:
- topic: The main topic/subject for tweets (string, required)
- frequency_per_day: Number of tweets per day (integer, default 1)
- duration_days: How many days the campaign runs (integer, default 7)
- tone: One of "professional", "casual", "viral", "thought_leadership" (string, default "professional")
- search_keywords: Additional keywords for research (array of strings, optional)
- custom_instructions: Any special instructions for content style (string, optional)

Duration hints:
- "a week" = 7 days
- "a month" = 30 days
- "two weeks" = 14 days
- "daily" or "every day" = 1 per day
- "twice a day" = 2 per day
- "3 times a day" = 3 per day
- "4 times a day" = 4 per day

Tone hints:
- "viral", "engaging", "attention-grabbing" = "viral"
- "professional", "business" = "professional"
- "casual", "friendly", "conversational" = "casual"
- "thought leader", "expert", "insightful" = "thought_leadership"

Example input: "schedule 4 times a day tweets for a month about AI and agentic development make it viral"
Example output: {"topic": "AI and agentic development", "frequency_per_day": 4, "duration_days": 30, "tone": "viral", "search_keywords": ["AI", "agentic", "AI agents", "autonomous AI"]}

Now parse this input:
"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def parse_campaign_prompt(
        self,
        prompt: str,
        deepseek_service: DeepSeekService,
    ) -> CampaignConfig:
        """
        Parse a natural language prompt into campaign configuration.

        Args:
            prompt: User's natural language campaign description
            deepseek_service: DeepSeek service for AI parsing

        Returns:
            CampaignConfig with extracted parameters
        """
        try:
            # Use DeepSeek to parse the prompt
            response = await deepseek_service._call_api(
                system_prompt="You are a JSON parser. Output ONLY valid JSON, no explanations.",
                user_prompt=self.PARSE_PROMPT + prompt,
            )

            # Clean up response and parse JSON
            response = response.strip()
            # Remove markdown code blocks if present
            if response.startswith("```"):
                response = re.sub(r"```json?\n?", "", response)
                response = response.rstrip("`")

            data = json.loads(response)

            # Map tone string to enum
            tone_str = data.get("tone", "professional").lower()
            tone_map = {
                "professional": TweetTone.PROFESSIONAL,
                "casual": TweetTone.CASUAL,
                "viral": TweetTone.VIRAL,
                "thought_leadership": TweetTone.THOUGHT_LEADERSHIP,
            }
            tone = tone_map.get(tone_str, TweetTone.PROFESSIONAL)

            config = CampaignConfig(
                topic=data.get("topic", prompt),
                frequency_per_day=max(1, min(10, data.get("frequency_per_day", 1))),
                duration_days=max(1, min(90, data.get("duration_days", 7))),
                tone=tone,
                search_keywords=data.get("search_keywords"),
                custom_instructions=data.get("custom_instructions"),
            )

            logger.info(
                "Campaign prompt parsed",
                topic=config.topic[:50],
                frequency=config.frequency_per_day,
                duration=config.duration_days,
                tone=config.tone.value,
            )

            return config

        except json.JSONDecodeError as e:
            logger.error("Failed to parse campaign config JSON", error=str(e))
            # Fallback: use the entire prompt as topic with defaults
            return CampaignConfig(
                topic=prompt,
                frequency_per_day=1,
                duration_days=7,
                tone=TweetTone.PROFESSIONAL,
            )
        except Exception as e:
            logger.error("Failed to parse campaign prompt", error=str(e))
            raise CampaignServiceError(f"Failed to parse campaign: {str(e)}")

    def generate_time_slots(
        self,
        start_date: datetime,
        duration_days: int,
        frequency_per_day: int,
        posting_start_hour: int = 9,
        posting_end_hour: int = 21,
        user_timezone: str = "UTC",
    ) -> List[datetime]:
        """
        Generate smart time slots for campaign tweets.

        Args:
            start_date: Campaign start date
            duration_days: Number of days to schedule
            frequency_per_day: Number of tweets per day
            posting_start_hour: Start hour for posting (default 9am)
            posting_end_hour: End hour for posting (default 9pm)
            user_timezone: User's timezone

        Returns:
            List of datetime objects for each time slot (in UTC)
        """
        slots = []
        tz = pytz.timezone(user_timezone)

        # Calculate posting window in hours
        posting_window = posting_end_hour - posting_start_hour
        if posting_window <= 0:
            posting_window = 12  # Default to 12 hours

        # Calculate interval between tweets
        if frequency_per_day > 1:
            interval_hours = posting_window / frequency_per_day
        else:
            interval_hours = posting_window / 2  # Single tweet in middle of day

        for day in range(duration_days):
            current_date = start_date + timedelta(days=day)

            for slot_num in range(frequency_per_day):
                # Calculate base time for this slot
                if frequency_per_day == 1:
                    # Single tweet: post around midday
                    base_hour = posting_start_hour + posting_window // 2
                else:
                    # Multiple tweets: distribute evenly
                    base_hour = posting_start_hour + (slot_num * interval_hours) + (interval_hours / 2)

                # Add random jitter (+/- 30 minutes) for organic feel
                jitter_minutes = random.randint(-30, 30)

                # Create datetime in user's timezone
                local_dt = tz.localize(datetime(
                    current_date.year,
                    current_date.month,
                    current_date.day,
                    int(base_hour),
                    max(0, min(59, 30 + jitter_minutes)),  # Base 30 min + jitter
                ))

                # Convert to UTC
                utc_dt = local_dt.astimezone(pytz.UTC)

                # Only add future slots
                if utc_dt > datetime.now(timezone.utc):
                    slots.append(utc_dt)

        logger.info(
            "Time slots generated",
            total_slots=len(slots),
            first_slot=slots[0].isoformat() if slots else None,
            last_slot=slots[-1].isoformat() if slots else None,
        )

        return slots

    async def create_campaign(
        self,
        user_id: UUID,
        config: CampaignConfig,
        user_timezone: str = "UTC",
        posting_start_hour: int = 9,
        posting_end_hour: int = 21,
    ) -> AutoCampaign:
        """
        Create a new campaign with scheduled time slots.

        Args:
            user_id: The user ID
            config: Parsed campaign configuration
            user_timezone: User's timezone for scheduling
            posting_start_hour: Start hour for posting
            posting_end_hour: End hour for posting

        Returns:
            Created AutoCampaign with scheduled tweets
        """
        # Calculate start and end dates
        start_date = config.start_date or datetime.now(timezone.utc)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        end_date = start_date + timedelta(days=config.duration_days)

        # Generate campaign name
        name = f"{config.topic[:50]} ({config.frequency_per_day}x/day for {config.duration_days} days)"

        # Calculate total tweets
        total_tweets = config.frequency_per_day * config.duration_days

        # Create the campaign
        campaign = AutoCampaign(
            user_id=user_id,
            name=name,
            original_prompt=config.topic,
            topic=config.topic,
            tone=config.tone,
            frequency_per_day=config.frequency_per_day,
            duration_days=config.duration_days,
            total_tweets=total_tweets,
            tweets_posted=0,
            tweets_failed=0,
            start_date=start_date,
            end_date=end_date,
            posting_start_hour=posting_start_hour,
            posting_end_hour=posting_end_hour,
            timezone=user_timezone,
            status=CampaignStatus.ACTIVE,
            web_search_enabled=True,
            search_keywords=config.search_keywords,
            custom_instructions=config.custom_instructions,
        )

        self.db.add(campaign)
        await self.db.flush()
        await self.db.refresh(campaign)

        # Generate time slots
        slots = self.generate_time_slots(
            start_date=start_date,
            duration_days=config.duration_days,
            frequency_per_day=config.frequency_per_day,
            posting_start_hour=posting_start_hour,
            posting_end_hour=posting_end_hour,
            user_timezone=user_timezone,
        )

        # Create scheduled tweets for each slot (without content)
        for scheduled_time in slots:
            tweet = ScheduledTweet(
                user_id=user_id,
                campaign_id=campaign.id,
                content="",  # Content will be generated at posting time
                is_campaign_tweet=True,
                content_generated=False,
                scheduled_for=scheduled_time,
                timezone=user_timezone,
                status=TweetStatus.AWAITING_GENERATION,
            )
            self.db.add(tweet)

        await self.db.flush()

        logger.info(
            "Campaign created",
            campaign_id=str(campaign.id),
            user_id=str(user_id),
            total_tweets=total_tweets,
            slots_created=len(slots),
        )

        return campaign

    async def get_campaign(
        self,
        campaign_id: UUID,
        user_id: UUID,
    ) -> Optional[AutoCampaign]:
        """Get a campaign by ID."""
        stmt = select(AutoCampaign).where(
            AutoCampaign.id == campaign_id,
            AutoCampaign.user_id == user_id,
            AutoCampaign.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_campaigns(
        self,
        user_id: UUID,
        status: Optional[CampaignStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AutoCampaign]:
        """Get all campaigns for a user."""
        conditions = [
            AutoCampaign.user_id == user_id,
            AutoCampaign.deleted_at.is_(None),
        ]

        if status:
            conditions.append(AutoCampaign.status == status)

        stmt = (
            select(AutoCampaign)
            .where(*conditions)
            .order_by(AutoCampaign.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def pause_campaign(self, campaign: AutoCampaign) -> AutoCampaign:
        """Pause an active campaign."""
        if campaign.status != CampaignStatus.ACTIVE:
            raise CampaignServiceError("Only active campaigns can be paused")

        campaign.pause()
        campaign.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(campaign)

        logger.info("Campaign paused", campaign_id=str(campaign.id))
        return campaign

    async def resume_campaign(self, campaign: AutoCampaign) -> AutoCampaign:
        """Resume a paused campaign."""
        if campaign.status != CampaignStatus.PAUSED:
            raise CampaignServiceError("Only paused campaigns can be resumed")

        campaign.resume()
        campaign.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(campaign)

        logger.info("Campaign resumed", campaign_id=str(campaign.id))
        return campaign

    async def cancel_campaign(self, campaign: AutoCampaign) -> AutoCampaign:
        """Cancel a campaign and all pending tweets."""
        if campaign.status not in [CampaignStatus.ACTIVE, CampaignStatus.PAUSED, CampaignStatus.DRAFT]:
            raise CampaignServiceError("Campaign cannot be cancelled")

        # Cancel all pending/awaiting tweets
        stmt = select(ScheduledTweet).where(
            ScheduledTweet.campaign_id == campaign.id,
            or_(
                ScheduledTweet.status == TweetStatus.PENDING,
                ScheduledTweet.status == TweetStatus.AWAITING_GENERATION,
            ),
        )
        result = await self.db.execute(stmt)
        pending_tweets = result.scalars().all()

        for tweet in pending_tweets:
            tweet.status = TweetStatus.CANCELLED
            tweet.updated_at = datetime.now(timezone.utc)

        campaign.cancel()
        campaign.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(campaign)

        logger.info(
            "Campaign cancelled",
            campaign_id=str(campaign.id),
            cancelled_tweets=len(pending_tweets),
        )
        return campaign

    async def delete_campaign(self, campaign: AutoCampaign) -> None:
        """Soft delete a campaign."""
        # Cancel first if active
        if campaign.status in [CampaignStatus.ACTIVE, CampaignStatus.PAUSED]:
            await self.cancel_campaign(campaign)

        campaign.soft_delete()
        await self.db.flush()
        logger.info("Campaign deleted", campaign_id=str(campaign.id))

    async def get_campaign_tweets(
        self,
        campaign_id: UUID,
        status: Optional[TweetStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScheduledTweet]:
        """Get tweets for a campaign."""
        conditions = [
            ScheduledTweet.campaign_id == campaign_id,
            ScheduledTweet.deleted_at.is_(None),
        ]

        if status:
            conditions.append(ScheduledTweet.status == status)

        stmt = (
            select(ScheduledTweet)
            .where(*conditions)
            .order_by(ScheduledTweet.scheduled_for.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_pending_campaign_tweets(
        self,
        limit: int = 50,
    ) -> List[ScheduledTweet]:
        """Get campaign tweets that are due for content generation and posting."""
        now = datetime.now(timezone.utc)

        stmt = (
            select(ScheduledTweet)
            .where(
                ScheduledTweet.is_campaign_tweet == True,
                ScheduledTweet.status == TweetStatus.AWAITING_GENERATION,
                ScheduledTweet.scheduled_for <= now,
                ScheduledTweet.deleted_at.is_(None),
            )
            .order_by(ScheduledTweet.scheduled_for.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        tweets = list(result.scalars().all())

        # Filter out tweets from paused campaigns
        active_tweets = []
        for tweet in tweets:
            campaign_stmt = select(AutoCampaign).where(
                AutoCampaign.id == tweet.campaign_id,
                AutoCampaign.status == CampaignStatus.ACTIVE,
            )
            campaign_result = await self.db.execute(campaign_stmt)
            if campaign_result.scalar_one_or_none():
                active_tweets.append(tweet)

        return active_tweets

    async def get_campaign_stats(
        self,
        campaign_id: UUID,
    ) -> dict:
        """Get statistics for a campaign."""
        from sqlalchemy import func

        campaign = await self.db.get(AutoCampaign, campaign_id)
        if not campaign:
            return {}

        # Count by status
        stats = {
            "total": campaign.total_tweets,
            "posted": campaign.tweets_posted,
            "failed": campaign.tweets_failed,
            "remaining": campaign.tweets_remaining,
            "progress_percentage": campaign.progress_percentage,
            "status": campaign.status.value,
        }

        # Count awaiting generation
        awaiting_stmt = select(func.count()).where(
            ScheduledTweet.campaign_id == campaign_id,
            ScheduledTweet.status == TweetStatus.AWAITING_GENERATION,
            ScheduledTweet.deleted_at.is_(None),
        )
        awaiting_result = await self.db.execute(awaiting_stmt)
        stats["awaiting_generation"] = awaiting_result.scalar() or 0

        # Count cancelled
        cancelled_stmt = select(func.count()).where(
            ScheduledTweet.campaign_id == campaign_id,
            ScheduledTweet.status == TweetStatus.CANCELLED,
            ScheduledTweet.deleted_at.is_(None),
        )
        cancelled_result = await self.db.execute(cancelled_stmt)
        stats["cancelled"] = cancelled_result.scalar() or 0

        return stats
