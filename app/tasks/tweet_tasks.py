"""Celery tasks for tweet posting."""

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.logging import get_logger
from app.models.tweet import ScheduledTweet, TweetExecutionLog, TweetStatus
from app.services.audit import AuditService
from app.services.tweet import TweetService
from app.services.twitter import TwitterAPIError, TwitterRateLimitError, TwitterService

logger = get_logger(__name__)


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def post_scheduled_tweet(self, tweet_id: str) -> dict:
    """Post a scheduled tweet to Twitter."""
    return run_async(_post_scheduled_tweet_async(self, tweet_id))


async def _post_scheduled_tweet_async(task, tweet_id: str) -> dict:
    """Async implementation of tweet posting."""
    async with async_session_factory() as db:
        try:
            # Get the scheduled tweet
            stmt = select(ScheduledTweet).where(ScheduledTweet.id == UUID(tweet_id))
            result = await db.execute(stmt)
            scheduled_tweet = result.scalar_one_or_none()

            if not scheduled_tweet:
                logger.error("Scheduled tweet not found", tweet_id=tweet_id)
                return {"success": False, "error": "Tweet not found"}

            # Check if already posted
            if scheduled_tweet.status == TweetStatus.POSTED:
                logger.warning("Tweet already posted", tweet_id=tweet_id)
                return {"success": True, "message": "Already posted"}

            # Check if cancelled
            if scheduled_tweet.status == TweetStatus.CANCELLED:
                logger.info("Tweet was cancelled", tweet_id=tweet_id)
                return {"success": False, "error": "Tweet was cancelled"}

            # Mark as posting
            scheduled_tweet.mark_as_posting()
            await db.commit()

            # Create execution log
            tweet_service = TweetService(db)
            execution_log = await tweet_service.create_execution_log(scheduled_tweet)
            await db.commit()

            # Get Twitter service and access token
            twitter_service = TwitterService(db)
            access_token = await twitter_service.get_valid_access_token(
                scheduled_tweet.user_id
            )

            if not access_token:
                error_msg = "No valid Twitter access token"
                scheduled_tweet.mark_as_failed(error_msg)
                execution_log.mark_completed(
                    success=False,
                    error_message=error_msg,
                    error_code="NO_TOKEN",
                )
                await db.commit()

                # Log audit
                audit_service = AuditService(db)
                await audit_service.log_tweet_failed(
                    user_id=scheduled_tweet.user_id,
                    tweet_id=scheduled_tweet.id,
                    error_message=error_msg,
                )
                await db.commit()

                return {"success": False, "error": error_msg}

            try:
                # Post the tweet
                if scheduled_tweet.is_thread and scheduled_tweet.thread_contents:
                    results = await twitter_service.post_thread(
                        access_token,
                        scheduled_tweet.thread_contents,
                    )
                    twitter_tweet_id = results[0]["data"]["id"]
                    thread_ids = [r["data"]["id"] for r in results]
                    scheduled_tweet.mark_as_posted(twitter_tweet_id, thread_ids)
                else:
                    result = await twitter_service.post_tweet(
                        access_token,
                        scheduled_tweet.content,
                    )
                    twitter_tweet_id = result["data"]["id"]
                    scheduled_tweet.mark_as_posted(twitter_tweet_id)

                # Update execution log
                execution_log.mark_completed(
                    success=True,
                    response=str(twitter_tweet_id),
                )
                await db.commit()

                # Log audit
                audit_service = AuditService(db)
                await audit_service.log_tweet_posted(
                    user_id=scheduled_tweet.user_id,
                    tweet_id=scheduled_tweet.id,
                    twitter_tweet_id=twitter_tweet_id,
                )
                await db.commit()

                logger.info(
                    "Tweet posted successfully",
                    tweet_id=tweet_id,
                    twitter_tweet_id=twitter_tweet_id,
                )

                return {
                    "success": True,
                    "twitter_tweet_id": twitter_tweet_id,
                }

            except TwitterRateLimitError as e:
                # Rate limited - retry later
                error_msg = f"Rate limited. Retry after: {e.retry_after}s"
                scheduled_tweet.mark_as_failed(error_msg)
                execution_log.mark_completed(
                    success=False,
                    error_message=error_msg,
                    error_code="RATE_LIMITED",
                )
                await db.commit()

                # Retry with delay
                retry_delay = e.retry_after or 60
                raise task.retry(countdown=retry_delay)

            except TwitterAPIError as e:
                error_msg = str(e)
                scheduled_tweet.mark_as_failed(error_msg)
                execution_log.mark_completed(
                    success=False,
                    error_message=error_msg,
                    error_code=e.error_code,
                )
                await db.commit()

                # Log audit
                audit_service = AuditService(db)
                await audit_service.log_tweet_failed(
                    user_id=scheduled_tweet.user_id,
                    tweet_id=scheduled_tweet.id,
                    error_message=error_msg,
                )
                await db.commit()

                logger.error(
                    "Tweet posting failed",
                    tweet_id=tweet_id,
                    error=error_msg,
                )

                if scheduled_tweet.can_retry:
                    raise task.retry()

                return {"success": False, "error": error_msg}

            finally:
                await twitter_service.close()

        except Exception as e:
            logger.exception("Unexpected error posting tweet", tweet_id=tweet_id)
            await db.rollback()
            raise


@shared_task
def process_pending_tweets() -> dict:
    """Process all pending tweets that are due."""
    return run_async(_process_pending_tweets_async())


async def _process_pending_tweets_async() -> dict:
    """Async implementation of pending tweet processing."""
    async with async_session_factory() as db:
        tweet_service = TweetService(db)
        pending_tweets = await tweet_service.get_pending_tweets(limit=50)

        if not pending_tweets:
            logger.debug("No pending tweets to process")
            return {"processed": 0}

        processed = 0
        for tweet in pending_tweets:
            # Queue each tweet for posting
            post_scheduled_tweet.delay(str(tweet.id))
            processed += 1

        logger.info("Queued pending tweets for posting", count=processed)
        return {"processed": processed}


@shared_task
def retry_failed_tweet(tweet_id: str) -> dict:
    """Manually retry a failed tweet."""
    return run_async(_retry_failed_tweet_async(tweet_id))


async def _retry_failed_tweet_async(tweet_id: str) -> dict:
    """Async implementation of failed tweet retry."""
    async with async_session_factory() as db:
        stmt = select(ScheduledTweet).where(ScheduledTweet.id == UUID(tweet_id))
        result = await db.execute(stmt)
        scheduled_tweet = result.scalar_one_or_none()

        if not scheduled_tweet:
            return {"success": False, "error": "Tweet not found"}

        if not scheduled_tweet.can_retry:
            return {"success": False, "error": "Tweet cannot be retried"}

        # Reset status for retry
        scheduled_tweet.status = TweetStatus.RETRYING
        await db.commit()

        # Queue for posting
        post_scheduled_tweet.delay(str(scheduled_tweet.id))

        return {"success": True, "message": "Tweet queued for retry"}
