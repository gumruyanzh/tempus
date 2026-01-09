"""Celery tasks for growth strategy processing."""

import asyncio
import random
from datetime import datetime, timedelta, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import func, select

from app.core.database import get_celery_db_context
from app.core.logging import get_logger
from app.models.system_log import LogCategory, LogLevel
from app.models.growth_strategy import (
    ActionType,
    Circle1Member,
    ConversationReply,
    ConversationStatus,
    ConversationThread,
    EngagementLog,
    EngagementStatus,
    EngagementTarget,
    GrowthStrategy,
    StrategyStatus,
    TargetType,
)
from app.models.user import APIKeyType
from app.services.growth_strategy import GrowthStrategyService
from app.services.rate_limiter import EngagementRateLimiter, RateLimitError
from app.services.system_logging import SystemLoggingService
from app.services.twitter import TwitterAPIError, TwitterRateLimitError, TwitterService
from app.services.user import UserService

logger = get_logger(__name__)


async def log_to_db(
    db,
    level: LogLevel,
    message: str,
    strategy_id: UUID = None,
    task_name: str = None,
    task_id: str = None,
    exception: Exception = None,
    details: dict = None,
):
    """Helper to log to database within Celery tasks."""
    try:
        log_service = SystemLoggingService(db)
        await log_service.log(
            level=level,
            message=message,
            category=LogCategory.GROWTH,
            logger_name="app.tasks.growth_tasks",
            task_name=task_name,
            task_id=task_id,
            strategy_id=strategy_id,
            details=details,
            exception=exception,
        )
    except Exception:
        pass  # Don't fail task if logging fails


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

        await log_to_db(
            db, LogLevel.INFO,
            f"Processing {len(active_strategies)} active growth strategies",
            task_name="process_growth_strategies",
        )

        processed = 0
        for strategy in active_strategies:
            try:
                # Check if strategy has completed
                if strategy.is_complete:
                    strategy.mark_completed()
                    await db.commit()
                    await log_to_db(
                        db, LogLevel.INFO,
                        f"Growth strategy '{strategy.name}' completed",
                        strategy_id=strategy.id,
                        task_name="process_growth_strategies",
                        details={"followers_gained": strategy.followers_gained},
                    )
                    logger.info(
                        "Growth strategy completed",
                        strategy_id=str(strategy.id),
                    )
                    continue

                # Queue engagement execution task
                execute_strategy_engagements.delay(str(strategy.id))

                # Queue post generation if configured (every ~30 min = 6 cycles of 5 min)
                import random
                if strategy.daily_posts > 0 and random.random() < 0.17:  # ~1/6 chance
                    generate_and_post_content.delay(str(strategy.id))

                processed += 1

            except Exception as e:
                await log_to_db(
                    db, LogLevel.ERROR,
                    f"Error processing strategy '{strategy.name}'",
                    strategy_id=strategy.id,
                    task_name="process_growth_strategies",
                    exception=e,
                )
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

            # Check for conservative mode (account < 90 days old)
            # This protects new accounts from being flagged as spam
            try:
                current_user = await twitter_service.get_current_user(access_token)
                user_data = current_user.get("data", {})

                # Parse account created_at if available
                account_created_at = None
                if user_data.get("created_at"):
                    try:
                        account_created_at = datetime.fromisoformat(
                            user_data["created_at"].replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

                # Get total tweets for conservative mode check
                total_tweets = user_data.get("public_metrics", {}).get("tweet_count", 0)

                conservative_info = growth_service.should_use_conservative_mode(
                    account_created_at=account_created_at,
                    total_tweets=total_tweets,
                )

                if conservative_info["conservative_mode"]:
                    logger.info(
                        "Using conservative mode for new account",
                        strategy_id=strategy_id,
                        account_age_days=conservative_info.get("account_age_days"),
                        max_daily_follows=conservative_info.get("max_daily_follows"),
                        has_new_account_boost=conservative_info.get("has_new_account_boost"),
                    )

            except Exception as e:
                logger.warning(f"Error checking conservative mode: {e}")
                conservative_info = {"conservative_mode": False}

            # Check rate limits
            rate_limiter = EngagementRateLimiter(db)
            if await rate_limiter.should_pause(strategy.user_id):
                logger.info(
                    "Pausing engagements due to high rate limit usage",
                    strategy_id=strategy_id,
                )
                return {"success": True, "paused": True}

            # Check spam limits (algorithm research shows these thresholds trigger suppression)
            # Get hourly action counts
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            hourly_stmt = select(
                EngagementLog.action_type,
                func.count(EngagementLog.id),
            ).where(
                EngagementLog.strategy_id == strategy.id,
                EngagementLog.success == True,
                EngagementLog.created_at >= one_hour_ago,
            ).group_by(EngagementLog.action_type)

            hourly_result = await db.execute(hourly_stmt)
            hourly_counts = {row[0].value: row[1] for row in hourly_result.all()}

            # Convert to spam check format
            actions_this_hour = {
                "follows": hourly_counts.get("follow", 0),
                "unfollows": hourly_counts.get("unfollow", 0),
                "likes": hourly_counts.get("like", 0),
                "posts": hourly_counts.get("post", 0) + hourly_counts.get("reply", 0) + hourly_counts.get("retweet", 0),
            }

            spam_check = growth_service.check_spam_limits(actions_this_hour)

            if not spam_check["is_safe"]:
                logger.warning(
                    "Spam limits reached - pausing engagements",
                    strategy_id=strategy_id,
                    warnings=spam_check["warnings"],
                    current=spam_check["current"],
                )
                await log_to_db(
                    db, LogLevel.WARNING,
                    "Spam limits reached - pausing to protect account",
                    strategy_id=UUID(strategy_id),
                    task_name="execute_strategy_engagements",
                    details=spam_check,
                )
                return {"success": True, "paused": True, "reason": "spam_limits_reached"}

            if spam_check["warnings"]:
                logger.info(
                    "Approaching spam limits",
                    strategy_id=strategy_id,
                    warnings=spam_check["warnings"],
                )

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
                        conservative_info=conservative_info,
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

            await log_to_db(
                db, LogLevel.INFO,
                f"Executed {executed} engagements for strategy",
                strategy_id=UUID(strategy_id),
                task_name="execute_strategy_engagements",
                details={"executed": executed},
            )

            logger.info(
                "Strategy engagements executed",
                strategy_id=strategy_id,
                executed=executed,
            )

            return {"success": True, "executed": executed}

        except Exception as e:
            await log_to_db(
                db, LogLevel.ERROR,
                f"Error executing strategy engagements",
                strategy_id=UUID(strategy_id),
                task_name="execute_strategy_engagements",
                exception=e,
            )
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
    conservative_info: dict = None,
) -> bool:
    """Execute a single engagement action."""
    if conservative_info is None:
        conservative_info = {"conservative_mode": False}

    if target.target_type == TargetType.ACCOUNT:
        # Follow action
        if target.should_follow:
            # Check follower/following ratio before following
            # Algorithm research: ratio < 1.0 hurts distribution, < 0.5 = reduced reach
            current_user = await twitter_service.get_current_user(access_token)
            user_metrics = current_user.get("data", {}).get("public_metrics", {})
            current_followers = user_metrics.get("followers_count", 0)
            current_following = user_metrics.get("following_count", 0)

            ratio_info = growth_service.calculate_safe_follow_limit(
                current_followers=current_followers,
                current_following=current_following,
                target_ratio=1.5,  # Target minimum healthy ratio
            )

            # Block follows if ratio is critical or no safe follows available
            if ratio_info["status"] == "critical":
                logger.warning(
                    "Follow blocked - ratio critical",
                    strategy_id=str(strategy.id),
                    ratio=ratio_info["current_ratio"],
                    recommendation=ratio_info["recommendation"],
                )
                target.mark_failed("Ratio protection: ratio too low")
                return False

            if ratio_info["safe_new_follows"] <= 0:
                logger.info(
                    "Follow skipped - protecting ratio",
                    strategy_id=str(strategy.id),
                    ratio=ratio_info["current_ratio"],
                    safe_follows=ratio_info["safe_new_follows"],
                )
                target.mark_failed("Ratio protection: no safe follows available")
                return False

            # Additional check for conservative mode (new accounts < 90 days)
            if conservative_info.get("conservative_mode"):
                max_daily_follows = conservative_info.get("max_daily_follows", 15)

                # Check today's follow count against conservative limit
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                follow_count_stmt = select(func.count(EngagementLog.id)).where(
                    EngagementLog.strategy_id == strategy.id,
                    EngagementLog.action_type == ActionType.FOLLOW,
                    EngagementLog.success == True,
                    EngagementLog.created_at >= today_start,
                )
                follow_count_result = await db.execute(follow_count_stmt)
                today_follows = follow_count_result.scalar() or 0

                if today_follows >= max_daily_follows:
                    logger.info(
                        "Follow skipped - conservative mode daily limit",
                        strategy_id=str(strategy.id),
                        today_follows=today_follows,
                        max_daily_follows=max_daily_follows,
                        account_age_days=conservative_info.get("account_age_days"),
                    )
                    target.mark_failed(f"Conservative mode: {today_follows}/{max_daily_follows} follows today")
                    return False

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

                    engagement_log = await growth_service.log_engagement(
                        strategy_id=strategy.id,
                        action_type=ActionType.REPLY,
                        success=True,
                        tweet_id=target.tweet_id,
                        twitter_username=target.tweet_author,
                        reply_content=target.reply_content,
                        reply_tweet_id=reply_tweet_id,
                    )

                    success_count += 1

                    # Create conversation thread for monitoring
                    # This captures the 75x algorithmic multiplier from reply-to-reply
                    if reply_tweet_id:
                        create_conversation_thread.delay(
                            strategy_id=str(strategy.id),
                            engagement_log_id=str(engagement_log.id) if engagement_log else None,
                            original_tweet_id=target.tweet_id,
                            original_author_id=target.tweet_author_id or "",
                            original_author_username=target.tweet_author or "",
                            original_content=target.tweet_content or "",
                            our_reply_tweet_id=reply_tweet_id,
                            our_reply_content=target.reply_content,
                            author_follower_count=None,  # Will be enriched in monitoring
                        )

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
def generate_and_post_content(strategy_id: str) -> dict:
    """Generate and post original content for a strategy."""
    return run_async(_generate_and_post_content_async(strategy_id))


async def _generate_and_post_content_async(strategy_id: str) -> dict:
    """Async implementation of content generation and posting."""
    from app.models.growth_strategy import EngagementLog
    from datetime import timedelta

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

            # Check if we should post today (based on daily_posts quota)
            if strategy.daily_posts <= 0:
                return {"success": True, "message": "No posts configured"}

            # Check cooldown - minimum 30 minutes between posts
            last_post_stmt = select(EngagementLog).where(
                EngagementLog.strategy_id == strategy.id,
                EngagementLog.action_type == ActionType.POST,
                EngagementLog.success == True,
            ).order_by(EngagementLog.created_at.desc()).limit(1)

            last_post_result = await db.execute(last_post_stmt)
            last_post = last_post_result.scalar_one_or_none()

            if last_post:
                time_since_last = datetime.now(timezone.utc) - last_post.created_at
                if time_since_last < timedelta(minutes=30):
                    return {"success": True, "message": f"Cooldown active, last post was {time_since_last.seconds // 60} minutes ago"}

            # Check rate limits
            rate_limiter = EngagementRateLimiter(db)
            remaining = await rate_limiter.get_remaining(strategy.user_id, "post")
            if remaining <= 0:
                return {"success": True, "message": "Post rate limit reached"}

            # Get DeepSeek key
            deepseek_key = await user_service.get_decrypted_api_key(
                strategy.user_id, APIKeyType.DEEPSEEK
            )
            if not deepseek_key:
                return {"success": False, "error": "No DeepSeek API key"}

            # Get Twitter access
            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)
            if not access_token:
                return {"success": False, "error": "No Twitter access token"}

            # Generate post content (now returns dict with text and optional image)
            content_result = await growth_service.generate_post_content(
                strategy=strategy,
                api_key=deepseek_key,
                include_image=False,  # Disabled - using OAuth 2.0 only (no media upload)
            )

            if not content_result:
                return {"success": False, "error": "Failed to generate content"}

            post_text = content_result.get("text")
            image_bytes = content_result.get("image_bytes")
            image_alt_text = content_result.get("image_alt_text")

            if not post_text:
                return {"success": False, "error": "No text content generated"}

            # Record rate limit
            await rate_limiter.check_and_record(strategy.user_id, "post")

            # Upload image if available
            media_ids = None
            if image_bytes:
                try:
                    media_id = await twitter_service.upload_media(
                        access_token=access_token,
                        media_bytes=image_bytes,
                        media_type="image/jpeg",
                        alt_text=image_alt_text,
                    )
                    media_ids = [media_id]
                    logger.info(
                        "Media uploaded for post",
                        media_id=media_id,
                        image_size=len(image_bytes),
                    )
                except Exception as e:
                    # Log but continue without image
                    logger.warning(
                        "Failed to upload media, posting without image",
                        error=str(e),
                    )

            # Post to Twitter (with media if available)
            result = await twitter_service.post_tweet(
                access_token=access_token,
                text=post_text,
                media_ids=media_ids,
            )
            tweet_id = result.get("data", {}).get("id")

            # Log engagement
            await growth_service.log_engagement(
                strategy_id=strategy.id,
                action_type=ActionType.POST,
                success=True,
                tweet_id=tweet_id,
                reply_content=post_text,
            )

            await db.commit()

            logger.info(
                "Original post created",
                strategy_id=strategy_id,
                tweet_id=tweet_id,
                has_media=media_ids is not None,
            )

            return {"success": True, "tweet_id": tweet_id, "content": post_text, "has_media": media_ids is not None}

        except Exception as e:
            logger.exception(
                "Error generating/posting content",
                strategy_id=strategy_id,
            )
            await db.rollback()
            return {"success": False, "error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task
def apply_tier_based_quotas() -> dict:
    """Apply account-size based activity quotas to all strategies.

    Runs daily to optimize quotas based on:
    - Current follower count (tier determination)
    - Follower/following ratio
    - Algorithm-optimal activity levels
    """
    return run_async(_apply_tier_based_quotas_async())


async def _apply_tier_based_quotas_async() -> dict:
    """Async implementation of tier-based quota application."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            active_strategies = await growth_service.get_active_strategies()

            if not active_strategies:
                return {"updated": 0}

            updated = 0
            tier_distribution = {"starter": 0, "growing": 0, "established": 0}

            for strategy in active_strategies:
                try:
                    twitter_service = TwitterService(db)
                    access_token = await twitter_service.get_valid_access_token(strategy.user_id)

                    if not access_token:
                        continue

                    # Get current metrics
                    current_user = await twitter_service.get_current_user(access_token)
                    user_metrics = current_user.get("data", {}).get("public_metrics", {})
                    current_followers = user_metrics.get("followers_count", 0)
                    current_following = user_metrics.get("following_count", 0)

                    # Apply tier-based quotas
                    result = await growth_service.apply_tier_based_quotas(
                        strategy=strategy,
                        current_followers=current_followers,
                        current_following=current_following,
                    )

                    tier_distribution[result["tier"]] = tier_distribution.get(result["tier"], 0) + 1

                    if result["changes_applied"]:
                        updated += 1

                    await twitter_service.close()
                    twitter_service = None

                except Exception as e:
                    logger.warning(
                        "Error applying tier quotas for strategy",
                        strategy_id=str(strategy.id),
                        error=str(e),
                    )

            await db.commit()

            await log_to_db(
                db, LogLevel.INFO,
                f"Tier-based quotas applied to {updated} strategies",
                task_name="apply_tier_based_quotas",
                details={
                    "updated": updated,
                    "tier_distribution": tier_distribution,
                },
            )

            logger.info(
                "Tier-based quotas applied",
                updated=updated,
                tier_distribution=tier_distribution,
            )

            return {
                "updated": updated,
                "tier_distribution": tier_distribution,
            }

        except Exception as e:
            logger.exception("Error applying tier-based quotas")
            await db.rollback()
            return {"error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


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


# =============================================================================
# CONVERSATION CONTINUATION SYSTEM
# Captures the 75x algorithmic multiplier from reply-to-reply interactions
# =============================================================================


@shared_task
def monitor_conversation_replies() -> dict:
    """Monitor active conversations for replies and queue responses.

    This task runs every 5 minutes to:
    1. Find active conversation threads due for checking
    2. Fetch replies to our tweets
    3. Calculate priority scores for conversations
    4. Queue high-priority conversations for response
    """
    return run_async(_monitor_conversation_replies_async())


async def _monitor_conversation_replies_async() -> dict:
    """Async implementation of conversation monitoring."""
    from datetime import timedelta

    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)

            # Get all active conversation threads due for checking
            now = datetime.now(timezone.utc)
            stmt = select(ConversationThread).where(
                ConversationThread.status == ConversationStatus.ACTIVE,
                ConversationThread.next_check_at <= now,
                ConversationThread.monitoring_until > now,
            ).order_by(ConversationThread.priority_score.desc()).limit(50)

            result = await db.execute(stmt)
            threads = result.scalars().all()

            if not threads:
                logger.debug("No conversation threads to monitor")
                return {"monitored": 0, "replies_found": 0}

            await log_to_db(
                db, LogLevel.INFO,
                f"Monitoring {len(threads)} active conversation threads",
                task_name="monitor_conversation_replies",
            )

            monitored = 0
            replies_found = 0
            responses_queued = 0

            # Group threads by strategy to minimize API calls
            threads_by_strategy = {}
            for thread in threads:
                if thread.strategy_id not in threads_by_strategy:
                    threads_by_strategy[thread.strategy_id] = []
                threads_by_strategy[thread.strategy_id].append(thread)

            for strategy_id, strategy_threads in threads_by_strategy.items():
                try:
                    # Get strategy and validate
                    strategy = await growth_service.get_strategy(strategy_id)
                    if not strategy or strategy.status != StrategyStatus.ACTIVE:
                        # Mark all threads as paused
                        for thread in strategy_threads:
                            thread.status = ConversationStatus.PAUSED
                        continue

                    # Get Twitter access
                    twitter_service = TwitterService(db)
                    access_token = await twitter_service.get_valid_access_token(strategy.user_id)

                    if not access_token:
                        logger.warning(
                            "No Twitter access for conversation monitoring",
                            strategy_id=str(strategy_id),
                        )
                        continue

                    # Get our user ID
                    current_user = await twitter_service.get_current_user(access_token)
                    our_user_id = current_user["data"]["id"]

                    for thread in strategy_threads:
                        try:
                            # Fetch replies to our tweet
                            replies = await twitter_service.get_tweet_replies(
                                access_token=access_token,
                                tweet_id=thread.our_reply_tweet_id,
                                our_user_id=our_user_id,
                                max_results=20,
                            )

                            thread.last_checked_at = now
                            monitored += 1

                            # Find new replies (not already in our database)
                            existing_reply_ids = {
                                r.tweet_id for r in thread.replies
                            }

                            new_replies = [
                                r for r in replies
                                if r["id"] not in existing_reply_ids
                                and r.get("is_direct_reply", False)
                            ]

                            if new_replies:
                                replies_found += len(new_replies)

                                # Process each new reply
                                for reply_data in new_replies:
                                    # Create ConversationReply record
                                    reply = ConversationReply(
                                        thread_id=thread.id,
                                        tweet_id=reply_data["id"],
                                        in_reply_to_tweet_id=thread.our_reply_tweet_id,
                                        author_id=reply_data["author_id"],
                                        author_username=reply_data.get("author_username"),
                                        is_from_us=False,
                                        content=reply_data.get("text"),
                                        like_count=reply_data.get("metrics", {}).get("like_count", 0),
                                        reply_count=reply_data.get("metrics", {}).get("reply_count", 0),
                                        retweet_count=reply_data.get("metrics", {}).get("retweet_count", 0),
                                        posted_at=datetime.fromisoformat(
                                            reply_data["created_at"].replace("Z", "+00:00")
                                        ) if reply_data.get("created_at") else now,
                                    )
                                    db.add(reply)

                                    # Update thread metrics
                                    thread.author_follower_count = reply_data.get("author_follower_count")
                                    thread.author_following_count = reply_data.get("author_following_count")
                                    thread.last_reply_received_at = now

                                # Update priority score
                                thread.calculate_priority_score()

                                # Queue response if high priority
                                if thread.should_continue():
                                    process_conversation_reply.delay(
                                        str(thread.id),
                                        new_replies[0]["id"]  # Process most recent reply
                                    )
                                    responses_queued += 1

                            else:
                                # No new replies, check if we should abandon
                                if thread.last_reply_received_at:
                                    time_since_reply = now - thread.last_reply_received_at
                                else:
                                    time_since_reply = now - thread.created_at

                                # Abandon if no reply in 2 hours
                                if time_since_reply > timedelta(hours=2):
                                    thread.mark_abandoned()

                            # Set next check time (15 min if active, longer if less engagement)
                            if thread.status == ConversationStatus.ACTIVE:
                                if thread.priority_score >= 70:
                                    thread.next_check_at = now + timedelta(minutes=10)
                                elif thread.priority_score >= 50:
                                    thread.next_check_at = now + timedelta(minutes=15)
                                else:
                                    thread.next_check_at = now + timedelta(minutes=30)

                        except Exception as e:
                            logger.warning(
                                "Error processing conversation thread",
                                thread_id=str(thread.id),
                                error=str(e),
                            )

                    if twitter_service:
                        await twitter_service.close()
                        twitter_service = None

                except Exception as e:
                    logger.error(
                        "Error monitoring conversations for strategy",
                        strategy_id=str(strategy_id),
                        error=str(e),
                    )

            await db.commit()

            await log_to_db(
                db, LogLevel.INFO,
                f"Conversation monitoring complete: {monitored} checked, {replies_found} replies, {responses_queued} queued",
                task_name="monitor_conversation_replies",
                details={
                    "monitored": monitored,
                    "replies_found": replies_found,
                    "responses_queued": responses_queued,
                },
            )

            logger.info(
                "Conversation monitoring complete",
                monitored=monitored,
                replies_found=replies_found,
                responses_queued=responses_queued,
            )

            return {
                "monitored": monitored,
                "replies_found": replies_found,
                "responses_queued": responses_queued,
            }

        except Exception as e:
            logger.exception("Error in conversation monitoring")
            await db.rollback()
            return {"error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_conversation_reply(self, thread_id: str, reply_tweet_id: str) -> dict:
    """Process a conversation reply and generate a response.

    Args:
        thread_id: UUID of the ConversationThread
        reply_tweet_id: The tweet ID we're responding to
    """
    return run_async(_process_conversation_reply_async(self, thread_id, reply_tweet_id))


async def _process_conversation_reply_async(task, thread_id: str, reply_tweet_id: str) -> dict:
    """Async implementation of conversation reply processing."""
    from datetime import timedelta

    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            user_service = UserService(db)

            # Get the conversation thread
            stmt = select(ConversationThread).where(
                ConversationThread.id == UUID(thread_id)
            )
            result = await db.execute(stmt)
            thread = result.scalar_one_or_none()

            if not thread:
                return {"success": False, "error": "Thread not found"}

            if thread.status != ConversationStatus.ACTIVE:
                return {"success": False, "error": "Thread not active"}

            if thread.depth >= thread.max_depth:
                thread.mark_completed("max_depth_reached")
                await db.commit()
                return {"success": True, "message": "Max depth reached"}

            # Get strategy
            strategy = await growth_service.get_strategy(thread.strategy_id)
            if not strategy or strategy.status != StrategyStatus.ACTIVE:
                thread.status = ConversationStatus.PAUSED
                await db.commit()
                return {"success": False, "error": "Strategy not active"}

            # Check rate limits (reserve 30% of post budget for conversations)
            rate_limiter = EngagementRateLimiter(db)
            remaining = await rate_limiter.get_remaining(strategy.user_id, "post")
            daily_limit = 100  # Twitter's daily post limit shared across actions

            # Only proceed if we have at least 30% of daily budget
            if remaining < daily_limit * 0.3:
                logger.info(
                    "Skipping conversation reply - preserving rate limit budget",
                    thread_id=thread_id,
                    remaining=remaining,
                )
                return {"success": True, "message": "Rate limit budget preserved"}

            # Get Twitter access
            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(strategy.user_id)

            if not access_token:
                return {"success": False, "error": "No Twitter access token"}

            # Get DeepSeek key for AI reply generation
            deepseek_key = await user_service.get_decrypted_api_key(
                strategy.user_id, APIKeyType.DEEPSEEK
            )

            if not deepseek_key:
                return {"success": False, "error": "No DeepSeek API key"}

            # Build conversation context for AI
            conversation_context = await _build_conversation_context(db, thread)

            # Generate reply using AI
            reply_content = await growth_service.generate_conversation_reply(
                thread=thread,
                strategy=strategy,
                conversation_context=conversation_context,
                api_key=deepseek_key,
            )

            if not reply_content:
                return {"success": False, "error": "Failed to generate reply"}

            # Add natural delay before posting (3-10 minutes to appear human)
            delay_seconds = random.randint(180, 600)
            await asyncio.sleep(min(delay_seconds, 30))  # Cap at 30s for task

            # Record rate limit
            await rate_limiter.check_and_record(strategy.user_id, "post")

            # Post the reply
            result = await twitter_service.reply_to_tweet(
                access_token=access_token,
                tweet_id=reply_tweet_id,
                text=reply_content,
            )

            our_reply_id = result.get("data", {}).get("id")

            if not our_reply_id:
                return {"success": False, "error": "No tweet ID in response"}

            # Record our reply in the conversation
            our_reply = ConversationReply(
                thread_id=thread.id,
                tweet_id=our_reply_id,
                in_reply_to_tweet_id=reply_tweet_id,
                author_id="",  # Will be filled from current user
                author_username="",
                is_from_us=True,
                content=reply_content,
                response_delay_seconds=delay_seconds,
                posted_at=datetime.now(timezone.utc),
            )

            # Get our user info
            current_user = await twitter_service.get_current_user(access_token)
            our_reply.author_id = current_user["data"]["id"]
            our_reply.author_username = current_user["data"]["username"]

            db.add(our_reply)

            # Update thread
            thread.increment_depth()
            thread.our_reply_tweet_id = our_reply_id  # Update to track latest reply
            thread.next_check_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            thread.total_engagement_gained += 1

            # Log engagement
            await growth_service.log_engagement(
                strategy_id=strategy.id,
                action_type=ActionType.REPLY,
                success=True,
                tweet_id=reply_tweet_id,
                reply_content=reply_content,
                reply_tweet_id=our_reply_id,
            )

            await db.commit()

            await log_to_db(
                db, LogLevel.INFO,
                f"Conversation reply sent (depth: {thread.depth})",
                strategy_id=strategy.id,
                task_name="process_conversation_reply",
                details={
                    "thread_id": thread_id,
                    "depth": thread.depth,
                    "reply_tweet_id": our_reply_id,
                },
            )

            logger.info(
                "Conversation reply posted",
                thread_id=thread_id,
                depth=thread.depth,
                reply_id=our_reply_id,
            )

            return {
                "success": True,
                "reply_id": our_reply_id,
                "depth": thread.depth,
            }

        except Exception as e:
            logger.exception(
                "Error processing conversation reply",
                thread_id=thread_id,
            )
            await db.rollback()
            return {"success": False, "error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


async def _build_conversation_context(db, thread: ConversationThread) -> str:
    """Build conversation context string for AI prompt."""
    parts = []

    # Original tweet
    parts.append(f"Original tweet: {thread.original_tweet_content or '[Content unavailable]'}")
    parts.append(f"Original author: @{thread.original_tweet_author_username or 'unknown'}")

    # Our first reply
    parts.append(f"\nOur first reply: {thread.our_reply_content or '[Content unavailable]'}")

    # Subsequent replies in order
    if thread.replies:
        parts.append("\nConversation thread:")
        for reply in sorted(thread.replies, key=lambda r: r.posted_at):
            prefix = "Us" if reply.is_from_us else f"@{reply.author_username or 'them'}"
            parts.append(f"  {prefix}: {reply.content or '[No content]'}")

    return "\n".join(parts)


@shared_task
def create_conversation_thread(
    strategy_id: str,
    engagement_log_id: str,
    original_tweet_id: str,
    original_author_id: str,
    original_author_username: str,
    original_content: str,
    our_reply_tweet_id: str,
    our_reply_content: str,
    author_follower_count: int = None,
) -> dict:
    """Create a new conversation thread after posting a reply.

    This should be called after successfully posting a reply to start
    tracking the conversation for continuation.
    """
    return run_async(_create_conversation_thread_async(
        strategy_id=strategy_id,
        engagement_log_id=engagement_log_id,
        original_tweet_id=original_tweet_id,
        original_author_id=original_author_id,
        original_author_username=original_author_username,
        original_content=original_content,
        our_reply_tweet_id=our_reply_tweet_id,
        our_reply_content=our_reply_content,
        author_follower_count=author_follower_count,
    ))


async def _create_conversation_thread_async(
    strategy_id: str,
    engagement_log_id: str,
    original_tweet_id: str,
    original_author_id: str,
    original_author_username: str,
    original_content: str,
    our_reply_tweet_id: str,
    our_reply_content: str,
    author_follower_count: int = None,
) -> dict:
    """Async implementation of conversation thread creation."""
    from datetime import timedelta

    async with get_celery_db_context() as db:
        try:
            now = datetime.now(timezone.utc)

            # Create the conversation thread
            thread = ConversationThread(
                strategy_id=UUID(strategy_id),
                engagement_log_id=UUID(engagement_log_id) if engagement_log_id else None,
                original_tweet_id=original_tweet_id,
                original_tweet_author_id=original_author_id,
                original_tweet_author_username=original_author_username,
                original_tweet_content=original_content,
                our_reply_tweet_id=our_reply_tweet_id,
                our_reply_content=our_reply_content,
                status=ConversationStatus.ACTIVE,
                depth=1,
                author_follower_count=author_follower_count,
                next_check_at=now + timedelta(minutes=15),  # First check in 15 min
                monitoring_until=now + timedelta(hours=6),  # Monitor for 6 hours
            )

            # Calculate initial priority score
            thread.calculate_priority_score()

            db.add(thread)
            await db.commit()
            await db.refresh(thread)

            logger.info(
                "Conversation thread created",
                thread_id=str(thread.id),
                strategy_id=strategy_id,
                priority_score=thread.priority_score,
            )

            return {
                "success": True,
                "thread_id": str(thread.id),
                "priority_score": thread.priority_score,
            }

        except Exception as e:
            logger.exception("Error creating conversation thread")
            await db.rollback()
            return {"success": False, "error": str(e)}


@shared_task
def cleanup_abandoned_conversations() -> dict:
    """Mark expired conversation threads as abandoned.

    Runs daily to clean up threads that have exceeded their monitoring window.
    """
    return run_async(_cleanup_abandoned_conversations_async())


async def _cleanup_abandoned_conversations_async() -> dict:
    """Async implementation of conversation cleanup."""
    async with get_celery_db_context() as db:
        try:
            now = datetime.now(timezone.utc)

            # Find threads past their monitoring window
            stmt = select(ConversationThread).where(
                ConversationThread.status == ConversationStatus.ACTIVE,
                ConversationThread.monitoring_until < now,
            )

            result = await db.execute(stmt)
            threads = result.scalars().all()

            abandoned_count = 0
            for thread in threads:
                thread.mark_abandoned()
                abandoned_count += 1

            await db.commit()

            logger.info(
                "Abandoned conversations cleaned up",
                count=abandoned_count,
            )

            return {"abandoned": abandoned_count}

        except Exception as e:
            logger.exception("Error cleaning up conversations")
            await db.rollback()
            return {"error": str(e)}


# =============================================================================
# CIRCLE 1 NURTURING SYSTEM
# Tracks top mutual engagers and ensures weekly touchpoints
# =============================================================================


@shared_task
def update_circle1_members() -> dict:
    """Update Circle 1 members for all active strategies.

    Runs daily to:
    1. Analyze engagement patterns
    2. Update Circle 1 member list (top 50 mutual engagers)
    3. Calculate Circle 1 scores
    """
    return run_async(_update_circle1_members_async())


async def _update_circle1_members_async() -> dict:
    """Async implementation of Circle 1 member update."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            active_strategies = await growth_service.get_active_strategies()

            if not active_strategies:
                return {"updated": 0}

            total_updated = 0

            for strategy in active_strategies:
                try:
                    twitter_service = TwitterService(db)
                    access_token = await twitter_service.get_valid_access_token(strategy.user_id)

                    if not access_token:
                        continue

                    members = await growth_service.update_circle1_members(
                        strategy=strategy,
                        twitter_service=twitter_service,
                        access_token=access_token,
                        limit=50,
                    )

                    total_updated += len(members)

                    await twitter_service.close()
                    twitter_service = None

                except Exception as e:
                    logger.warning(
                        "Error updating Circle 1 for strategy",
                        strategy_id=str(strategy.id),
                        error=str(e),
                    )

            await db.commit()

            await log_to_db(
                db, LogLevel.INFO,
                f"Circle 1 members updated for all strategies",
                task_name="update_circle1_members",
                details={"total_updated": total_updated},
            )

            logger.info("Circle 1 members updated", total=total_updated)
            return {"updated": total_updated}

        except Exception as e:
            logger.exception("Error updating Circle 1 members")
            await db.rollback()
            return {"error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task
def process_circle1_touchpoints() -> dict:
    """Process Circle 1 touchpoints for strategies needing engagement.

    Runs every 6 hours to:
    1. Find Circle 1 members needing touchpoints
    2. Queue engagement targets for those members
    """
    return run_async(_process_circle1_touchpoints_async())


async def _process_circle1_touchpoints_async() -> dict:
    """Async implementation of Circle 1 touchpoint processing."""
    async with get_celery_db_context() as db:
        twitter_service = None
        try:
            growth_service = GrowthStrategyService(db)
            active_strategies = await growth_service.get_active_strategies()

            if not active_strategies:
                return {"processed": 0}

            total_queued = 0

            for strategy in active_strategies:
                try:
                    # Get Circle 1 members needing touchpoints
                    members = await growth_service.get_circle1_members_needing_touchpoint(
                        strategy_id=strategy.id,
                        limit=5,  # Process 5 per cycle
                    )

                    if not members:
                        continue

                    twitter_service = TwitterService(db)
                    access_token = await twitter_service.get_valid_access_token(strategy.user_id)

                    if not access_token:
                        continue

                    for member in members:
                        # Find a recent tweet from this user to engage with
                        try:
                            tweets = await twitter_service.get_user_tweets(
                                access_token=access_token,
                                user_id=member.twitter_user_id,
                                max_results=5,
                            )

                            if tweets and len(tweets) > 0:
                                # Create engagement target for the most recent tweet
                                tweet = tweets[0]

                                target = EngagementTarget(
                                    strategy_id=strategy.id,
                                    target_type=TargetType.TWEET,
                                    tweet_id=tweet.get("id"),
                                    tweet_author=member.twitter_username,
                                    tweet_author_id=member.twitter_user_id,
                                    tweet_content=tweet.get("text", "")[:500],
                                    should_like=True,
                                    should_reply=True,  # Circle 1 gets replies
                                    status=EngagementStatus.PENDING,
                                    scheduled_for=datetime.now(timezone.utc),
                                    relevance_score=0.9,  # High priority
                                    priority=1,  # Top priority
                                )
                                db.add(target)

                                # Mark touchpoint as pending (will be recorded on execution)
                                total_queued += 1

                        except Exception as e:
                            logger.warning(
                                "Error finding tweets for Circle 1 member",
                                username=member.twitter_username,
                                error=str(e),
                            )

                    await twitter_service.close()
                    twitter_service = None

                except Exception as e:
                    logger.warning(
                        "Error processing Circle 1 touchpoints for strategy",
                        strategy_id=str(strategy.id),
                        error=str(e),
                    )

            await db.commit()

            await log_to_db(
                db, LogLevel.INFO,
                f"Circle 1 touchpoints queued",
                task_name="process_circle1_touchpoints",
                details={"queued": total_queued},
            )

            logger.info("Circle 1 touchpoints queued", count=total_queued)
            return {"queued": total_queued}

        except Exception as e:
            logger.exception("Error processing Circle 1 touchpoints")
            await db.rollback()
            return {"error": str(e)}

        finally:
            if twitter_service:
                await twitter_service.close()


@shared_task
def reset_weekly_circle1_touchpoints() -> dict:
    """Reset weekly touchpoint counters for all strategies.

    Runs weekly (Monday at midnight) to reset touchpoint tracking.
    """
    return run_async(_reset_weekly_circle1_touchpoints_async())


async def _reset_weekly_circle1_touchpoints_async() -> dict:
    """Async implementation of weekly touchpoint reset."""
    async with get_celery_db_context() as db:
        try:
            growth_service = GrowthStrategyService(db)
            active_strategies = await growth_service.get_active_strategies()

            total_reset = 0

            for strategy in active_strategies:
                count = await growth_service.reset_weekly_circle1_touchpoints(strategy.id)
                total_reset += count

            await db.commit()

            logger.info("Weekly Circle 1 touchpoints reset", count=total_reset)
            return {"reset": total_reset}

        except Exception as e:
            logger.exception("Error resetting weekly touchpoints")
            await db.rollback()
            return {"error": str(e)}
