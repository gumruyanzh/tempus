"""Growth strategy service for Twitter account growth automation."""

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.growth_strategy import (
    ActionType,
    DailyProgress,
    EngagementLog,
    EngagementStatus,
    EngagementTarget,
    GrowthStrategy,
    StrategyStatus,
    TargetType,
    VerificationStatus,
)
from app.models.user import APIKeyType
from app.services.deepseek import DeepSeekService
from app.services.rate_limiter import EngagementRateLimiter
from app.services.twitter import TwitterService
from app.services.user import UserService
from app.services.web_search import WebSearchService

logger = get_logger(__name__)


@dataclass
class StrategyConfig:
    """Configuration parsed from user prompt."""

    duration_days: int
    niche_keywords: list[str]
    target_accounts: list[str]
    daily_follows: int
    daily_likes: int
    daily_retweets: int
    daily_replies: int
    engagement_hours_start: int
    engagement_hours_end: int
    timezone: str
    name: str


class GrowthStrategyService:
    """Service for managing Twitter growth strategies."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ========== Strategy Creation ==========

    async def parse_strategy_prompt(
        self,
        prompt: str,
        api_key: str,
    ) -> StrategyConfig:
        """Parse a natural language prompt into strategy configuration.

        Args:
            prompt: User's natural language strategy request
            api_key: DeepSeek API key

        Returns:
            Parsed StrategyConfig
        """
        deepseek = DeepSeekService(api_key)

        try:
            system_prompt = """You are an expert Twitter growth strategist. Parse the user's growth strategy request into a structured JSON format.

Extract the following:
- duration_days: Number of days (30 for "1 month", 90 for "3 months", 180 for "6 months", 365 for "1 year")
- niche_keywords: List of topics/niches to focus on (extract from the prompt)
- target_accounts: List of accounts to engage with (if mentioned, otherwise empty)
- daily_follows: Recommended daily follows (50-200 based on aggressiveness)
- daily_likes: Recommended daily likes (100-400 based on aggressiveness)
- daily_retweets: Recommended daily retweets (5-20)
- daily_replies: Recommended daily replies (10-30)
- name: Short name for the strategy (max 50 chars)

Consider the user's goals and adjust quotas accordingly:
- "aggressive" = higher daily numbers
- "safe" or "organic" = lower daily numbers
- Default to moderate numbers

Output ONLY valid JSON, no explanation."""

            user_prompt = f"""Parse this growth strategy request:

"{prompt}"

Output JSON:"""

            response = await deepseek._call_api(system_prompt, user_prompt)

            # Parse JSON response
            try:
                # Clean up response if needed
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]

                data = json.loads(response.strip())

                return StrategyConfig(
                    duration_days=data.get("duration_days", 90),
                    niche_keywords=data.get("niche_keywords", []),
                    target_accounts=data.get("target_accounts", []),
                    daily_follows=min(data.get("daily_follows", 100), 400),
                    daily_likes=min(data.get("daily_likes", 200), 1000),
                    daily_retweets=min(data.get("daily_retweets", 10), 50),
                    daily_replies=min(data.get("daily_replies", 20), 50),
                    engagement_hours_start=9,
                    engagement_hours_end=21,
                    timezone="UTC",
                    name=data.get("name", "Growth Strategy")[:50],
                )

            except json.JSONDecodeError:
                logger.error("Failed to parse strategy JSON", response=response)
                # Return default config
                return StrategyConfig(
                    duration_days=90,
                    niche_keywords=[],
                    target_accounts=[],
                    daily_follows=100,
                    daily_likes=200,
                    daily_retweets=10,
                    daily_replies=20,
                    engagement_hours_start=9,
                    engagement_hours_end=21,
                    timezone="UTC",
                    name="Growth Strategy",
                )

        finally:
            await deepseek.close()

    async def create_strategy(
        self,
        user_id: UUID,
        config: StrategyConfig,
        original_prompt: str,
        verification_status: VerificationStatus = VerificationStatus.NONE,
        starting_followers: int = 0,
    ) -> GrowthStrategy:
        """Create a new growth strategy.

        Args:
            user_id: User ID
            config: Parsed strategy configuration
            original_prompt: Original user prompt
            verification_status: Account verification status
            starting_followers: Current follower count

        Returns:
            Created GrowthStrategy
        """
        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=config.duration_days)

        # Set character limit based on verification
        char_limit = 10000 if verification_status != VerificationStatus.NONE else 280

        strategy = GrowthStrategy(
            user_id=user_id,
            name=config.name,
            original_prompt=original_prompt,
            verification_status=verification_status,
            tweet_char_limit=char_limit,
            starting_followers=starting_followers,
            current_followers=starting_followers,
            duration_days=config.duration_days,
            start_date=now,
            end_date=end_date,
            status=StrategyStatus.DRAFT,
            daily_follows=config.daily_follows,
            daily_likes=config.daily_likes,
            daily_retweets=config.daily_retweets,
            daily_replies=config.daily_replies,
            niche_keywords=config.niche_keywords,
            target_accounts=config.target_accounts,
            engagement_hours_start=config.engagement_hours_start,
            engagement_hours_end=config.engagement_hours_end,
            timezone=config.timezone,
        )

        self.db.add(strategy)
        await self.db.flush()
        await self.db.refresh(strategy)

        logger.info(
            "Growth strategy created",
            strategy_id=str(strategy.id),
            duration_days=config.duration_days,
        )

        return strategy

    async def generate_ai_plan(
        self,
        strategy: GrowthStrategy,
        api_key: str,
    ) -> dict[str, Any]:
        """Generate a detailed AI strategy plan.

        Args:
            strategy: The growth strategy
            api_key: DeepSeek API key

        Returns:
            AI-generated strategy plan
        """
        deepseek = DeepSeekService(api_key)

        try:
            # Build research context
            research_context = ""
            user_service = UserService(self.db)
            tavily_key = await user_service.get_decrypted_api_key(
                strategy.user_id, APIKeyType.TAVILY
            )

            if tavily_key:
                web_search = WebSearchService(tavily_key)
                try:
                    # Search for growth strategies in the niche
                    query = f"Twitter growth strategies {' '.join(strategy.niche_keywords[:3] if strategy.niche_keywords else [])}"
                    results = await web_search.search_news(query, max_results=5)
                    if results:
                        research_context = web_search.format_results_for_prompt(results)
                finally:
                    await web_search.close()

            system_prompt = """You are an expert Twitter growth strategist with deep knowledge of the platform's algorithms, engagement patterns, and growth tactics.

Create a detailed, actionable growth plan based on the user's parameters. Be specific and practical.

Output valid JSON with this structure:
{
    "strategy_summary": "Brief 2-3 sentence overview",
    "weekly_focus": [
        {"week": 1, "focus": "...", "goals": ["...", "..."]},
        ...
    ],
    "content_themes": ["theme1", "theme2", ...],
    "engagement_tactics": [
        {"tactic": "...", "frequency": "...", "expected_impact": "high/medium/low"},
        ...
    ],
    "target_profile_criteria": [
        "Follows accounts in [niche]",
        "Has 1K-50K followers",
        ...
    ],
    "reply_guidelines": [
        "Add value, don't just agree",
        "Ask thoughtful questions",
        ...
    ],
    "milestone_targets": [
        {"day": 30, "followers": X, "engagement_rate": X},
        {"day": 60, "followers": X, "engagement_rate": X},
        ...
    ],
    "risk_warnings": ["...", "..."],
    "daily_schedule": {
        "9am": "Check trending topics",
        "10am": "First engagement session",
        ...
    }
}"""

            user_prompt = f"""Create a Twitter growth strategy plan:

Account Status:
- Verification: {strategy.verification_status.value}
- Character limit: {strategy.tweet_char_limit}
- Starting followers: {strategy.starting_followers}

Strategy Parameters:
- Duration: {strategy.duration_days} days
- Niche/Keywords: {', '.join(strategy.niche_keywords or ['general'])}
- Target accounts to engage with: {', '.join(strategy.target_accounts or ['various industry leaders'])}
- Daily quotas:
  - Follows: {strategy.daily_follows}
  - Likes: {strategy.daily_likes}
  - Retweets: {strategy.daily_retweets}
  - Replies: {strategy.daily_replies}

User's original request: "{strategy.original_prompt}"

{f"Recent research on growth strategies:{chr(10)}{research_context}" if research_context else ""}

Generate a comprehensive plan. Output ONLY valid JSON."""

            response = await deepseek._call_api(system_prompt, user_prompt)

            # Parse JSON
            try:
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]

                plan = json.loads(response.strip())

                # Save to strategy
                strategy.strategy_plan = plan
                await self.db.flush()

                logger.info("AI strategy plan generated", strategy_id=str(strategy.id))
                return plan

            except json.JSONDecodeError:
                logger.error("Failed to parse strategy plan JSON")
                return {"error": "Failed to generate plan", "raw": response[:500]}

        finally:
            await deepseek.close()

    async def estimate_results(
        self,
        strategy: GrowthStrategy,
    ) -> dict[str, Any]:
        """Estimate growth results based on strategy parameters.

        Uses research-based conversion rates and compound growth.
        """
        # Base conversion rates (from research)
        follow_conversion = 0.02  # 2% of follows follow back
        like_conversion = 0.005   # 0.5% of likes lead to follows
        reply_conversion = 0.03  # 3% of quality replies lead to follows

        # Quality multiplier based on strategy completeness
        quality_score = 0.7
        if strategy.niche_keywords:
            quality_score += 0.1
        if strategy.target_accounts:
            quality_score += 0.1
        if strategy.strategy_plan:
            quality_score += 0.1

        # Calculate daily new followers
        daily_follows_from_following = strategy.daily_follows * follow_conversion
        daily_follows_from_likes = strategy.daily_likes * like_conversion
        daily_follows_from_replies = strategy.daily_replies * reply_conversion

        base_daily_new = (
            daily_follows_from_following +
            daily_follows_from_likes +
            daily_follows_from_replies
        ) * quality_score

        # Compound growth simulation
        milestones = []
        current = strategy.starting_followers
        total_gained = 0

        for day in range(1, strategy.duration_days + 1):
            # Compound bonus: larger accounts grow faster
            compound_bonus = 1 + (current / 10000) * 0.1
            daily_new = base_daily_new * compound_bonus

            total_gained += daily_new
            current += daily_new

            # Record milestones
            if day in [7, 14, 30, 60, 90, 180, 365]:
                milestones.append({
                    "day": day,
                    "estimated_followers": int(current),
                    "total_gained": int(total_gained),
                    "growth_percentage": round((total_gained / max(strategy.starting_followers, 1)) * 100, 1),
                })

        # Calculate expected engagement rate
        base_engagement = 3.0  # Base 3%
        engagement_bonus = quality_score * 2  # Up to 2% bonus
        expected_engagement = base_engagement + engagement_bonus

        # Calculate total engagements over the duration
        total_engagements = (
            strategy.daily_follows +
            strategy.daily_likes +
            strategy.daily_retweets +
            strategy.daily_replies
        ) * strategy.duration_days

        # Calculate overall conversion rate
        conversion_rate = total_gained / max(total_engagements, 1)

        results = {
            "estimated_new_followers": int(total_gained),
            "estimated_total_followers": int(current),
            "estimated_engagement_rate": round(expected_engagement, 1),
            "daily_growth_rate": round(base_daily_new, 2),
            "total_engagements": total_engagements,
            "conversion_rate": round(conversion_rate, 4),
            "milestones": milestones,
            "confidence": "medium",
            "confidence_level": "medium",
            "key_factors": [
                "Consistency of engagement",
                "Quality of replies",
                "Niche relevance",
                "Posting frequency",
            ],
        }

        strategy.estimated_results = results
        strategy.target_followers = int(current)
        strategy.target_engagement_rate = expected_engagement
        await self.db.flush()

        logger.info(
            "Growth estimates calculated",
            strategy_id=str(strategy.id),
            estimated_followers=results["estimated_new_followers"],
        )

        return results

    # ========== Target Discovery ==========

    async def find_target_accounts(
        self,
        strategy: GrowthStrategy,
        twitter_service: TwitterService,
        access_token: str,
        limit: int = 50,
    ) -> list[EngagementTarget]:
        """Find accounts to engage with based on strategy.

        Args:
            strategy: The growth strategy
            twitter_service: Twitter service instance
            access_token: Valid access token
            limit: Maximum targets to find

        Returns:
            List of created EngagementTargets
        """
        targets = []

        # Search for users based on niche keywords
        for keyword in (strategy.niche_keywords or [])[:3]:
            try:
                users = await twitter_service.search_users(
                    access_token=access_token,
                    query=keyword,
                    max_results=20,
                )

                for user in users:
                    if len(targets) >= limit:
                        break

                    # Check if already a target
                    username = user.get("username")
                    if not username:
                        continue

                    existing = await self._get_target_by_username(strategy.id, username)
                    if existing:
                        continue

                    # Create target
                    target = EngagementTarget(
                        strategy_id=strategy.id,
                        target_type=TargetType.ACCOUNT,
                        twitter_username=username,
                        should_follow=True,
                        status=EngagementStatus.PENDING,
                        scheduled_for=self._get_next_engagement_slot(strategy),
                        relevance_score=0.7,  # Default relevance
                        priority=len(targets),
                    )
                    self.db.add(target)
                    targets.append(target)

            except Exception as e:
                logger.warning(
                    "Error finding target accounts",
                    keyword=keyword,
                    error=str(e),
                )

        # Also check target accounts from strategy
        for username in (strategy.target_accounts or [])[:10]:
            if len(targets) >= limit:
                break

            # Get followers of target accounts
            try:
                user_data = await twitter_service.get_user_by_username(
                    access_token=access_token,
                    username=username.lstrip("@"),
                )
                if user_data and user_data.get("data"):
                    user_id = user_data["data"].get("id")
                    if user_id:
                        followers = await twitter_service.get_followers(
                            access_token=access_token,
                            user_id=user_id,
                            max_results=30,
                        )

                        for follower in followers.get("data", [])[:10]:
                            if len(targets) >= limit:
                                break

                            follower_username = follower.get("username")
                            if not follower_username:
                                continue

                            existing = await self._get_target_by_username(
                                strategy.id, follower_username
                            )
                            if existing:
                                continue

                            target = EngagementTarget(
                                strategy_id=strategy.id,
                                target_type=TargetType.ACCOUNT,
                                twitter_user_id=follower.get("id"),
                                twitter_username=follower_username,
                                follower_count=follower.get("public_metrics", {}).get("followers_count"),
                                following_count=follower.get("public_metrics", {}).get("following_count"),
                                bio=follower.get("description"),
                                should_follow=True,
                                status=EngagementStatus.PENDING,
                                scheduled_for=self._get_next_engagement_slot(strategy),
                                relevance_score=0.8,  # Higher relevance for followers of target accounts
                                priority=len(targets),
                            )
                            self.db.add(target)
                            targets.append(target)

            except Exception as e:
                logger.warning(
                    "Error getting followers of target account",
                    username=username,
                    error=str(e),
                )

        await self.db.flush()

        logger.info(
            "Found target accounts",
            strategy_id=str(strategy.id),
            count=len(targets),
        )

        return targets

    async def find_engagement_tweets(
        self,
        strategy: GrowthStrategy,
        twitter_service: TwitterService,
        access_token: str,
        limit: int = 30,
    ) -> list[EngagementTarget]:
        """Find tweets to engage with (like, retweet, reply).

        Args:
            strategy: The growth strategy
            twitter_service: Twitter service instance
            access_token: Valid access token
            limit: Maximum targets to find

        Returns:
            List of created EngagementTargets
        """
        targets = []

        # Search for popular tweets in niche
        for keyword in (strategy.niche_keywords or ["twitter growth"])[:3]:
            try:
                tweets = await twitter_service.get_popular_tweets_about_topic(
                    access_token=access_token,
                    topic=keyword,
                    max_results=20,
                )

                for tweet in tweets:
                    if len(targets) >= limit:
                        break

                    tweet_id = tweet.get("id")
                    if not tweet_id:
                        continue

                    # Check if already a target
                    existing = await self._get_target_by_tweet_id(strategy.id, tweet_id)
                    if existing:
                        continue

                    # Determine actions based on engagement metrics
                    metrics = tweet.get("metrics", {})
                    like_count = metrics.get("like_count", 0)
                    retweet_count = metrics.get("retweet_count", 0)

                    should_like = True
                    should_retweet = like_count > 100 and retweet_count > 20  # Only RT popular tweets
                    should_reply = like_count > 50  # Reply to moderately popular tweets

                    # Calculate relevance score
                    engagement_score = (like_count + retweet_count * 3) / 1000
                    relevance = min(0.9, 0.5 + engagement_score)

                    target = EngagementTarget(
                        strategy_id=strategy.id,
                        target_type=TargetType.TWEET,
                        tweet_id=tweet_id,
                        tweet_author=tweet.get("author_username"),
                        tweet_content=tweet.get("text", "")[:500],
                        tweet_like_count=like_count,
                        tweet_retweet_count=retweet_count,
                        should_like=should_like,
                        should_retweet=should_retweet,
                        should_reply=should_reply,
                        status=EngagementStatus.PENDING,
                        scheduled_for=self._get_next_engagement_slot(strategy),
                        relevance_score=relevance,
                        priority=len(targets),
                    )
                    self.db.add(target)
                    targets.append(target)

            except Exception as e:
                logger.warning(
                    "Error finding engagement tweets",
                    keyword=keyword,
                    error=str(e),
                )

        await self.db.flush()

        logger.info(
            "Found engagement tweets",
            strategy_id=str(strategy.id),
            count=len(targets),
        )

        return targets

    async def generate_reply_content(
        self,
        target: EngagementTarget,
        strategy: GrowthStrategy,
        api_key: str,
    ) -> str:
        """Generate AI reply content for a tweet.

        Args:
            target: The engagement target (tweet)
            strategy: The growth strategy
            api_key: DeepSeek API key

        Returns:
            Generated reply text
        """
        deepseek = DeepSeekService(api_key)

        try:
            system_prompt = """You are a thoughtful Twitter user engaging authentically with content in your niche.

Write a reply that:
1. Adds value to the conversation
2. Shows genuine interest
3. Doesn't sound like a bot or generic comment
4. Is under 280 characters
5. Avoids generic phrases like "Great post!" or "Thanks for sharing!"
6. Either asks a thoughtful question, shares a relevant insight, or adds a personal perspective

Be conversational and human."""

            # Get reply guidelines from strategy plan
            guidelines = ""
            if strategy.strategy_plan and "reply_guidelines" in strategy.strategy_plan:
                guidelines = "\n".join(strategy.strategy_plan["reply_guidelines"])

            user_prompt = f"""Write a reply to this tweet:

Tweet by @{target.tweet_author}:
"{target.tweet_content}"

Context/Niche: {', '.join(strategy.niche_keywords or ['general topics'])}

{f"Guidelines:{chr(10)}{guidelines}" if guidelines else ""}

Output ONLY the reply text, nothing else."""

            response = await deepseek._call_api(system_prompt, user_prompt)
            reply = response.strip()

            # Clean up
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1]

            # Ensure under character limit
            if len(reply) > strategy.tweet_char_limit:
                reply = reply[:strategy.tweet_char_limit - 3] + "..."

            # Save to target
            target.reply_content = reply
            await self.db.flush()

            logger.info(
                "Reply generated",
                target_id=str(target.id),
                reply_length=len(reply),
            )

            return reply

        finally:
            await deepseek.close()

    # ========== Strategy Management ==========

    async def get_user_strategies(
        self,
        user_id: UUID,
        status: Optional[StrategyStatus] = None,
    ) -> list[GrowthStrategy]:
        """Get all strategies for a user."""
        stmt = select(GrowthStrategy).where(
            GrowthStrategy.user_id == user_id,
            GrowthStrategy.deleted_at.is_(None),
        )

        if status:
            stmt = stmt.where(GrowthStrategy.status == status)

        stmt = stmt.order_by(GrowthStrategy.created_at.desc())

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_strategy(
        self,
        strategy_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[GrowthStrategy]:
        """Get a strategy by ID."""
        stmt = select(GrowthStrategy).where(
            GrowthStrategy.id == strategy_id,
            GrowthStrategy.deleted_at.is_(None),
        )

        if user_id:
            stmt = stmt.where(GrowthStrategy.user_id == user_id)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def activate_strategy(self, strategy_id: UUID) -> GrowthStrategy:
        """Activate a draft strategy."""
        strategy = await self.get_strategy(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")

        strategy.activate()
        await self.db.flush()

        logger.info("Strategy activated", strategy_id=str(strategy_id))
        return strategy

    async def pause_strategy(self, strategy_id: UUID) -> GrowthStrategy:
        """Pause an active strategy."""
        strategy = await self.get_strategy(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")

        strategy.pause()
        await self.db.flush()

        logger.info("Strategy paused", strategy_id=str(strategy_id))
        return strategy

    async def resume_strategy(self, strategy_id: UUID) -> GrowthStrategy:
        """Resume a paused strategy."""
        strategy = await self.get_strategy(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")

        strategy.resume()
        await self.db.flush()

        logger.info("Strategy resumed", strategy_id=str(strategy_id))
        return strategy

    async def cancel_strategy(self, strategy_id: UUID) -> GrowthStrategy:
        """Cancel a strategy."""
        strategy = await self.get_strategy(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")

        strategy.cancel()
        await self.db.flush()

        logger.info("Strategy cancelled", strategy_id=str(strategy_id))
        return strategy

    # ========== Progress & Analytics ==========

    async def update_follower_count(
        self,
        strategy: GrowthStrategy,
        twitter_service: TwitterService,
        access_token: str,
    ) -> int:
        """Update current follower count from Twitter."""
        try:
            metrics = await twitter_service.get_user_metrics(access_token)
            followers = metrics.get("data", {}).get("public_metrics", {}).get("followers_count", 0)

            strategy.update_followers(followers)
            await self.db.flush()

            logger.info(
                "Follower count updated",
                strategy_id=str(strategy.id),
                followers=followers,
            )

            return followers

        except Exception as e:
            logger.warning(
                "Failed to update follower count",
                strategy_id=str(strategy.id),
                error=str(e),
            )
            return strategy.current_followers

    async def record_daily_progress(
        self,
        strategy: GrowthStrategy,
    ) -> DailyProgress:
        """Record daily progress for a strategy."""
        today = date.today()

        # Check if already recorded today
        stmt = select(DailyProgress).where(
            DailyProgress.strategy_id == strategy.id,
            DailyProgress.date == today,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return existing

        # Count today's actions from logs
        today_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        today_end = today_start + timedelta(days=1)

        actions = await self._count_actions_in_range(
            strategy.id, today_start, today_end
        )

        progress = DailyProgress(
            strategy_id=strategy.id,
            date=today,
            follows_done=actions.get(ActionType.FOLLOW, 0),
            unfollows_done=actions.get(ActionType.UNFOLLOW, 0),
            likes_done=actions.get(ActionType.LIKE, 0),
            retweets_done=actions.get(ActionType.RETWEET, 0),
            replies_done=actions.get(ActionType.REPLY, 0),
            follower_count=strategy.current_followers,
            following_count=0,  # Would need to fetch from Twitter
            engagement_rate=strategy.target_engagement_rate,
        )

        self.db.add(progress)
        await self.db.flush()
        await self.db.refresh(progress)

        return progress

    async def get_strategy_analytics(
        self,
        strategy_id: UUID,
    ) -> dict[str, Any]:
        """Get detailed analytics for a strategy."""
        strategy = await self.get_strategy(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")

        # Get daily progress data
        stmt = select(DailyProgress).where(
            DailyProgress.strategy_id == strategy_id,
        ).order_by(DailyProgress.date.desc()).limit(30)

        result = await self.db.execute(stmt)
        daily_progress = list(result.scalars().all())

        # Get action counts
        total_actions = {
            "follows": strategy.total_follows,
            "unfollows": strategy.total_unfollows,
            "likes": strategy.total_likes,
            "retweets": strategy.total_retweets,
            "replies": strategy.total_replies,
        }

        # Calculate growth rate
        if len(daily_progress) >= 2:
            oldest = daily_progress[-1]
            newest = daily_progress[0]
            days_diff = (newest.date - oldest.date).days or 1
            followers_diff = newest.follower_count - oldest.follower_count
            daily_growth = followers_diff / days_diff
        else:
            daily_growth = 0

        # Calculate days elapsed
        days_elapsed = strategy.duration_days - strategy.days_remaining

        return {
            "strategy_id": str(strategy_id),
            "status": strategy.status.value,
            "duration_days": strategy.duration_days,
            "days_remaining": strategy.days_remaining,
            "days_elapsed": days_elapsed,
            "progress_percentage": strategy.progress_percentage,
            "starting_followers": strategy.starting_followers,
            "current_followers": strategy.current_followers,
            "followers_gained": strategy.followers_gained,
            "follower_growth_rate": strategy.follower_growth_rate,
            "daily_growth": round(daily_growth, 2),
            "total_engagements": strategy.total_engagements,
            "total_actions": total_actions,
            "total_actions_count": sum(total_actions.values()),
            "estimated_results": strategy.estimated_results,
            "daily_progress": [
                {
                    "date": p.date.isoformat(),
                    "follower_count": p.follower_count,
                    "engagements": p.total_engagements,
                }
                for p in daily_progress
            ],
        }

    async def log_engagement(
        self,
        strategy_id: UUID,
        action_type: ActionType,
        success: bool,
        twitter_user_id: Optional[str] = None,
        twitter_username: Optional[str] = None,
        tweet_id: Optional[str] = None,
        error_message: Optional[str] = None,
        reply_content: Optional[str] = None,
        reply_tweet_id: Optional[str] = None,
    ) -> EngagementLog:
        """Log an engagement action."""
        log = EngagementLog(
            strategy_id=strategy_id,
            action_type=action_type,
            twitter_user_id=twitter_user_id,
            twitter_username=twitter_username,
            tweet_id=tweet_id,
            success=success,
            error_message=error_message,
            reply_content=reply_content,
            reply_tweet_id=reply_tweet_id,
        )

        self.db.add(log)
        await self.db.flush()

        # Update strategy counters if successful
        if success:
            strategy = await self.get_strategy(strategy_id)
            if strategy:
                if action_type == ActionType.FOLLOW:
                    strategy.increment_follows()
                elif action_type == ActionType.UNFOLLOW:
                    strategy.increment_unfollows()
                elif action_type == ActionType.LIKE:
                    strategy.increment_likes()
                elif action_type == ActionType.RETWEET:
                    strategy.increment_retweets()
                elif action_type == ActionType.REPLY:
                    strategy.increment_replies()

                await self.db.flush()

        return log

    # ========== Engagement Targets ==========

    async def get_pending_targets(
        self,
        strategy_id: UUID,
        limit: int = 20,
    ) -> list[EngagementTarget]:
        """Get pending engagement targets for a strategy."""
        now = datetime.now(timezone.utc)

        stmt = select(EngagementTarget).where(
            EngagementTarget.strategy_id == strategy_id,
            EngagementTarget.status == EngagementStatus.PENDING,
            EngagementTarget.scheduled_for <= now,
        ).order_by(
            EngagementTarget.priority.asc(),
            EngagementTarget.scheduled_for.asc(),
        ).limit(limit)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_active_strategies(self) -> list[GrowthStrategy]:
        """Get all active strategies."""
        stmt = select(GrowthStrategy).where(
            GrowthStrategy.status == StrategyStatus.ACTIVE,
            GrowthStrategy.deleted_at.is_(None),
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ========== Helper Methods ==========

    async def _get_target_by_username(
        self,
        strategy_id: UUID,
        username: str,
    ) -> Optional[EngagementTarget]:
        """Get existing target by Twitter username."""
        stmt = select(EngagementTarget).where(
            EngagementTarget.strategy_id == strategy_id,
            EngagementTarget.twitter_username == username,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_target_by_tweet_id(
        self,
        strategy_id: UUID,
        tweet_id: str,
    ) -> Optional[EngagementTarget]:
        """Get existing target by tweet ID."""
        stmt = select(EngagementTarget).where(
            EngagementTarget.strategy_id == strategy_id,
            EngagementTarget.tweet_id == tweet_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _get_next_engagement_slot(
        self,
        strategy: GrowthStrategy,
    ) -> datetime:
        """Calculate next engagement time slot with natural randomization."""
        now = datetime.now(timezone.utc)

        # If within engagement hours, schedule soon with random delay
        hour = now.hour
        if strategy.engagement_hours_start <= hour < strategy.engagement_hours_end:
            # Random delay 1-15 minutes
            delay = random.randint(60, 900)
            return now + timedelta(seconds=delay)

        # Otherwise schedule for start of next engagement window
        if hour >= strategy.engagement_hours_end:
            # Tomorrow
            next_day = now + timedelta(days=1)
            return next_day.replace(
                hour=strategy.engagement_hours_start,
                minute=random.randint(0, 30),
                second=0,
                microsecond=0,
            )
        else:
            # Today at start hour
            return now.replace(
                hour=strategy.engagement_hours_start,
                minute=random.randint(0, 30),
                second=0,
                microsecond=0,
            )

    async def _count_actions_in_range(
        self,
        strategy_id: UUID,
        start: datetime,
        end: datetime,
    ) -> dict[ActionType, int]:
        """Count engagement actions in a time range."""
        stmt = select(
            EngagementLog.action_type,
            func.count(EngagementLog.id),
        ).where(
            EngagementLog.strategy_id == strategy_id,
            EngagementLog.success == True,
            EngagementLog.created_at >= start,
            EngagementLog.created_at < end,
        ).group_by(EngagementLog.action_type)

        result = await self.db.execute(stmt)
        return {row[0]: row[1] for row in result.all()}
