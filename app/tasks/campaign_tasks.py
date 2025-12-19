"""Celery tasks for campaign tweet processing."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.logging import get_logger
from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import APIKeyType
from app.services.audit import AuditService
from app.services.campaign import CampaignService
from app.services.deepseek import DeepSeekService
from app.services.tweet import TweetService
from app.services.twitter import TwitterAPIError, TwitterRateLimitError, TwitterService
from app.services.user import UserService
from app.services.web_search import WebSearchService, WebSearchError

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
def process_campaign_tweets() -> dict:
    """Process campaign tweets that are due for generation and posting."""
    return run_async(_process_campaign_tweets_async())


async def _process_campaign_tweets_async() -> dict:
    """Async implementation of campaign tweet processing."""
    async with async_session_factory() as db:
        campaign_service = CampaignService(db)
        pending_tweets = await campaign_service.get_pending_campaign_tweets(limit=20)

        if not pending_tweets:
            logger.debug("No campaign tweets to process")
            return {"processed": 0}

        processed = 0
        for tweet in pending_tweets:
            # Queue each tweet for generation and posting
            generate_and_post_campaign_tweet.delay(str(tweet.id))
            processed += 1

        logger.info("Queued campaign tweets for processing", count=processed)
        return {"processed": processed}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def generate_and_post_campaign_tweet(self, tweet_id: str) -> dict:
    """Generate content for a campaign tweet and post it."""
    return run_async(_generate_and_post_campaign_tweet_async(self, tweet_id))


async def _generate_and_post_campaign_tweet_async(task, tweet_id: str) -> dict:
    """Async implementation of campaign tweet generation and posting."""
    async with async_session_factory() as db:
        deepseek_service = None
        web_search_service = None
        twitter_service = None

        try:
            # Get the scheduled tweet
            stmt = select(ScheduledTweet).where(ScheduledTweet.id == UUID(tweet_id))
            result = await db.execute(stmt)
            scheduled_tweet = result.scalar_one_or_none()

            if not scheduled_tweet:
                logger.error("Campaign tweet not found", tweet_id=tweet_id)
                return {"success": False, "error": "Tweet not found"}

            if not scheduled_tweet.is_campaign_tweet:
                logger.error("Tweet is not a campaign tweet", tweet_id=tweet_id)
                return {"success": False, "error": "Not a campaign tweet"}

            if scheduled_tweet.status not in [TweetStatus.AWAITING_GENERATION, TweetStatus.RETRYING]:
                logger.warning(
                    "Tweet not in correct status for processing",
                    tweet_id=tweet_id,
                    status=scheduled_tweet.status.value,
                )
                return {"success": False, "error": "Invalid tweet status"}

            # Get the campaign
            campaign_stmt = select(AutoCampaign).where(
                AutoCampaign.id == scheduled_tweet.campaign_id
            )
            campaign_result = await db.execute(campaign_stmt)
            campaign = campaign_result.scalar_one_or_none()

            if not campaign:
                logger.error("Campaign not found", tweet_id=tweet_id)
                scheduled_tweet.status = TweetStatus.FAILED
                scheduled_tweet.last_error = "Campaign not found"
                await db.commit()
                return {"success": False, "error": "Campaign not found"}

            if campaign.status != CampaignStatus.ACTIVE:
                logger.info(
                    "Campaign not active, skipping tweet",
                    tweet_id=tweet_id,
                    campaign_status=campaign.status.value,
                )
                scheduled_tweet.status = TweetStatus.CANCELLED
                await db.commit()
                return {"success": False, "error": "Campaign not active"}

            user_service = UserService(db)

            # Get DeepSeek API key
            deepseek_key = await user_service.get_decrypted_api_key(
                campaign.user_id, APIKeyType.DEEPSEEK
            )
            if not deepseek_key:
                error_msg = "No DeepSeek API key configured"
                logger.error(error_msg, user_id=str(campaign.user_id))
                scheduled_tweet.status = TweetStatus.FAILED
                scheduled_tweet.last_error = error_msg
                campaign.increment_failed()
                await db.commit()
                return {"success": False, "error": error_msg}

            deepseek_service = DeepSeekService(deepseek_key)

            # Step 1: Perform web search for research (if enabled)
            search_context = ""
            if campaign.web_search_enabled:
                tavily_key = await user_service.get_decrypted_api_key(
                    campaign.user_id, APIKeyType.TAVILY
                )
                if tavily_key:
                    try:
                        web_search_service = WebSearchService(tavily_key)

                        # Build search query
                        search_query = campaign.topic
                        if campaign.search_keywords:
                            search_query += " " + " ".join(campaign.search_keywords[:3])

                        # Search for recent news/content
                        search_results = await web_search_service.search_news(
                            query=search_query,
                            max_results=5,
                            days=7,
                        )

                        if search_results:
                            search_context = web_search_service.format_results_for_prompt(
                                search_results,
                                max_chars_per_result=300,
                            )
                            logger.info(
                                "Web search completed for campaign tweet",
                                tweet_id=tweet_id,
                                results_count=len(search_results),
                            )

                    except WebSearchError as e:
                        logger.warning(
                            "Web search failed, continuing without research",
                            tweet_id=tweet_id,
                            error=str(e),
                        )
                    finally:
                        if web_search_service:
                            await web_search_service.close()
                            web_search_service = None

            # Step 1.5: Fetch Twitter context (trending tweets about topic)
            twitter_context = ""
            twitter_service = TwitterService(db)
            try:
                access_token = await twitter_service.get_valid_access_token(campaign.user_id)
                if access_token:
                    # Get popular tweets about the topic
                    popular_tweets = await twitter_service.get_popular_tweets_about_topic(
                        access_token=access_token,
                        topic=campaign.topic,
                        max_results=5,
                    )

                    # Try to get trending topics (may fail if no elevated access)
                    trending_topics = await twitter_service.get_trending_topics(
                        access_token=access_token,
                        woeid=1,  # Worldwide
                    )

                    if popular_tweets or trending_topics:
                        twitter_context = twitter_service.format_twitter_context_for_prompt(
                            tweets=popular_tweets,
                            trends=trending_topics,
                        )
                        logger.info(
                            "Twitter context fetched for campaign tweet",
                            tweet_id=tweet_id,
                            popular_tweets=len(popular_tweets),
                            trending_topics=len(trending_topics),
                        )
            except (TwitterAPIError, TwitterRateLimitError) as e:
                logger.warning(
                    "Twitter context fetch failed, continuing without",
                    tweet_id=tweet_id,
                    error=str(e),
                )
            except Exception as e:
                logger.warning(
                    "Unexpected error fetching Twitter context",
                    tweet_id=tweet_id,
                    error=str(e),
                )

            # Step 2: Fetch previously posted tweets to avoid repetition
            campaign_service = CampaignService(db)
            previous_tweets = await campaign_service.get_campaign_tweets(
                campaign_id=campaign.id,
                status=TweetStatus.POSTED,
                limit=25,  # Fetch more for better context
            )

            # Build list of previous tweet content for context
            previous_content = ""
            if previous_tweets:
                previous_content = "\n".join([
                    f"- {t.content}" for t in previous_tweets[:15]  # Show full content of recent tweets
                ])

            # Calculate tweet number for variety
            tweet_number = campaign.tweets_posted + 1
            total_tweets = campaign.total_tweets

            # Define different angles/approaches to cycle through
            import random
            angles = [
                "Share a surprising fact or statistic",
                "Ask a thought-provoking question",
                "Share a personal observation or insight",
                "Provide a practical tip or advice",
                "Challenge a common misconception",
                "Share a prediction or future trend",
                "Tell a mini-story or anecdote",
                "Make a bold statement or hot take",
                "Share a lesson learned",
                "Highlight an underrated aspect",
                "Compare or contrast two ideas",
                "Share what most people get wrong",
            ]
            selected_angle = angles[(tweet_number - 1) % len(angles)]

            # Add randomness to opening styles
            opening_styles = [
                "Start with a question",
                "Start with a bold claim",
                "Start with 'I' and a personal take",
                "Start with a number or statistic",
                "Start with 'Here's the thing about...'",
                "Start with a contrarian view",
                "Start mid-thought, as if continuing a conversation",
                "Start with an observation",
            ]
            selected_opening = random.choice(opening_styles)

            # Step 3: Generate tweet content with STRONG uniqueness constraints
            generation_prompt = f"""Topic: {campaign.topic}

APPROACH FOR THIS TWEET: {selected_angle}
OPENING STYLE: {selected_opening}
Tweet #{tweet_number} of {total_tweets} in this campaign.

"""
            if search_context:
                generation_prompt += f"""Recent news and context (incorporate fresh angles from this):
{search_context}

"""
            if twitter_context:
                generation_prompt += f"""What's happening on Twitter right now about this topic:
{twitter_context}

Consider joining the conversation or offering a unique perspective on what others are saying.

"""
            if previous_content:
                generation_prompt += f"""=== BANNED CONTENT - DO NOT USE ANYTHING SIMILAR ===
These are previous tweets. Your tweet MUST be COMPLETELY DIFFERENT in:
- Opening words (first 3-4 words must be unique)
- Main idea/angle
- Sentence structure
- Tone variation

Previous tweets to AVOID copying:
{previous_content}
=== END BANNED CONTENT ===

"""
            if campaign.custom_instructions:
                generation_prompt += f"""Special instructions: {campaign.custom_instructions}

"""
            generation_prompt += f"""Create a FRESH, UNIQUE tweet that is COMPLETELY DIFFERENT from all previous tweets.

MANDATORY REQUIREMENTS:
1. Your opening MUST be different from ALL previous tweets - check first 4 words
2. Use the "{selected_angle}" approach
3. {selected_opening}
4. Cover a NEW aspect of {campaign.topic} not mentioned in previous tweets
5. Vary your sentence length and rhythm
6. Sound like a real human having a genuine thought, not a content bot
7. NO generic phrases like "The future of...", "In today's world...", "It's important to..."
8. Be specific, opinionated, and valuable

If you catch yourself writing something similar to a previous tweet, STOP and try a completely different angle."""

            try:
                generated_content = await deepseek_service.generate_tweet(
                    prompt=generation_prompt,
                    tone=campaign.tone,
                )

                # Update tweet with generated content
                scheduled_tweet.content = generated_content
                scheduled_tweet.content_generated = True
                scheduled_tweet.status = TweetStatus.POSTING
                scheduled_tweet.last_attempt_at = datetime.now(timezone.utc)
                await db.commit()

                logger.info(
                    "Tweet content generated",
                    tweet_id=tweet_id,
                    content_length=len(generated_content),
                )

            except Exception as e:
                logger.error(
                    "Failed to generate tweet content",
                    tweet_id=tweet_id,
                    error=str(e),
                )
                scheduled_tweet.status = TweetStatus.FAILED
                scheduled_tweet.last_error = f"Content generation failed: {str(e)[:200]}"
                campaign.increment_failed()
                await db.commit()
                return {"success": False, "error": str(e)}

            finally:
                if deepseek_service:
                    await deepseek_service.close()
                    deepseek_service = None

            # Step 4: Post to Twitter (reuse twitter_service from context fetch)
            if twitter_service is None:
                twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(campaign.user_id)

            if not access_token:
                error_msg = "No valid Twitter access token"
                logger.error(error_msg, user_id=str(campaign.user_id))
                scheduled_tweet.status = TweetStatus.FAILED
                scheduled_tweet.last_error = error_msg
                campaign.increment_failed()
                await db.commit()
                return {"success": False, "error": error_msg}

            try:
                # Create execution log
                tweet_service = TweetService(db)
                execution_log = await tweet_service.create_execution_log(scheduled_tweet)
                await db.commit()

                # Post the tweet
                result = await twitter_service.post_tweet(
                    access_token,
                    scheduled_tweet.content,
                )
                twitter_tweet_id = result["data"]["id"]

                # Update tweet as posted
                scheduled_tweet.mark_as_posted(twitter_tweet_id)
                execution_log.mark_completed(
                    success=True,
                    response=str(twitter_tweet_id),
                )

                # Update campaign counter
                campaign.increment_posted()

                # Log audit
                audit_service = AuditService(db)
                await audit_service.log_tweet_posted(
                    user_id=campaign.user_id,
                    tweet_id=scheduled_tweet.id,
                    twitter_tweet_id=twitter_tweet_id,
                )
                await db.commit()

                logger.info(
                    "Campaign tweet posted successfully",
                    tweet_id=tweet_id,
                    twitter_tweet_id=twitter_tweet_id,
                    campaign_id=str(campaign.id),
                )

                return {
                    "success": True,
                    "twitter_tweet_id": twitter_tweet_id,
                }

            except TwitterRateLimitError as e:
                error_msg = f"Rate limited. Retry after: {e.retry_after}s"
                scheduled_tweet.status = TweetStatus.RETRYING
                scheduled_tweet.last_error = error_msg
                scheduled_tweet.retry_count += 1
                await db.commit()

                # Retry with delay
                retry_delay = e.retry_after or 120
                raise task.retry(countdown=retry_delay)

            except TwitterAPIError as e:
                error_msg = str(e)
                scheduled_tweet.mark_as_failed(error_msg)
                campaign.increment_failed()
                await db.commit()

                logger.error(
                    "Campaign tweet posting failed",
                    tweet_id=tweet_id,
                    error=error_msg,
                )

                if scheduled_tweet.can_retry:
                    raise task.retry()

                return {"success": False, "error": error_msg}

            finally:
                if twitter_service:
                    await twitter_service.close()

        except Exception as e:
            logger.exception("Unexpected error processing campaign tweet", tweet_id=tweet_id)
            await db.rollback()
            raise


@shared_task
def check_completed_campaigns() -> dict:
    """Check for campaigns that have completed and update their status."""
    return run_async(_check_completed_campaigns_async())


async def _check_completed_campaigns_async() -> dict:
    """Async implementation of completed campaign checking."""
    async with async_session_factory() as db:
        # Find active campaigns that might be complete
        stmt = select(AutoCampaign).where(
            AutoCampaign.status == CampaignStatus.ACTIVE,
            AutoCampaign.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        campaigns = result.scalars().all()

        completed = 0
        for campaign in campaigns:
            if campaign.is_complete:
                campaign.mark_completed()
                completed += 1
                logger.info(
                    "Campaign marked as completed",
                    campaign_id=str(campaign.id),
                    posted=campaign.tweets_posted,
                    failed=campaign.tweets_failed,
                )

        if completed > 0:
            await db.commit()

        return {"completed": completed}
