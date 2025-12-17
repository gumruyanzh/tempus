"""Tweet management service."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tweet import (
    ScheduledTweet,
    TweetDraft,
    TweetExecutionLog,
    TweetStatus,
    TweetTone,
)

logger = get_logger(__name__)


class TweetServiceError(Exception):
    """Tweet service error."""

    pass


class TweetService:
    """Service for tweet management operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Draft operations

    async def create_draft(
        self,
        user_id: UUID,
        content: str,
        is_thread: bool = False,
        thread_contents: Optional[list[str]] = None,
        generated_by_ai: bool = False,
        prompt_used: Optional[str] = None,
        tone_used: Optional[TweetTone] = None,
    ) -> TweetDraft:
        """Create a new tweet draft."""
        # Validate content length
        if not is_thread and len(content) > 280:
            raise TweetServiceError("Tweet content exceeds 280 characters")

        if is_thread and thread_contents:
            for i, tweet in enumerate(thread_contents):
                if len(tweet) > 280:
                    raise TweetServiceError(
                        f"Thread tweet {i + 1} exceeds 280 characters"
                    )

        draft = TweetDraft(
            user_id=user_id,
            content=content,
            is_thread=is_thread,
            thread_contents=thread_contents,
            generated_by_ai=generated_by_ai,
            prompt_used=prompt_used,
            tone_used=tone_used,
            character_count=len(content),
        )

        self.db.add(draft)
        await self.db.flush()
        await self.db.refresh(draft)

        logger.info("Draft created", draft_id=str(draft.id), user_id=str(user_id))
        return draft

    async def get_draft(
        self,
        draft_id: UUID,
        user_id: UUID,
    ) -> Optional[TweetDraft]:
        """Get a draft by ID."""
        stmt = select(TweetDraft).where(
            TweetDraft.id == draft_id,
            TweetDraft.user_id == user_id,
            TweetDraft.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_drafts(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TweetDraft]:
        """Get all drafts for a user."""
        stmt = (
            select(TweetDraft)
            .where(
                TweetDraft.user_id == user_id,
                TweetDraft.deleted_at.is_(None),
            )
            .order_by(TweetDraft.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_draft(
        self,
        draft: TweetDraft,
        content: Optional[str] = None,
        thread_contents: Optional[list[str]] = None,
    ) -> TweetDraft:
        """Update a draft."""
        if content is not None:
            if len(content) > 280 and not draft.is_thread:
                raise TweetServiceError("Tweet content exceeds 280 characters")
            draft.content = content
            draft.character_count = len(content)

        if thread_contents is not None:
            for i, tweet in enumerate(thread_contents):
                if len(tweet) > 280:
                    raise TweetServiceError(
                        f"Thread tweet {i + 1} exceeds 280 characters"
                    )
            draft.thread_contents = thread_contents

        draft.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(draft)

        logger.info("Draft updated", draft_id=str(draft.id))
        return draft

    async def delete_draft(self, draft: TweetDraft) -> None:
        """Soft delete a draft."""
        draft.soft_delete()
        await self.db.flush()
        logger.info("Draft deleted", draft_id=str(draft.id))

    # Scheduled tweet operations

    async def schedule_tweet(
        self,
        user_id: UUID,
        content: str,
        scheduled_for: datetime,
        timezone_str: str = "UTC",
        is_thread: bool = False,
        thread_contents: Optional[list[str]] = None,
        draft_id: Optional[UUID] = None,
    ) -> ScheduledTweet:
        """Schedule a tweet for posting."""
        # Validate content
        if not is_thread and len(content) > 280:
            raise TweetServiceError("Tweet content exceeds 280 characters")

        if is_thread and thread_contents:
            for i, tweet in enumerate(thread_contents):
                if len(tweet) > 280:
                    raise TweetServiceError(
                        f"Thread tweet {i + 1} exceeds 280 characters"
                    )

        # Validate schedule time
        if scheduled_for <= datetime.now(timezone.utc):
            raise TweetServiceError("Scheduled time must be in the future")

        scheduled_tweet = ScheduledTweet(
            user_id=user_id,
            draft_id=draft_id,
            content=content,
            is_thread=is_thread,
            thread_contents=thread_contents,
            scheduled_for=scheduled_for,
            timezone=timezone_str,
            status=TweetStatus.PENDING,
        )

        self.db.add(scheduled_tweet)
        await self.db.flush()
        await self.db.refresh(scheduled_tweet)

        logger.info(
            "Tweet scheduled",
            tweet_id=str(scheduled_tweet.id),
            user_id=str(user_id),
            scheduled_for=scheduled_for.isoformat(),
        )
        return scheduled_tweet

    async def get_scheduled_tweet(
        self,
        tweet_id: UUID,
        user_id: UUID,
    ) -> Optional[ScheduledTweet]:
        """Get a scheduled tweet by ID."""
        stmt = select(ScheduledTweet).where(
            ScheduledTweet.id == tweet_id,
            ScheduledTweet.user_id == user_id,
            ScheduledTweet.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_scheduled_tweets(
        self,
        user_id: UUID,
        status: Optional[TweetStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScheduledTweet]:
        """Get scheduled tweets for a user."""
        conditions = [
            ScheduledTweet.user_id == user_id,
            ScheduledTweet.deleted_at.is_(None),
        ]

        if status:
            conditions.append(ScheduledTweet.status == status)

        stmt = (
            select(ScheduledTweet)
            .where(*conditions)
            .order_by(ScheduledTweet.scheduled_for.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_pending_tweets(
        self,
        limit: int = 100,
    ) -> list[ScheduledTweet]:
        """Get tweets that are due for posting."""
        now = datetime.now(timezone.utc)

        stmt = (
            select(ScheduledTweet)
            .where(
                ScheduledTweet.scheduled_for <= now,
                or_(
                    ScheduledTweet.status == TweetStatus.PENDING,
                    ScheduledTweet.status == TweetStatus.RETRYING,
                ),
                ScheduledTweet.deleted_at.is_(None),
            )
            .order_by(ScheduledTweet.scheduled_for.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_scheduled_tweet(
        self,
        tweet: ScheduledTweet,
        content: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
        thread_contents: Optional[list[str]] = None,
    ) -> ScheduledTweet:
        """Update a scheduled tweet."""
        if tweet.status not in [TweetStatus.PENDING, TweetStatus.DRAFT]:
            raise TweetServiceError("Cannot edit tweet that is already being processed")

        if content is not None:
            if len(content) > 280 and not tweet.is_thread:
                raise TweetServiceError("Tweet content exceeds 280 characters")
            tweet.content = content

        if scheduled_for is not None:
            if scheduled_for <= datetime.now(timezone.utc):
                raise TweetServiceError("Scheduled time must be in the future")
            tweet.scheduled_for = scheduled_for

        if thread_contents is not None:
            for i, t in enumerate(thread_contents):
                if len(t) > 280:
                    raise TweetServiceError(
                        f"Thread tweet {i + 1} exceeds 280 characters"
                    )
            tweet.thread_contents = thread_contents

        tweet.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(tweet)

        logger.info("Scheduled tweet updated", tweet_id=str(tweet.id))
        return tweet

    async def cancel_scheduled_tweet(self, tweet: ScheduledTweet) -> ScheduledTweet:
        """Cancel a scheduled tweet."""
        if tweet.status == TweetStatus.POSTED:
            raise TweetServiceError("Cannot cancel a posted tweet")
        if tweet.status == TweetStatus.POSTING:
            raise TweetServiceError("Cannot cancel a tweet being posted")

        tweet.cancel()
        tweet.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(tweet)

        logger.info("Scheduled tweet cancelled", tweet_id=str(tweet.id))
        return tweet

    async def delete_scheduled_tweet(self, tweet: ScheduledTweet) -> None:
        """Soft delete a scheduled tweet."""
        tweet.soft_delete()
        await self.db.flush()
        logger.info("Scheduled tweet deleted", tweet_id=str(tweet.id))

    async def duplicate_scheduled_tweet(
        self,
        tweet: ScheduledTweet,
        new_scheduled_for: datetime,
    ) -> ScheduledTweet:
        """Duplicate a scheduled tweet with a new schedule time."""
        return await self.schedule_tweet(
            user_id=tweet.user_id,
            content=tweet.content,
            scheduled_for=new_scheduled_for,
            timezone_str=tweet.timezone,
            is_thread=tweet.is_thread,
            thread_contents=tweet.thread_contents,
            draft_id=tweet.draft_id,
        )

    # Execution logging

    async def create_execution_log(
        self,
        scheduled_tweet: ScheduledTweet,
    ) -> TweetExecutionLog:
        """Create an execution log entry."""
        log = TweetExecutionLog(
            scheduled_tweet_id=scheduled_tweet.id,
            attempt_number=scheduled_tweet.retry_count + 1,
            status=TweetStatus.POSTING,
            started_at=datetime.now(timezone.utc),
        )

        self.db.add(log)
        await self.db.flush()
        await self.db.refresh(log)

        return log

    async def get_tweet_stats(
        self,
        user_id: UUID,
    ) -> dict:
        """Get tweet statistics for a user."""
        from sqlalchemy import func

        # Pending count
        pending_stmt = select(func.count()).where(
            ScheduledTweet.user_id == user_id,
            ScheduledTweet.status == TweetStatus.PENDING,
            ScheduledTweet.deleted_at.is_(None),
        )
        pending_result = await self.db.execute(pending_stmt)
        pending_count = pending_result.scalar() or 0

        # Posted count
        posted_stmt = select(func.count()).where(
            ScheduledTweet.user_id == user_id,
            ScheduledTweet.status == TweetStatus.POSTED,
            ScheduledTweet.deleted_at.is_(None),
        )
        posted_result = await self.db.execute(posted_stmt)
        posted_count = posted_result.scalar() or 0

        # Failed count
        failed_stmt = select(func.count()).where(
            ScheduledTweet.user_id == user_id,
            ScheduledTweet.status == TweetStatus.FAILED,
            ScheduledTweet.deleted_at.is_(None),
        )
        failed_result = await self.db.execute(failed_stmt)
        failed_count = failed_result.scalar() or 0

        # Draft count
        draft_stmt = select(func.count()).where(
            TweetDraft.user_id == user_id,
            TweetDraft.deleted_at.is_(None),
        )
        draft_result = await self.db.execute(draft_stmt)
        draft_count = draft_result.scalar() or 0

        return {
            "pending": pending_count,
            "posted": posted_count,
            "failed": failed_count,
            "drafts": draft_count,
        }
