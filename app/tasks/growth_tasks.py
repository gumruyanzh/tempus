"""Celery tasks for growth strategy processing."""

import asyncio
import random
from datetime import datetime, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.core.database import get_celery_db_context
from app.core.logging import get_logger
from app.models.growth_strategy import (
    ActionType,
    EngagementStatus,
    EngagementTarget,
    GrowthStrategy,
    StrategyStatus,
    TargetType,
)
from app.models.user import APIKeyType
from app.services.growth_strategy import GrowthStrategyService
from app.services.rate_limiter import EngagementRateLimiter, RateLimitError
from app.services.twitter import TwitterAPIError, TwitterRateLimitError, TwitterService
from app.services.user import UserService

logger = get_logger(__name__)


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task
def process_growth_strategies() -> dict:
    """Main task - process all active growth strategies.

    Runs every 5 minutes to:
    1. Find active strategies
    2. Discover new engagement targets if needed
    3. Queue pending engagements for execution
    """
    return run_async(_process_growth_strategies_async())


async def _process_growth_strategies_async() -> dict:
    """Async implementation of growth strategy processing."""
    async with get_celery_db_context() as db:
        growth_service = GrowthStrategyService(db)
        active_strategies = await growth_service.get_active_strategies()

        if not active_strategies:
            logger.debug("No active growth strategies")
            return {"processed": 0}

        processed = 0
        for strategy in active_strategies:
            try:
                # Check if strategy has completed
                if strategy.is_complete:
                    strategy.mark_completed()
                    await db.commit()
                    logger.info(
                        "Growth strategy completed",
                        strategy_id=str(strategy.id),
                    )
                    continue

                # Queue engagement execution task
                execute_strategy_engagements.delay(str(strategy.id))
                processed += 1

            except Exception as e:
                logger.error(
                    "Error processing growth strategy",
                    strategy_id=str(strategy.id),
                    error=str(e),
                )

        logger.info("Queued growth strategies for processing", count=processed)
        return {"processed": processed}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def execute_strategy_engagements(self, strategy_id: str) -> dict:
    """Execute pending engagements for a strategy."""
    return run_async(_execute_strategy_engagements_async(self, strategy_id))


async def _execute_strategy_engagements_async(task, strategy_id: str) -> dict:
    """Async implementation of engagement execution."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            user_service = UserService(db)

            strategy = await growth_service.get_strategy(UUID(strategy_id))
            if not strategy:
                return {"success": False, "error": "Strategy not found"}

            if strategy.status != StrategyStatus.ACTIVE:
                return {"success": False, "error": "Strategy not active"}

            # Get Twitter access token
            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)

            if not access_token:
                logger.error("No valid Twitter access token", user_id=str(strategy.user_id))
                return {"success": False, "error": "No Twitter access token"}

            # Check rate limits
            rate_limiter = EngagementRateLimiter(db)
            if await rate_limiter.should_pause(strategy.user_id):
                logger.info(
                    "Pausing engagements due to high rate limit usage",
                    strategy_id=strategy_id,
                )
                return {"success": True, "paused": True}

            # Get pending targets
            pending_targets = await growth_service.get_pending_targets(
                strategy.id, limit=10
            )

            if not pending_targets:
                # Try to discover more targets
                discover_engagement_targets.delay(strategy_id)
                return {"success": True, "executed": 0, "message": "No pending targets"}

            executed = 0
            for target in pending_targets:
                try:
                    # Execute engagement with rate limiting
                    success = await _execute_single_engagement(
                        db=db,
                        target=target,
                        strategy=strategy,
                        twitter_service=twitter_service,
                        access_token=access_token,
                        rate_limiter=rate_limiter,
                        growth_service=growth_service,
                    )

                    if success:
                        executed += 1

                    # Add random delay between engagements (60-180 seconds)
                    delay = random.randint(60, 180)
                    await asyncio.sleep(min(delay, 5))  # Cap at 5s for task

                except RateLimitError as e:
                    logger.warning(
                        "Rate limit reached, stopping engagements",
                        strategy_id=strategy_id,
                        action=e.action,
                    )
                    break

                except Exception as e:
                    logger.error(
                        "Error executing engagement",
                        target_id=str(target.id),
                        error=str(e),
                    )
                    target.mark_failed(str(e)[:200])

            await db.commit()

            logger.info(
                "Strategy engagements executed",
                strategy_id=strategy_id,
                executed=executed,
            )

            return {"success": True, "executed": executed}

        except Exception as e:
            logger.exception(
                "Unexpected error executing strategy engagements",
                strategy_id=strategy_id,
            )
            await db.rollback()
            raise

        finally:
            if twitter_service:
                await twitter_service.close()


async def _execute_single_engagement(
    db,
    target: EngagementTarget,
    strategy: GrowthStrategy,
    twitter_service: TwitterService,
    access_token: str,
    rate_limiter: EngagementRateLimiter,
    growth_service: GrowthStrategyService,
) -> bool:
    """Execute a single engagement action."""

    if target.target_type == TargetType.ACCOUNT:
        # Follow action
        if target.should_follow:
            # Check rate limit
            await rate_limiter.check_and_record(strategy.user_id, "follow")

            try:
                # Get user ID if we only have username
                if not target.twitter_user_id and target.twitter_username:
                    user_data = await twitter_service.get_user_by_username(
                        access_token, target.twitter_username
                    )
                    if user_data and user_data.get("data"):
                        target.twitter_user_id = user_data["data"]["id"]
                    else:
                        target.mark_failed("User not found")
                        return False

                await twitter_service.follow_user(
                    access_token, target.twitter_user_id
                )

                target.mark_completed()

                await growth_service.log_engagement(
                    strategy_id=strategy.id,
                    action_type=ActionType.FOLLOW,
                    success=True,
                    twitter_user_id=target.twitter_user_id,
                    twitter_username=target.twitter_username,
                )

                return True

            except TwitterRateLimitError as e:
                target.status = EngagementStatus.PENDING  # Will retry
                raise RateLimitError("follow", 0)

            except TwitterAPIError as e:
                target.mark_failed(str(e)[:200])
                await growth_service.log_engagement(
                    strategy_id=strategy.id,
                    action_type=ActionType.FOLLOW,
                    success=False,
                    twitter_user_id=target.twitter_user_id,
                    twitter_username=target.twitter_username,
                    error_message=str(e)[:200],
                )
                return False

    elif target.target_type == TargetType.TWEET:
        success_count = 0

        # Like action
        if target.should_like:
            try:
                await rate_limiter.check_and_record(strategy.user_id, "like")

                await twitter_service.like_tweet(access_token, target.tweet_id)

                await growth_service.log_engagement(
                    strategy_id=strategy.id,
                    action_type=ActionType.LIKE,
                    success=True,
                    tweet_id=target.tweet_id,
                    twitter_username=target.tweet_author,
                )

                success_count += 1

            except (TwitterRateLimitError, RateLimitError):
                pass  # Continue with other actions
            except TwitterAPIError as e:
                logger.warning("Like failed", tweet_id=target.tweet_id, error=str(e))

        # Retweet action
        if target.should_retweet:
            try:
                await rate_limiter.check_and_record(strategy.user_id, "post")

                await twitter_service.retweet(access_token, target.tweet_id)

                await growth_service.log_engagement(
                    strategy_id=strategy.id,
                    action_type=ActionType.RETWEET,
                    success=True,
                    tweet_id=target.tweet_id,
                    twitter_username=target.tweet_author,
                )

                success_count += 1

            except (TwitterRateLimitError, RateLimitError):
                pass
            except TwitterAPIError as e:
                logger.warning("Retweet failed", tweet_id=target.tweet_id, error=str(e))

        # Reply action
        if target.should_reply and target.reply_content:
            # Check if reply is approved (if required)
            if strategy.require_reply_approval and not target.reply_approved:
                logger.info(
                    "Reply requires approval",
                    target_id=str(target.id),
                )
            else:
                try:
                    await rate_limiter.check_and_record(strategy.user_id, "post")

                    result = await twitter_service.reply_to_tweet(
                        access_token, target.tweet_id, target.reply_content
                    )

                    reply_tweet_id = result.get("data", {}).get("id")

                    await growth_service.log_engagement(
                        strategy_id=strategy.id,
                        action_type=ActionType.REPLY,
                        success=True,
                        tweet_id=target.tweet_id,
                        twitter_username=target.tweet_author,
                        reply_content=target.reply_content,
                        reply_tweet_id=reply_tweet_id,
                    )

                    success_count += 1

                except (TwitterRateLimitError, RateLimitError):
                    pass
                except TwitterAPIError as e:
                    logger.warning("Reply failed", tweet_id=target.tweet_id, error=str(e))

        if success_count > 0:
            target.mark_completed()
            return True
        else:
            target.mark_failed("No actions succeeded")
            return False

    return False


@shared_task
def discover_engagement_targets(strategy_id: str) -> dict:
    """Discover new engagement targets for a strategy."""
    return run_async(_discover_engagement_targets_async(strategy_id))


async def _discover_engagement_targets_async(strategy_id: str) -> dict:
    """Async implementation of target discovery."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            strategy = await growth_service.get_strategy(UUID(strategy_id))

            if not strategy:
                return {"success": False, "error": "Strategy not found"}

            if strategy.status != StrategyStatus.ACTIVE:
                return {"success": False, "error": "Strategy not active"}

            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)

            if not access_token:
                return {"success": False, "error": "No Twitter access token"}

            # Find account targets
            account_targets = await growth_service.find_target_accounts(
                strategy=strategy,
                twitter_service=twitter_service,
                access_token=access_token,
                limit=30,
            )

            # Find tweet targets
            tweet_targets = await growth_service.find_engagement_tweets(
                strategy=strategy,
                twitter_service=twitter_service,
                access_token=access_token,
                limit=20,
            )

            # Generate replies for tweet targets
            user_service = UserService(db)
            deepseek_key = await user_service.get_decrypted_api_key(
                strategy.user_id, APIKeyType.DEEPSEEK
            )

            if deepseek_key:
                for target in tweet_targets:
                    if target.should_reply and not target.reply_content:
                        try:
                            await growth_service.generate_reply_content(
                                target=target,
                                strategy=strategy,
                                api_key=deepseek_key,
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to generate reply",
                                target_id=str(target.id),
                                error=str(e),
                            )

            await db.commit()

            logger.info(
                "Engagement targets discovered",
                strategy_id=strategy_id,
                accounts=len(account_targets),
                tweets=len(tweet_targets),
            )

            return {
                "success": True,
                "accounts_found": len(account_targets),
                "tweets_found": len(tweet_targets),
            }

        except Exception as e:
            logger.exception(
                "Error discovering engagement targets",
                strategy_id=strategy_id,
            )
            await db.rollback()
            return {"success": False, "error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task
def update_strategy_metrics(strategy_id: str) -> dict:
    """Update metrics for a strategy (daily task)."""
    return run_async(_update_strategy_metrics_async(strategy_id))


async def _update_strategy_metrics_async(strategy_id: str) -> dict:
    """Async implementation of metrics update."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            strategy = await growth_service.get_strategy(UUID(strategy_id))

            if not strategy:
                return {"success": False, "error": "Strategy not found"}

            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)

            if access_token:
                # Update follower count
                await growth_service.update_follower_count(
                    strategy=strategy,
                    twitter_service=twitter_service,
                    access_token=access_token,
                )

            # Record daily progress
            await growth_service.record_daily_progress(strategy)

            await db.commit()

            logger.info(
                "Strategy metrics updated",
                strategy_id=strategy_id,
                followers=strategy.current_followers,
            )

            return {
                "success": True,
                "current_followers": strategy.current_followers,
                "followers_gained": strategy.followers_gained,
            }

        except Exception as e:
            logger.exception(
                "Error updating strategy metrics",
                strategy_id=strategy_id,
            )
            await db.rollback()
            return {"success": False, "error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task
def update_all_strategy_metrics() -> dict:
    """Update metrics for all active strategies (daily task)."""
    return run_async(_update_all_strategy_metrics_async())


async def _update_all_strategy_metrics_async() -> dict:
    """Async implementation of updating all strategy metrics."""
    async with get_celery_db_context() as db:
        growth_service = GrowthStrategyService(db)
        active_strategies = await growth_service.get_active_strategies()

        updated = 0
        for strategy in active_strategies:
            update_strategy_metrics.delay(str(strategy.id))
            updated += 1

        logger.info("Queued strategy metrics updates", count=updated)
        return {"queued": updated}


@shared_task
def ai_strategy_review(strategy_id: str) -> dict:
    """Weekly AI review and adjustment of strategy."""
    return run_async(_ai_strategy_review_async(strategy_id))


async def _ai_strategy_review_async(strategy_id: str) -> dict:
    """Async implementation of AI strategy review."""
    async with get_celery_db_context() as db:
        try:
            growth_service = GrowthStrategyService(db)
            user_service = UserService(db)

            strategy = await growth_service.get_strategy(UUID(strategy_id))
            if not strategy:
                return {"success": False, "error": "Strategy not found"}

            # Get analytics
            analytics = await growth_service.get_strategy_analytics(strategy.id)

            # Get DeepSeek key for AI review
            deepseek_key = await user_service.get_decrypted_api_key(
                strategy.user_id, APIKeyType.DEEPSEEK
            )

            if not deepseek_key:
                return {"success": False, "error": "No DeepSeek API key"}

            from app.services.deepseek import DeepSeekService
            deepseek = DeepSeekService(deepseek_key)

            try:
                system_prompt = """You are a Twitter growth strategist reviewing a growth strategy's performance.

Analyze the data and provide actionable recommendations.

Output JSON:
{
    "performance_summary": "Brief assessment of progress",
    "what_is_working": ["...", "..."],
    "what_needs_improvement": ["...", "..."],
    "recommended_adjustments": [
        {"parameter": "daily_follows", "current": X, "recommended": Y, "reason": "..."},
        ...
    ],
    "observations": "...",
    "risk_level": "low/medium/high"
}"""

                user_prompt = f"""Review this Twitter growth strategy:

Strategy: {strategy.name}
Duration: {strategy.duration_days} days
Days remaining: {strategy.days_remaining}

Starting followers: {strategy.starting_followers}
Current followers: {strategy.current_followers}
Followers gained: {strategy.followers_gained}
Growth rate: {analytics.get('daily_growth', 0)} per day

Total engagements:
- Follows: {strategy.total_follows}
- Likes: {strategy.total_likes}
- Retweets: {strategy.total_retweets}
- Replies: {strategy.total_replies}

Estimated results were: {strategy.estimated_results}

Current daily quotas:
- Follows: {strategy.daily_follows}
- Likes: {strategy.daily_likes}
- Retweets: {strategy.daily_retweets}
- Replies: {strategy.daily_replies}

Provide your analysis and recommendations."""

                response = await deepseek._call_api(system_prompt, user_prompt)

                # Parse response
                import json
                response = response.strip()
                if response.startswith("```"):
                    response = response.split("```")[1]
                    if response.startswith("json"):
                        response = response[4:]

                review = json.loads(response.strip())

                # Store review in strategy plan
                if not strategy.strategy_plan:
                    strategy.strategy_plan = {}
                strategy.strategy_plan["last_review"] = {
                    "date": datetime.now(timezone.utc).isoformat(),
                    "review": review,
                }

                await db.commit()

                logger.info(
                    "AI strategy review completed",
                    strategy_id=strategy_id,
                    risk_level=review.get("risk_level"),
                )

                return {"success": True, "review": review}

            finally:
                await deepseek.close()

        except Exception as e:
            logger.exception(
                "Error in AI strategy review",
                strategy_id=strategy_id,
            )
            await db.rollback()
            return {"success": False, "error": str(e)}


@shared_task
def cleanup_rate_limit_trackers() -> dict:
    """Clean up old rate limit trackers (daily task)."""
    return run_async(_cleanup_rate_limit_trackers_async())


async def _cleanup_rate_limit_trackers_async() -> dict:
    """Async implementation of rate limit tracker cleanup."""
    async with get_celery_db_context() as db:
        rate_limiter = EngagementRateLimiter(db)
        deleted = await rate_limiter.reset_daily_counts()
        await db.commit()

        logger.info("Rate limit trackers cleaned up", deleted=deleted)
        return {"deleted": deleted}
