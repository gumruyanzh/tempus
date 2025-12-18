"""Tweet-related models: drafts, scheduled tweets, and execution logs."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.campaign import AutoCampaign
    from app.models.user import User


class TweetTone(str, enum.Enum):
    """Tone options for tweet generation."""

    PROFESSIONAL = "professional"
    CASUAL = "casual"
    VIRAL = "viral"
    THOUGHT_LEADERSHIP = "thought_leadership"


class TweetStatus(str, enum.Enum):
    """Status of a scheduled tweet."""

    DRAFT = "draft"
    PENDING = "pending"
    AWAITING_GENERATION = "awaiting_generation"  # Campaign tweets waiting for content
    POSTING = "posting"
    POSTED = "posted"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TweetDraft(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Draft tweet content model."""

    __tablename__ = "tweet_drafts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_thread: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    thread_contents: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Generation metadata
    generated_by_ai: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    prompt_used: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    tone_used: Mapped[Optional[TweetTone]] = mapped_column(
        Enum(TweetTone),
        nullable=True,
    )

    # Character count validation
    character_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Relationship
    user: Mapped["User"] = relationship(
        "User",
        back_populates="tweet_drafts",
    )
    scheduled_tweets: Mapped[List["ScheduledTweet"]] = relationship(
        "ScheduledTweet",
        back_populates="draft",
        lazy="selectin",
    )

    def update_character_count(self) -> None:
        """Update the character count based on content."""
        self.character_count = len(self.content) if self.content else 0


class ScheduledTweet(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Scheduled tweet model."""

    __tablename__ = "scheduled_tweets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    draft_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("tweet_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("auto_campaigns.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Campaign tweet flags
    is_campaign_tweet: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    content_generated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Content (snapshot from draft or direct input)
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_thread: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    thread_contents: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Scheduling
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        default="UTC",
        nullable=False,
    )

    # Status tracking
    status: Mapped[TweetStatus] = mapped_column(
        Enum(TweetStatus),
        default=TweetStatus.PENDING,
        nullable=False,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )

    # Execution tracking
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    twitter_tweet_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    twitter_thread_ids: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Error tracking
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="scheduled_tweets",
    )
    draft: Mapped[Optional["TweetDraft"]] = relationship(
        "TweetDraft",
        back_populates="scheduled_tweets",
    )
    campaign: Mapped[Optional["AutoCampaign"]] = relationship(
        "AutoCampaign",
        back_populates="scheduled_tweets",
    )
    execution_logs: Mapped[List["TweetExecutionLog"]] = relationship(
        "TweetExecutionLog",
        back_populates="scheduled_tweet",
        lazy="selectin",
    )

    @property
    def can_retry(self) -> bool:
        """Check if tweet can be retried."""
        return (
            self.status in [TweetStatus.FAILED, TweetStatus.RETRYING]
            and self.retry_count < self.max_retries
        )

    @property
    def is_due(self) -> bool:
        """Check if tweet is due for posting."""
        return (
            self.status == TweetStatus.PENDING
            and datetime.now(timezone.utc) >= self.scheduled_for
        )

    def mark_as_posting(self) -> None:
        """Mark tweet as currently being posted."""
        self.status = TweetStatus.POSTING
        self.last_attempt_at = datetime.now(timezone.utc)

    def mark_as_posted(self, tweet_id: str, thread_ids: Optional[List[str]] = None) -> None:
        """Mark tweet as successfully posted."""
        self.status = TweetStatus.POSTED
        self.posted_at = datetime.now(timezone.utc)
        self.twitter_tweet_id = tweet_id
        if thread_ids:
            self.twitter_thread_ids = thread_ids
        self.last_error = None

    def mark_as_failed(self, error_message: str) -> None:
        """Mark tweet as failed."""
        self.last_error = error_message
        self.retry_count += 1
        if self.can_retry:
            self.status = TweetStatus.RETRYING
        else:
            self.status = TweetStatus.FAILED

    def cancel(self) -> None:
        """Cancel the scheduled tweet."""
        self.status = TweetStatus.CANCELLED


class TweetExecutionLog(Base, UUIDMixin, TimestampMixin):
    """Execution log for tweet posting attempts."""

    __tablename__ = "tweet_execution_logs"

    scheduled_tweet_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("scheduled_tweets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Execution details
    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[TweetStatus] = mapped_column(
        Enum(TweetStatus),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Result
    success: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    twitter_response: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    error_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Relationship
    scheduled_tweet: Mapped["ScheduledTweet"] = relationship(
        "ScheduledTweet",
        back_populates="execution_logs",
    )

    def mark_completed(
        self,
        success: bool,
        response: Optional[str] = None,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> None:
        """Mark execution as completed."""
        self.completed_at = datetime.now(timezone.utc)
        self.success = success
        self.twitter_response = response
        self.error_message = error_message
        self.error_code = error_code
