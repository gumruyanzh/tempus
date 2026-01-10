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
    Circle1Member,
    ConversationReply,
    ConversationStatus,
    ConversationThread,
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
    daily_posts: int
    engagement_hours_start: int
    engagement_hours_end: int
    timezone: str
    name: str


class GrowthStrategyService:
    """Service for managing Twitter growth strategies."""

    # Algorithm-derived constants from Twitter research
    RATIO_THRESHOLDS = {
        "suppressed": 0.1,      # Below this = spam suppressed
        "reduced": 0.5,         # Below this = reduced distribution
        "neutral": 1.0,         # Neutral point
        "boosted": 1.5,         # Target minimum
        "significant": 2.0,     # Significant boost
        "authority": 10.0,      # Authority status
    }

    # Spam detection thresholds (per hour)
    SPAM_LIMITS = {
        "follows_per_hour": 40,     # >50 triggers suppression (we use 40 for safety)
        "unfollows_per_hour": 25,   # >30 triggers penalty
        "likes_per_hour": 80,       # Conservative limit
        "posts_per_hour": 15,       # Includes replies
    }

    # Account tier definitions
    ACCOUNT_TIERS = {
        "starter": (0, 1000),       # 0-1K followers
        "growing": (1000, 10000),   # 1K-10K followers
        "established": (10000, float("inf")),  # 10K+
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    # ========== Algorithm Optimization Methods ==========

    def calculate_safe_follow_limit(
        self,
        current_followers: int,
        current_following: int,
        target_ratio: float = 1.5,
    ) -> dict:
        """Calculate safe following limit to maintain healthy ratio.

        Based on algorithm research:
        - Ratio below 0.1 = suppressed as spam
        - Ratio 0.5-1.0 = neutral
        - Ratio 1.5+ = boosted distribution
        - Ratio 2.0+ = significant boost

        Args:
            current_followers: Current follower count
            current_following: Current following count
            target_ratio: Target follower/following ratio (default 1.5)

        Returns:
            Dict with ratio info and safe limits
        """
        current_ratio = current_followers / max(current_following, 1)

        # Calculate max following to maintain target ratio
        # followers / max_following = target_ratio
        # max_following = followers / target_ratio
        max_following = int(current_followers / target_ratio)
        safe_new_follows = max(0, max_following - current_following)

        # Determine current ratio status
        if current_ratio < self.RATIO_THRESHOLDS["suppressed"]:
            status = "critical"
            recommendation = "STOP following immediately. Focus on content to gain followers."
        elif current_ratio < self.RATIO_THRESHOLDS["reduced"]:
            status = "warning"
            recommendation = "Reduce follows significantly. Ratio is hurting distribution."
        elif current_ratio < self.RATIO_THRESHOLDS["neutral"]:
            status = "caution"
            recommendation = "Be conservative with follows. Ratio should improve."
        elif current_ratio < self.RATIO_THRESHOLDS["boosted"]:
            status = "good"
            recommendation = "Healthy ratio. Can follow moderately."
        else:
            status = "excellent"
            recommendation = "Strong ratio. Follow strategy unrestricted."

        return {
            "current_ratio": round(current_ratio, 2),
            "status": status,
            "recommendation": recommendation,
            "max_following": max_following,
            "safe_new_follows": safe_new_follows,
            "current_followers": current_followers,
            "current_following": current_following,
            "target_ratio": target_ratio,
        }

    def get_account_tier(self, follower_count: int) -> str:
        """Determine account tier based on follower count.

        Different tiers have different optimal activity levels:
        - starter (0-1K): Focus on quality replies, 15-20 replies/day
        - growing (1K-10K): Balance content and engagement
        - established (10K+): Focus on community, threads

        Args:
            follower_count: Current follower count

        Returns:
            Account tier name
        """
        for tier, (min_val, max_val) in self.ACCOUNT_TIERS.items():
            if min_val <= follower_count < max_val:
                return tier
        return "starter"

    def get_optimal_quotas_for_tier(
        self,
        tier: str,
        ratio_status: str = "good",
    ) -> dict:
        """Get optimal daily activity quotas based on account tier.

        Based on algorithm research:
        - 0-1K: 2 posts, 15-20 replies to larger accounts, 10-15 follows
        - 1K-10K: 2-3 posts, 10-15 replies, 5-10 follows
        - 10K+: 4-5 posts, 10+ fan engagements, minimal follows

        Args:
            tier: Account tier (starter, growing, established)
            ratio_status: Follower ratio status (affects follow limits)

        Returns:
            Dict of recommended daily quotas
        """
        # Base quotas by tier
        quotas = {
            "starter": {
                "daily_posts": 2,
                "daily_replies": 18,
                "daily_follows": 12,
                "daily_likes": 50,
                "daily_retweets": 5,
                "focus": "Quality replies to larger accounts in niche",
            },
            "growing": {
                "daily_posts": 3,
                "daily_replies": 12,
                "daily_follows": 8,
                "daily_likes": 75,
                "daily_retweets": 8,
                "focus": "Building Circle 1, authority content",
            },
            "established": {
                "daily_posts": 5,
                "daily_replies": 10,
                "daily_follows": 3,  # Minimal to protect ratio
                "daily_likes": 100,
                "daily_retweets": 10,
                "focus": "Community building, thread content",
            },
        }

        base = quotas.get(tier, quotas["starter"])

        # Adjust follows based on ratio status
        ratio_multipliers = {
            "critical": 0,        # No follows
            "warning": 0.25,      # Very limited
            "caution": 0.5,       # Half
            "good": 1.0,          # Normal
            "excellent": 1.5,     # Can be more aggressive
        }

        multiplier = ratio_multipliers.get(ratio_status, 1.0)
        base["daily_follows"] = int(base["daily_follows"] * multiplier)

        return base

    def should_use_conservative_mode(
        self,
        account_created_at: Optional[datetime],
        total_tweets: int = 0,
    ) -> dict:
        """Check if account should use conservative mode.

        New accounts (< 90 days) get +20% reach on first 50 tweets IF they:
        - Don't follow >100 users in first week
        - Keep engagement rate above 1%
        - Avoid spam signals

        Args:
            account_created_at: When the Twitter account was created
            total_tweets: Total tweets posted by account

        Returns:
            Dict with conservative mode recommendation
        """
        if not account_created_at:
            return {
                "conservative_mode": False,
                "reason": "Account age unknown",
                "recommendations": [],
            }

        account_age_days = (datetime.now(timezone.utc) - account_created_at).days

        if account_age_days <= 90:
            in_first_week = account_age_days <= 7
            under_50_tweets = total_tweets < 50

            recommendations = []
            if in_first_week:
                recommendations.append("Limit to <100 follows this week")
            if under_50_tweets:
                recommendations.append("Focus on high-quality tweets to maximize new account boost")

            recommendations.extend([
                "Keep engagement rate above 1%",
                "Avoid any spam-like behavior",
                "Post consistently at same times",
                "Stay in your niche (max 3 topics)",
            ])

            return {
                "conservative_mode": True,
                "account_age_days": account_age_days,
                "has_new_account_boost": under_50_tweets,
                "reason": f"Account is {account_age_days} days old (< 90 days)",
                "recommendations": recommendations,
                "max_daily_follows": 15 if in_first_week else 25,
            }

        return {
            "conservative_mode": False,
            "account_age_days": account_age_days,
            "reason": "Account is established (> 90 days)",
            "recommendations": [],
        }

    def calculate_engagement_distribution(
        self,
        total_replies: int,
        follower_ratio: float = 0.4,
    ) -> dict:
        """Calculate optimal engagement distribution between followers/non-followers.

        Algorithm research shows:
        - Out-of-network engagement weighted higher (signals broader appeal)
        - Optimal split: 40% to followers (nurture), 60% to non-followers (expansion)

        Args:
            total_replies: Total replies planned
            follower_ratio: Percentage to allocate to followers (default 0.4)

        Returns:
            Dict with engagement distribution targets
        """
        to_followers = int(total_replies * follower_ratio)
        to_non_followers = total_replies - to_followers

        return {
            "total_replies": total_replies,
            "to_followers": to_followers,
            "to_non_followers": to_non_followers,
            "follower_ratio": follower_ratio,
            "expansion_ratio": 1 - follower_ratio,
            "strategy": "40% nurture existing followers, 60% expand to new audiences",
        }

    async def apply_tier_based_quotas(
        self,
        strategy: GrowthStrategy,
        current_followers: int,
        current_following: int,
    ) -> dict:
        """Apply optimal quotas based on account tier and ratio status.

        Algorithm research shows different activity levels work best at different sizes:
        - Starter (0-1K): Focus on quality replies to larger accounts
        - Growing (1K-10K): Balance content and engagement
        - Established (10K+): Community building, thread content

        Args:
            strategy: The growth strategy to update
            current_followers: Current follower count
            current_following: Current following count

        Returns:
            Dict with recommended quotas and whether updates were applied
        """
        # Determine account tier
        tier = self.get_account_tier(current_followers)

        # Get ratio status
        ratio_info = self.calculate_safe_follow_limit(
            current_followers=current_followers,
            current_following=current_following,
        )

        # Get optimal quotas for this tier and ratio status
        optimal_quotas = self.get_optimal_quotas_for_tier(
            tier=tier,
            ratio_status=ratio_info["status"],
        )

        changes_made = []

        # Only apply quotas if they're more conservative than current settings
        # This prevents increasing beyond user's original intent
        if strategy.daily_follows > optimal_quotas["daily_follows"]:
            strategy.daily_follows = optimal_quotas["daily_follows"]
            changes_made.append(f"daily_follows: {optimal_quotas['daily_follows']}")

        if strategy.daily_replies > optimal_quotas["daily_replies"]:
            strategy.daily_replies = optimal_quotas["daily_replies"]
            changes_made.append(f"daily_replies: {optimal_quotas['daily_replies']}")

        if strategy.daily_posts > optimal_quotas["daily_posts"]:
            strategy.daily_posts = optimal_quotas["daily_posts"]
            changes_made.append(f"daily_posts: {optimal_quotas['daily_posts']}")

        if changes_made:
            await self.db.flush()
            logger.info(
                "Applied tier-based quotas",
                strategy_id=str(strategy.id),
                tier=tier,
                ratio_status=ratio_info["status"],
                changes=changes_made,
            )

        return {
            "tier": tier,
            "ratio_status": ratio_info["status"],
            "optimal_quotas": optimal_quotas,
            "changes_applied": changes_made,
            "focus": optimal_quotas.get("focus", ""),
        }

    def check_spam_limits(
        self,
        actions_this_hour: dict,
    ) -> dict:
        """Check if current activity is within spam-safe limits.

        Based on algorithm research:
        - >50 follows/hour triggers suppression
        - >30 unfollows/hour triggers penalty
        - Exact timing intervals flagged as bot behavior

        Args:
            actions_this_hour: Dict of action counts this hour

        Returns:
            Dict with limit status and warnings
        """
        warnings = []
        is_safe = True

        for action, limit in self.SPAM_LIMITS.items():
            count = actions_this_hour.get(action.replace("_per_hour", "s"), 0)
            if count >= limit:
                is_safe = False
                warnings.append(f"{action}: {count}/{limit} - LIMIT REACHED")
            elif count >= limit * 0.8:
                warnings.append(f"{action}: {count}/{limit} - approaching limit")

        return {
            "is_safe": is_safe,
            "warnings": warnings,
            "limits": self.SPAM_LIMITS,
            "current": actions_this_hour,
        }

    # ========== Circle 1 Nurturing ==========

    async def update_circle1_members(
        self,
        strategy: GrowthStrategy,
        twitter_service: "TwitterService",
        access_token: str,
        limit: int = 50,
    ) -> list[Circle1Member]:
        """Update Circle 1 members based on engagement patterns.

        Circle 1 = mutual follows + frequent engagement (highest trust)
        - Identifies top 50 mutual engagers
        - Tracks engagement sent/received
        - Ensures weekly touchpoints

        Args:
            strategy: The growth strategy
            twitter_service: Twitter service instance
            access_token: Valid access token
            limit: Maximum Circle 1 members to track (default 50)

        Returns:
            List of Circle1Member records
        """
        # Get our followers and following
        current_user = await twitter_service.get_current_user(access_token)
        our_user_id = current_user["data"]["id"]

        # Analyze engagement logs to find top engagers
        # Get users we've engaged with most
        stmt = select(
            EngagementLog.twitter_username,
            EngagementLog.twitter_user_id,
            func.count(EngagementLog.id).label("engagement_count"),
        ).where(
            EngagementLog.strategy_id == strategy.id,
            EngagementLog.success == True,
            EngagementLog.twitter_username.isnot(None),
        ).group_by(
            EngagementLog.twitter_username,
            EngagementLog.twitter_user_id,
        ).order_by(
            func.count(EngagementLog.id).desc()
        ).limit(100)

        result = await self.db.execute(stmt)
        engagement_data = result.all()

        updated_members = []

        for row in engagement_data:
            username = row.twitter_username
            user_id = row.twitter_user_id
            engagements_sent = row.engagement_count

            if not username:
                continue

            # Check if already in Circle 1
            existing_stmt = select(Circle1Member).where(
                Circle1Member.strategy_id == strategy.id,
                Circle1Member.twitter_username == username,
            )
            existing_result = await self.db.execute(existing_stmt)
            member = existing_result.scalar_one_or_none()

            if member:
                # Update existing member
                member.total_engagements_sent = engagements_sent
                member.last_engagement_at = datetime.now(timezone.utc)
            else:
                # Create new member
                member = Circle1Member(
                    strategy_id=strategy.id,
                    twitter_user_id=user_id or "",
                    twitter_username=username,
                    total_engagements_sent=engagements_sent,
                    last_engagement_at=datetime.now(timezone.utc),
                )
                self.db.add(member)

            # Calculate Circle 1 score
            member.calculate_circle1_score()
            updated_members.append(member)

            if len(updated_members) >= limit:
                break

        await self.db.flush()

        logger.info(
            "Circle 1 members updated",
            strategy_id=str(strategy.id),
            count=len(updated_members),
        )

        return updated_members

    async def get_circle1_members_needing_touchpoint(
        self,
        strategy_id: UUID,
        limit: int = 10,
    ) -> list[Circle1Member]:
        """Get Circle 1 members who need a touchpoint this week.

        Args:
            strategy_id: The strategy ID
            limit: Maximum members to return

        Returns:
            List of Circle1Member needing engagement
        """
        # Get members who haven't had a touchpoint in 5+ days
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)

        stmt = select(Circle1Member).where(
            Circle1Member.strategy_id == strategy_id,
            Circle1Member.is_active == True,
            Circle1Member.touchpoints_this_week == 0,
            (
                Circle1Member.last_touchpoint_at.is_(None) |
                (Circle1Member.last_touchpoint_at < five_days_ago)
            ),
        ).order_by(
            Circle1Member.circle1_score.desc()
        ).limit(limit)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def record_circle1_touchpoint(
        self,
        strategy_id: UUID,
        twitter_username: str,
    ) -> Optional[Circle1Member]:
        """Record a touchpoint with a Circle 1 member.

        Args:
            strategy_id: The strategy ID
            twitter_username: The Twitter username

        Returns:
            Updated Circle1Member or None
        """
        stmt = select(Circle1Member).where(
            Circle1Member.strategy_id == strategy_id,
            Circle1Member.twitter_username == twitter_username,
        )
        result = await self.db.execute(stmt)
        member = result.scalar_one_or_none()

        if member:
            member.record_touchpoint()
            await self.db.flush()
            logger.info(
                "Circle 1 touchpoint recorded",
                strategy_id=str(strategy_id),
                username=twitter_username,
            )

        return member

    async def reset_weekly_circle1_touchpoints(
        self,
        strategy_id: UUID,
    ) -> int:
        """Reset weekly touchpoint counters for all Circle 1 members.

        Should be called weekly (e.g., every Monday at midnight).

        Args:
            strategy_id: The strategy ID

        Returns:
            Number of members reset
        """
        stmt = select(Circle1Member).where(
            Circle1Member.strategy_id == strategy_id,
            Circle1Member.is_active == True,
        )
        result = await self.db.execute(stmt)
        members = result.scalars().all()

        for member in members:
            member.reset_weekly_touchpoints()

        await self.db.flush()

        logger.info(
            "Circle 1 weekly touchpoints reset",
            strategy_id=str(strategy_id),
            count=len(members),
        )

        return len(members)

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
- daily_posts: Recommended daily original posts/tweets (3-10, this is separate from replies)
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
                    daily_posts=min(data.get("daily_posts", 5), 20),
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
                    daily_posts=5,
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
            daily_posts=config.daily_posts,
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
  - Original posts: {strategy.daily_posts}

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

        Uses smart targeting optimized for Twitter's algorithm:
        - Sweet spot: 5-20 likes (high reply-back rate, less competition)
        - Reply ratio: 15-40% indicates discussion-friendly content
        - Recency: First 30 minutes is the golden engagement window
        - This captures better conversation potential for the 75x multiplier
        - Engagement distribution: 60% non-followers (expansion), 40% followers (nurture)

        Args:
            strategy: The growth strategy
            twitter_service: Twitter service instance
            access_token: Valid access token
            limit: Maximum targets to find

        Returns:
            List of created EngagementTargets
        """
        targets = []

        # Calculate optimal engagement distribution
        # 60% non-followers for expansion, 40% followers for nurturing
        distribution = self.calculate_engagement_distribution(limit, follower_ratio=0.4)
        follower_targets_limit = distribution["to_followers"]
        non_follower_targets_limit = distribution["to_non_followers"]

        logger.info(
            "Engagement distribution",
            strategy_id=str(strategy.id),
            followers=follower_targets_limit,
            non_followers=non_follower_targets_limit,
        )

        # First, try to get tweets from people who follow us (nurturing)
        follower_targets = []
        try:
            # Get our user ID
            current_user = await twitter_service.get_current_user(access_token)
            our_user_id = current_user["data"]["id"]

            # Get some of our followers' recent tweets (for nurturing)
            followers_response = await twitter_service.get_followers(
                access_token=access_token,
                user_id=our_user_id,
                max_results=30,
            )

            followers_data = followers_response.get("data", [])[:follower_targets_limit]

            for follower in followers_data:
                if len(follower_targets) >= follower_targets_limit:
                    break

                try:
                    # Get recent tweet from this follower
                    tweets = await twitter_service.get_user_tweets(
                        access_token=access_token,
                        user_id=follower.get("id"),
                        max_results=3,
                    )

                    if tweets and len(tweets) > 0:
                        tweet = tweets[0]
                        tweet_id = tweet.get("id")

                        # Check if already targeted
                        existing = await self._get_target_by_tweet_id(strategy.id, tweet_id)
                        if existing:
                            continue

                        metrics = tweet.get("metrics", {})
                        target = EngagementTarget(
                            strategy_id=strategy.id,
                            target_type=TargetType.TWEET,
                            tweet_id=tweet_id,
                            tweet_author=follower.get("username"),
                            tweet_author_id=follower.get("id"),
                            tweet_content=tweet.get("text", "")[:500],
                            tweet_like_count=metrics.get("like_count", 0),
                            tweet_retweet_count=metrics.get("retweet_count", 0),
                            should_like=True,
                            should_retweet=False,
                            should_reply=True,  # Engage more with followers
                            status=EngagementStatus.PENDING,
                            scheduled_for=self._get_next_engagement_slot(strategy),
                            relevance_score=0.85,  # High relevance for followers
                            priority=5,  # High priority
                        )
                        self.db.add(target)
                        follower_targets.append(target)
                        targets.append(target)

                except Exception as e:
                    logger.debug(f"Error getting tweets for follower: {e}")

        except Exception as e:
            logger.warning(f"Error getting follower tweets for nurturing: {e}")

        # Now search for non-follower tweets (expansion) - 60% of targets
        non_follower_targets = []
        for keyword in (strategy.niche_keywords or ["twitter growth"])[:3]:
            try:
                # Check if we've reached the non-follower limit
                if len(non_follower_targets) >= non_follower_targets_limit:
                    break

                # Get recent tweets (for recency factor)
                recent_tweets = await twitter_service.search_recent_tweets(
                    access_token=access_token,
                    query=keyword,
                    max_results=50,
                    sort_order="recency",  # Fresh tweets for early engagement
                )

                # Also get some popular tweets
                popular_tweets = await twitter_service.get_popular_tweets_about_topic(
                    access_token=access_token,
                    topic=keyword,
                    max_results=20,
                )

                # Combine and dedupe
                all_tweets = recent_tweets + popular_tweets
                seen_ids = set()
                unique_tweets = []
                for tweet in all_tweets:
                    if tweet.get("id") not in seen_ids:
                        seen_ids.add(tweet.get("id"))
                        unique_tweets.append(tweet)

                # Score and sort tweets by conversation potential
                scored_tweets = []
                for tweet in unique_tweets:
                    score = self._calculate_conversation_potential(tweet)
                    scored_tweets.append((score, tweet))

                # Sort by score descending (best opportunities first)
                scored_tweets.sort(key=lambda x: x[0], reverse=True)

                for score, tweet in scored_tweets:
                    # Check against non-follower limit (not total limit)
                    if len(non_follower_targets) >= non_follower_targets_limit:
                        break

                    tweet_id = tweet.get("id")
                    if not tweet_id:
                        continue

                    # Check if already a target
                    existing = await self._get_target_by_tweet_id(strategy.id, tweet_id)
                    if existing:
                        continue

                    # Get engagement metrics
                    metrics = tweet.get("metrics", {})
                    like_count = metrics.get("like_count", 0)
                    retweet_count = metrics.get("retweet_count", 0)
                    reply_count = metrics.get("reply_count", 0)

                    # Smart targeting decisions
                    # Like everything (low cost, high signal to algorithm)
                    should_like = True

                    # Retweet: Only RT content with good engagement but not saturated
                    # Sweet spot: 20-100 likes (good content, not too crowded)
                    should_retweet = 20 <= like_count <= 100 and retweet_count >= 5

                    # Reply: Prioritize the conversation potential sweet spot
                    # 5-50 likes = best reply-back rate (authors more likely to engage)
                    # Also require low reply ratio = less competition
                    reply_ratio = reply_count / max(like_count, 1)
                    should_reply = (
                        5 <= like_count <= 50 and  # Sweet spot for engagement
                        reply_ratio < 0.4 and  # Not too many replies (less competition)
                        score >= 0.5  # Only reply to high-potential conversations
                    )

                    target = EngagementTarget(
                        strategy_id=strategy.id,
                        target_type=TargetType.TWEET,
                        tweet_id=tweet_id,
                        tweet_author=tweet.get("author_username"),
                        tweet_author_id=tweet.get("author_id"),
                        tweet_content=tweet.get("text", "")[:500],
                        tweet_like_count=like_count,
                        tweet_retweet_count=retweet_count,
                        should_like=should_like,
                        should_retweet=should_retweet,
                        should_reply=should_reply,
                        status=EngagementStatus.PENDING,
                        scheduled_for=self._get_next_engagement_slot(strategy),
                        relevance_score=score,  # Use conversation potential as relevance
                        priority=int(score * 100),  # Higher score = higher priority
                    )
                    self.db.add(target)
                    non_follower_targets.append(target)
                    targets.append(target)

            except Exception as e:
                logger.warning(
                    "Error finding engagement tweets",
                    keyword=keyword,
                    error=str(e),
                )

        await self.db.flush()

        logger.info(
            "Found engagement tweets with smart targeting and distribution",
            strategy_id=str(strategy.id),
            total=len(targets),
            follower_targets=len(follower_targets),
            non_follower_targets=len(non_follower_targets),
            distribution_actual=f"{len(follower_targets)}/{len(non_follower_targets)}",
            distribution_target=f"{follower_targets_limit}/{non_follower_targets_limit}",
            reply_targets=sum(1 for t in targets if t.should_reply),
        )

        return targets

    def _calculate_conversation_potential(self, tweet: dict) -> float:
        """Calculate conversation potential score for a tweet.

        This algorithm optimizes for the 75x reply-to-reply multiplier by
        identifying tweets where the author is most likely to engage back.

        Score factors (0-1 scale):
        - Optimal engagement size (30%): 5-20 likes is sweet spot
        - Reply ratio (25%): 15-40% indicates discussion-friendly
        - Recency (25%): First 30 minutes is golden window
        - Author engagement potential (20%): Mid-tier followers more responsive

        Args:
            tweet: Tweet data dict with metrics

        Returns:
            Score between 0.0 and 1.0
        """
        score = 0.0
        metrics = tweet.get("metrics", {})
        like_count = metrics.get("like_count", 0)
        reply_count = metrics.get("reply_count", 0)
        retweet_count = metrics.get("retweet_count", 0)

        # 1. Optimal engagement size factor (30%)
        # Sweet spot: 5-20 likes - enough signal it's good content,
        # but not so popular that author is overwhelmed
        if 5 <= like_count <= 20:
            size_factor = 1.0  # Perfect range
        elif 3 <= like_count < 5:
            size_factor = 0.8  # Good but might be too early
        elif 20 < like_count <= 50:
            size_factor = 0.7  # Still decent, more competition
        elif 50 < like_count <= 100:
            size_factor = 0.5  # Getting crowded
        elif like_count > 100:
            size_factor = 0.3  # Too popular, low reply-back rate
        else:
            size_factor = 0.4  # Very new tweet (< 3 likes)
        score += size_factor * 0.30

        # 2. Reply ratio factor (25%)
        # 15-40% reply ratio indicates discussion-friendly content
        reply_ratio = reply_count / max(like_count, 1)
        if 0.15 <= reply_ratio <= 0.40:
            ratio_factor = 1.0  # Optimal discussion ratio
        elif 0.05 <= reply_ratio < 0.15:
            ratio_factor = 0.7  # Low replies = might not engage
        elif 0.40 < reply_ratio <= 0.60:
            ratio_factor = 0.6  # High competition
        elif reply_ratio > 0.60:
            ratio_factor = 0.3  # Very crowded
        else:
            ratio_factor = 0.5  # No replies yet
        score += ratio_factor * 0.25

        # 3. Recency factor (25%)
        # Try to parse created_at - tweets in first 30 min are golden
        recency_factor = 0.5  # Default if we can't parse
        created_at = tweet.get("created_at")
        if created_at:
            try:
                tweet_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_minutes = (datetime.now(timezone.utc) - tweet_time).total_seconds() / 60

                if age_minutes <= 30:
                    recency_factor = 1.0  # Golden window
                elif age_minutes <= 60:
                    recency_factor = 0.8  # Still fresh
                elif age_minutes <= 120:
                    recency_factor = 0.6  # Decent
                elif age_minutes <= 360:  # 6 hours
                    recency_factor = 0.4
                else:
                    recency_factor = 0.2  # Old tweet
            except Exception:
                pass
        score += recency_factor * 0.25

        # 4. Author engagement potential (20%)
        # We don't always have follower counts, so use what we have
        author_verified = tweet.get("author_verified", False)
        if author_verified:
            # Verified accounts less likely to reply to randoms
            author_factor = 0.3
        else:
            # Non-verified more likely to engage
            author_factor = 0.7
        score += author_factor * 0.20

        return min(score, 1.0)

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
        from app.models.user import User

        deepseek = DeepSeekService(api_key)

        try:
            # Fetch user's default prompt template
            user_stmt = select(User).where(User.id == strategy.user_id)
            user_result = await self.db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            user_default_prompt = user.default_prompt_template if user else None

            # Build system prompt - prioritize user's default prompt if set
            if user_default_prompt:
                # Use user's default prompt as the base
                base_system = user_default_prompt

                # Add strategy-specific instructions if available
                if strategy.custom_prompt:
                    system_prompt = f"""{base_system}

ADDITIONAL STRATEGY-SPECIFIC INSTRUCTIONS:
{strategy.custom_prompt}"""
                else:
                    system_prompt = base_system
            elif strategy.custom_prompt:
                # No user default, but has strategy custom prompt
                base_system = """You are a thoughtful Twitter user engaging authentically with content in your niche.

Write a reply that:
1. Adds value to the conversation
2. Shows genuine interest
3. Doesn't sound like a bot or generic comment
4. Is under 280 characters
5. Avoids generic phrases like "Great post!" or "Thanks for sharing!"
6. Either asks a thoughtful question, shares a relevant insight, or adds a personal perspective

Be conversational and human."""

                system_prompt = f"""{base_system}

IMPORTANT - Follow these specific instructions from the user:
{strategy.custom_prompt}"""
            else:
                # No custom prompts at all, use default
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

    async def generate_conversation_reply(
        self,
        thread: "ConversationThread",
        strategy: GrowthStrategy,
        conversation_context: str,
        api_key: str,
    ) -> str:
        """Generate AI reply content for a conversation continuation.

        This method generates contextually appropriate replies for ongoing
        conversations, maintaining coherence across multiple turns to capture
        the 75x algorithmic multiplier from reply-to-reply interactions.

        Args:
            thread: The conversation thread
            strategy: The growth strategy
            conversation_context: Formatted string of the full conversation
            api_key: DeepSeek API key

        Returns:
            Generated reply text
        """
        from app.models.user import User
        from app.models.growth_strategy import ConversationThread

        deepseek = DeepSeekService(api_key)

        try:
            # Fetch user's default prompt template
            user_stmt = select(User).where(User.id == strategy.user_id)
            user_result = await self.db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            user_default_prompt = user.default_prompt_template if user else None

            # Build system prompt for conversation continuation
            base_system = """You are a thoughtful Twitter user continuing an engaging conversation.

CRITICAL REQUIREMENTS:
1. Maintain conversation coherence - reference what was said before
2. Be natural and conversational - this is a real dialogue, not a one-off reply
3. Add value - share insights, ask thoughtful follow-up questions, or build on their points
4. Keep it under 280 characters
5. Sound human, not robotic or generic
6. Know when to naturally wrap up - if the conversation has reached a natural conclusion, write something that ends it gracefully
7. NEVER repeat what you've already said in the conversation

CONVERSATION DYNAMICS:
- If they asked a question, answer it directly then add your own thought
- If they shared an opinion, engage with it genuinely
- If they shared information, acknowledge it and add perspective
- If they're being friendly, be warm back
- If the conversation is winding down, don't force it to continue"""

            # Add user/strategy customizations
            if user_default_prompt:
                system_prompt = f"""{base_system}

USER'S VOICE & STYLE:
{user_default_prompt}"""
            elif strategy.custom_prompt:
                system_prompt = f"""{base_system}

VOICE & STYLE GUIDELINES:
{strategy.custom_prompt}"""
            else:
                system_prompt = base_system

            # Build the user prompt with full conversation context
            user_prompt = f"""Continue this Twitter conversation naturally.

FULL CONVERSATION:
{conversation_context}

CONTEXT:
- Niche/Topics: {', '.join(strategy.niche_keywords or ['general topics'])}
- Conversation depth: {thread.depth} turns so far
- Max depth: {thread.max_depth} (we want meaningful engagement, not spam)

Write your next reply. Output ONLY the reply text, nothing else. Keep it under 280 characters."""

            response = await deepseek._call_api(system_prompt, user_prompt)
            reply = response.strip()

            # Clean up
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1]

            # Ensure under character limit
            if len(reply) > strategy.tweet_char_limit:
                reply = reply[:strategy.tweet_char_limit - 3] + "..."

            logger.info(
                "Conversation reply generated",
                thread_id=str(thread.id),
                depth=thread.depth,
                reply_length=len(reply),
            )

            return reply

        finally:
            await deepseek.close()

    async def _fetch_trending_topics(
        self,
        strategy: GrowthStrategy,
    ) -> list[dict]:
        """Fetch viral cannabis tweets from Twitter to remix.

        Searches Twitter for popular cannabis/weed tweets with good engagement,
        tracks which tweets have been used, and returns one unused tweet to remix.
        Caches tweets for 2 hours to avoid rate limits.
        """
        import random
        from datetime import timedelta

        try:
            # Get cache data
            cache_data = strategy.trending_topics_cache or {}
            used_tweet_ids = set(cache_data.get('used_tweet_ids', []))
            cached_tweets = cache_data.get('cached_tweets', [])

            # Check if we have valid cached tweets (under 2 hours old)
            if cached_tweets and strategy.trending_topics_updated_at:
                cache_age = datetime.now(timezone.utc) - strategy.trending_topics_updated_at
                if cache_age < timedelta(hours=2):
                    # Use cached tweets - pick an unused one
                    unused_tweets = [t for t in cached_tweets if t.get('id') not in used_tweet_ids]
                    if unused_tweets:
                        selected = random.choice(unused_tweets)
                        used_tweet_ids.add(selected['id'])
                        strategy.trending_topics_cache = {
                            'used_tweet_ids': list(used_tweet_ids)[-100],
                            'cached_tweets': cached_tweets,
                            'last_tweet': selected,
                        }
                        await self.db.commit()
                        logger.info(
                            "Using cached viral tweet",
                            strategy_id=str(strategy.id),
                            tweet_id=selected['id'],
                            cache_age_min=int(cache_age.total_seconds() / 60),
                            unused_tweets_left=len(unused_tweets) - 1,
                            total_cached=len(cached_tweets),
                        )
                        return [selected]

            # Get Twitter access token for fresh fetch
            twitter_service = TwitterService(self.db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)

            if not access_token:
                logger.warning("No Twitter access token for viral tweets")
                return []

            # Get cache data (includes used_tweet_ids)
            used_tweet_ids = set(cache_data.get('used_tweet_ids', []))

            # Search queries for cannabis content - rotate through them
            search_queries = [
                "weed",
                "cannabis",
                "stoner",
                "420",
                "high",
                "smoke weed",
                "dispensary",
                "thc",
            ]

            # Pick a random query to get variety
            query = random.choice(search_queries)

            try:
                # Search for popular tweets
                tweets = await twitter_service.search_recent_tweets(
                    access_token=access_token,
                    query=query,
                    max_results=50,
                    sort_order="relevancy",
                )

                if not tweets:
                    logger.warning("No tweets found for viral remix", query=query)
                    return []

                # Filter for engagement and exclude already used tweets
                good_tweets = []
                for tweet in tweets:
                    tweet_id = tweet.get('id')
                    metrics = tweet.get('public_metrics', {})
                    likes = metrics.get('like_count', 0)
                    retweets = metrics.get('retweet_count', 0)

                    # Skip if already used
                    if tweet_id in used_tweet_ids:
                        continue

                    # Only include tweets with some engagement
                    if likes >= 5 or retweets >= 2:
                        good_tweets.append({
                            'id': tweet_id,
                            'text': tweet.get('text', ''),
                            'likes': likes,
                            'retweets': retweets,
                            'author': tweet.get('author', {}).get('username', 'unknown'),
                        })

                if not good_tweets:
                    # Clear used tweets if we've exhausted them
                    logger.info("Clearing used tweets cache - all tweets exhausted")
                    used_tweet_ids = set()
                    # Re-filter without exclusion
                    for tweet in tweets:
                        metrics = tweet.get('public_metrics', {})
                        likes = metrics.get('like_count', 0)
                        retweets = metrics.get('retweet_count', 0)
                        if likes >= 5 or retweets >= 2:
                            good_tweets.append({
                                'id': tweet.get('id'),
                                'text': tweet.get('text', ''),
                                'likes': likes,
                                'retweets': retweets,
                                'author': tweet.get('author', {}).get('username', 'unknown'),
                            })

                if not good_tweets:
                    logger.warning("No engaging tweets found", query=query)
                    return []

                # Sort by engagement and pick a random one from top tweets
                good_tweets.sort(key=lambda x: x['likes'] + x['retweets'] * 2, reverse=True)
                selected_tweet = random.choice(good_tweets[:10])

                # Mark as used
                used_tweet_ids.add(selected_tweet['id'])

                # Update cache - store ALL good tweets for 2-hour caching
                strategy.trending_topics_cache = {
                    'used_tweet_ids': list(used_tweet_ids)[-100],  # Keep last 100
                    'cached_tweets': good_tweets,  # Cache all fetched tweets for reuse
                    'last_tweet': selected_tweet,
                }
                strategy.trending_topics_updated_at = datetime.now(timezone.utc)
                await self.db.commit()

                logger.info(
                    "Found viral tweet to remix (fresh fetch)",
                    strategy_id=str(strategy.id),
                    tweet_id=selected_tweet['id'],
                    likes=selected_tweet['likes'],
                    author=selected_tweet['author'],
                    tweets_cached=len(good_tweets),
                    query_used=query,
                )

                return [selected_tweet]

            finally:
                await twitter_service.close()

        except Exception as e:
            logger.error(
                "Error fetching viral tweets",
                strategy_id=str(strategy.id),
                error=str(e),
            )
            return []

    async def generate_post_content(
        self,
        strategy: GrowthStrategy,
        api_key: str,
        include_image: bool = True,
    ) -> dict:
        """Generate original post content for the strategy with optional image.

        Args:
            strategy: The growth strategy
            api_key: DeepSeek API key
            include_image: Whether to generate an accompanying image

        Returns:
            Dict with 'text' (str), 'image_bytes' (Optional[bytes]), 'image_alt_text' (Optional[str])
        """
        import httpx
        from datetime import timedelta
        from app.models.user import User
        from app.models.growth_strategy import EngagementLog

        deepseek = DeepSeekService(api_key)

        try:
            # Fetch user's default prompt template
            user_stmt = select(User).where(User.id == strategy.user_id)
            user_result = await self.db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            user_default_prompt = user.default_prompt_template if user else None

            # Check if trending topics mode is enabled
            trending_topics_data = None
            if getattr(strategy, 'use_trending_topics', False):
                trending_topics_data = await self._fetch_trending_topics(strategy)

            # Check if this is a cannabis/cannapedia account - fetch real strain data
            # Skip strain data if trending topics mode is enabled and has data
            strain_data = None
            niche_keywords = strategy.niche_keywords or []
            is_cannabis_niche = any(kw.lower() in ['cannabis', 'marijuana', 'weed', 'cbd', 'hemp', '420']
                                   for kw in niche_keywords)

            if is_cannabis_niche and not trending_topics_data:
                # Get recent posts to avoid duplicates (last 24 hours)
                recent_posts_stmt = select(EngagementLog).where(
                    EngagementLog.strategy_id == strategy.id,
                    EngagementLog.action_type == ActionType.POST,
                    EngagementLog.success == True,
                    EngagementLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
                ).order_by(EngagementLog.created_at.desc())

                recent_posts_result = await self.db.execute(recent_posts_stmt)
                recent_posts = list(recent_posts_result.scalars().all())

                # Extract strain names from recent posts
                recent_strain_names = set()
                for post in recent_posts:
                    if post.reply_content:
                        # Extract first line which usually contains strain name
                        first_line = post.reply_content.split('\n')[0].lower()
                        recent_strain_names.add(first_line)

                logger.info(
                    "Checking for duplicate strains",
                    recent_count=len(recent_posts),
                    recent_strains=list(recent_strain_names)[:5],
                )

                # Try to fetch a unique strain (max 5 attempts)
                max_attempts = 5
                for attempt in range(max_attempts):
                    try:
                        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                            # Use /api/strains/random/ endpoint for truly random strain
                            random_response = await client.get("https://cannapedia.ai/api/strains/random/")

                            if random_response.status_code == 200:
                                strain_data = random_response.json()
                                strain_name = strain_data.get("name", "").lower()

                                # Check if this strain was recently posted
                                is_duplicate = False
                                for recent_strain in recent_strain_names:
                                    if strain_name in recent_strain or recent_strain in strain_name:
                                        is_duplicate = True
                                        break

                                if is_duplicate:
                                    logger.warning(
                                        f"Strain '{strain_data.get('name')}' was recently posted, trying another (attempt {attempt + 1}/{max_attempts})"
                                    )
                                    strain_data = None
                                    continue

                                logger.info(
                                    "Fetched random strain from cannapedia.ai",
                                    strain=strain_data.get("name"),
                                    attempt=attempt + 1,
                                )
                                break
                            else:
                                logger.warning(f"Random strain API returned {random_response.status_code}")

                    except Exception as e:
                        logger.warning(f"Failed to fetch strain data (attempt {attempt + 1}): {e}")

                if not strain_data:
                    logger.error("Could not fetch unique strain after all attempts")
                    return None

            # Build system prompt
            if user_default_prompt:
                base_system = user_default_prompt
                if strategy.custom_prompt:
                    system_prompt = f"""{base_system}

ADDITIONAL STRATEGY-SPECIFIC INSTRUCTIONS:
{strategy.custom_prompt}"""
                else:
                    system_prompt = base_system
            elif strategy.custom_prompt:
                system_prompt = f"""You are a Twitter content creator. Create engaging, authentic posts.

IMPORTANT - Follow these specific instructions from the user:
{strategy.custom_prompt}"""
            else:
                system_prompt = """You are a Twitter content creator focused on building an engaged audience.

Create posts that:
1. Are authentic and conversational
2. Provide value (insights, tips, perspectives)
3. Spark engagement and conversation
4. Fit naturally in the niche
5. Are under 280 characters unless the account has verification"""

            niche = ', '.join(niche_keywords or ['general topics'])

            # If we have strain data, include it in the prompt
            if strain_data:
                # Extract effects with intensities
                effects_list = strain_data.get('effects', [])
                positive_effects = [f"{e['effect']['name']} ({e['intensity']}%)"
                                   for e in effects_list
                                   if e.get('effect', {}).get('category') == 'positive'][:4]
                effects_str = ', '.join(positive_effects) if positive_effects else 'Unknown'

                # Extract flavors
                flavors_list = strain_data.get('flavors', [])
                flavors_str = ', '.join([f['name'] for f in flavors_list][:4]) if flavors_list else 'Unknown'

                # Extract terpenes
                terpenes_list = strain_data.get('terpenes', [])
                terpenes_str = ', '.join([t.get('terpene', {}).get('name', t.get('name', '')) for t in terpenes_list][:3]) if terpenes_list else ''

                # Extract parent strains
                parents_list = strain_data.get('parent_strains', [])
                parents_str = ' x '.join([p.get('name', '') for p in parents_list]) if parents_list else 'Unknown lineage'

                # Store strain name for logging/verification
                fetched_strain_name = strain_data.get('name', 'Unknown')

                user_prompt = f"""Create an original tweet about the cannabis strain "{fetched_strain_name}".

===== REAL STRAIN DATA (USE ONLY THIS DATA) =====
Strain Name: {fetched_strain_name}
Type: {strain_data.get('category', 'Unknown').title()}
THC Level: {strain_data.get('thc_display', 'Unknown')}
CBD Level: {strain_data.get('cbd_display', 'Unknown') or 'Low'}
Top Effects: {effects_str}
Flavors/Aromas: {flavors_str}
Genetics/Parents: {parents_str}
{f"Terpenes: {terpenes_str}" if terpenes_str else ""}
=================================================

CRITICAL RULES:
1. The tweet MUST be about "{fetched_strain_name}" - this is the strain you are posting about
2. Use ONLY the real data provided above - do NOT use any example strains from your instructions
3. Do NOT copy or modify any example tweets from your instructions
4. Do NOT include any URLs or links
5. Start the tweet with the strain name "{fetched_strain_name}"

Character limit: {strategy.tweet_char_limit}

Output ONLY the tweet text for "{fetched_strain_name}", nothing else. No quotes."""

                logger.info(
                    "Generating post for strain",
                    strain_name=fetched_strain_name,
                    thc=strain_data.get('thc_display'),
                    effects=effects_str[:50],
                )
            elif trending_topics_data:
                # Remix a viral tweet found on Twitter
                source_tweet = trending_topics_data[0]
                source_text = source_tweet.get('text', '')
                source_author = source_tweet.get('author', 'unknown')
                source_likes = source_tweet.get('likes', 0)

                user_prompt = f"""Remix this viral cannabis tweet into your own unique post. Take the core idea/vibe and make it YOUR OWN with fresh wording.

===== VIRAL TWEET TO REMIX =====
Original: "{source_text}"
(by @{source_author} - {source_likes} likes)
================================

RULES FOR REMIXING:
1. Capture the VIBE and IDEA, but use completely different words
2. Add your own personality and voice - be WeedVader
3. DO NOT copy phrases - rewrite it fresh
4. Keep it under {strategy.tweet_char_limit} characters
5. Make it sound like YOUR original thought
6. Add Star Wars references if they fit naturally
7. No URLs or links, no @mentions
8. 1-2 hashtags max if it fits the vibe

IMPORTANT: The output should feel like an original post, NOT a copy or quote. Just capture the energy.

Output ONLY your remixed tweet, nothing else."""

                logger.info(
                    "Generating remixed tweet",
                    strategy_id=str(strategy.id),
                    source_tweet_id=source_tweet.get('id'),
                    source_likes=source_likes,
                )
            else:
                user_prompt = f"""Create an original tweet for a {niche} account.

Character limit: {strategy.tweet_char_limit}

IMPORTANT: Do NOT include any URLs or links in the tweet.

Output ONLY the tweet text, nothing else. No quotes around it."""

            response = await deepseek._call_api(system_prompt, user_prompt)
            post = response.strip()

            # Clean up
            if post.startswith('"') and post.endswith('"'):
                post = post[1:-1]

            # Ensure under character limit
            if len(post) > strategy.tweet_char_limit:
                post = post[:strategy.tweet_char_limit - 3] + "..."

            # Validate that post contains the correct strain name (for cannabis posts)
            if strain_data:
                fetched_name = strain_data.get('name', '').lower()
                post_lower = post.lower()

                # Check if the strain name appears in the post
                if fetched_name not in post_lower:
                    logger.error(
                        "Generated post does not contain the fetched strain name!",
                        expected_strain=fetched_name,
                        post_preview=post[:100],
                    )
                    # Return None to indicate failure - don't post wrong content
                    return None

                logger.info(
                    "Post generated and validated",
                    strategy_id=str(strategy.id),
                    strain_name=fetched_name,
                    post_length=len(post),
                )
            else:
                logger.info(
                    "Post generated",
                    strategy_id=str(strategy.id),
                    post_length=len(post),
                )

            # Generate image if requested
            image_bytes = None
            image_alt_text = None

            if include_image:
                try:
                    from app.services.stability import StabilityAIService, StabilityAIError
                    from app.core.config import settings

                    if settings.stability_api_key:
                        stability = StabilityAIService()
                        try:
                            if is_cannabis_niche and strain_data:
                                # Generate cannabis-specific image
                                strain_name = strain_data.get('name', 'cannabis')
                                strain_type = strain_data.get('category', 'hybrid').title()

                                # Build a descriptive prompt for the strain
                                image_prompt = (
                                    f"Professional cannabis photography of {strain_name} strain, "
                                    f"beautiful {strain_type.lower()} marijuana buds, "
                                    "macro close-up showing trichomes and crystals, "
                                    "vibrant colors, studio lighting, botanical style, "
                                    "high detail, sharp focus"
                                )

                                negative_prompt = (
                                    "text, watermark, logo, low quality, blurry, "
                                    "distorted, deformed, human, hands, smoking"
                                )

                                logger.info(
                                    "Generating image for strain post",
                                    strain=strain_name,
                                )

                                image_bytes = await stability.generate_image(
                                    prompt=image_prompt,
                                    negative_prompt=negative_prompt,
                                    width=1024,
                                    height=576,  # 16:9 for Twitter
                                    style_preset="photographic",
                                )

                                # Optimize for Twitter
                                image_bytes = stability.optimize_image_for_twitter(image_bytes)

                                image_alt_text = f"Cannabis strain {strain_name} - {strain_type}"

                                logger.info(
                                    "Image generated successfully",
                                    strain=strain_name,
                                    image_size=len(image_bytes),
                                )
                            else:
                                # Generate generic niche image
                                niche = ', '.join(niche_keywords[:2]) if niche_keywords else 'lifestyle'
                                image_bytes = await stability.generate_for_tweet(
                                    topic=post[:100],
                                    niche=niche,
                                    style="photographic",
                                )
                                image_bytes = stability.optimize_image_for_twitter(image_bytes)
                                image_alt_text = f"Image for {niche} content"

                        finally:
                            await stability.close()
                    else:
                        logger.debug("Stability API key not configured, skipping image generation")

                except StabilityAIError as e:
                    # Log but don't fail - post without image
                    logger.warning(
                        "Image generation failed, posting without image",
                        error=str(e),
                    )
                except Exception as e:
                    logger.warning(
                        "Unexpected error in image generation",
                        error=str(e),
                    )

            return {
                "text": post,
                "image_bytes": image_bytes,
                "image_alt_text": image_alt_text,
            }

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
            posts_done=actions.get(ActionType.POST, 0),
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
            "posts": strategy.total_posts,
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

        # Get conversation stats
        conversation_stats = await self.get_conversation_stats(strategy_id)

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
            "conversations": conversation_stats,
        }

    async def get_conversation_stats(
        self,
        strategy_id: UUID,
    ) -> dict[str, Any]:
        """Get conversation thread statistics for a strategy."""
        from sqlalchemy import func

        # Count threads by status
        status_counts = {}
        for status in ConversationStatus:
            stmt = select(func.count(ConversationThread.id)).where(
                ConversationThread.strategy_id == strategy_id,
                ConversationThread.status == status,
            )
            result = await self.db.execute(stmt)
            status_counts[status.value] = result.scalar() or 0

        total_threads = sum(status_counts.values())

        # Count total replies received (not from us)
        stmt = select(func.count(ConversationReply.id)).where(
            ConversationReply.thread_id.in_(
                select(ConversationThread.id).where(
                    ConversationThread.strategy_id == strategy_id
                )
            ),
            ConversationReply.is_from_us == False,
        )
        result = await self.db.execute(stmt)
        replies_received = result.scalar() or 0

        # Count our responses (replies from us, excluding initial)
        stmt = select(func.count(ConversationReply.id)).where(
            ConversationReply.thread_id.in_(
                select(ConversationThread.id).where(
                    ConversationThread.strategy_id == strategy_id
                )
            ),
            ConversationReply.is_from_us == True,
        )
        result = await self.db.execute(stmt)
        our_responses = result.scalar() or 0

        # Get threads that led to follows
        stmt = select(func.count(ConversationThread.id)).where(
            ConversationThread.strategy_id == strategy_id,
            ConversationThread.led_to_follow == True,
        )
        result = await self.db.execute(stmt)
        led_to_follows = result.scalar() or 0

        # Get average conversation depth
        stmt = select(func.avg(ConversationThread.depth)).where(
            ConversationThread.strategy_id == strategy_id,
            ConversationThread.depth > 0,
        )
        result = await self.db.execute(stmt)
        avg_depth = result.scalar() or 0

        return {
            "total_threads": total_threads,
            "active_threads": status_counts.get("active", 0),
            "completed_threads": status_counts.get("completed", 0),
            "paused_threads": status_counts.get("paused", 0),
            "replies_received": replies_received,
            "our_responses": our_responses,
            "led_to_follows": led_to_follows,
            "avg_depth": round(float(avg_depth), 1) if avg_depth else 0,
            "reply_rate": round(replies_received / total_threads * 100, 1) if total_threads > 0 else 0,
        }

    async def get_conversation_threads(
        self,
        strategy_id: UUID,
        status: Optional[ConversationStatus] = None,
        limit: int = 50,
    ) -> list[ConversationThread]:
        """Get conversation threads for a strategy."""
        stmt = select(ConversationThread).where(
            ConversationThread.strategy_id == strategy_id,
        )

        if status:
            stmt = stmt.where(ConversationThread.status == status)

        stmt = stmt.order_by(ConversationThread.created_at.desc()).limit(limit)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

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
                elif action_type == ActionType.POST:
                    strategy.increment_posts()

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
