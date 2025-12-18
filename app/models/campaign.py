"""Campaign model for automated tweet scheduling."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.models.tweet import TweetTone

if TYPE_CHECKING:
    from app.models.tweet import ScheduledTweet
    from app.models.user import User


class CampaignStatus(str, enum.Enum):
    """Status of an automated campaign."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AutoCampaign(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Automated tweet campaign model."""

    __tablename__ = "auto_campaigns"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Campaign details
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    original_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    tone: Mapped[TweetTone] = mapped_column(
        Enum(TweetTone),
        default=TweetTone.PROFESSIONAL,
        nullable=False,
    )

    # Scheduling parameters
    frequency_per_day: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    duration_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    total_tweets: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    tweets_posted: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    tweets_failed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Time distribution
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    posting_start_hour: Mapped[int] = mapped_column(
        Integer,
        default=9,  # 9 AM
        nullable=False,
    )
    posting_end_hour: Mapped[int] = mapped_column(
        Integer,
        default=21,  # 9 PM
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        default="UTC",
        nullable=False,
    )

    # Status
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus),
        default=CampaignStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Research settings
    web_search_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    search_keywords: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Custom instructions for content generation
    custom_instructions: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="campaigns",
    )
    scheduled_tweets: Mapped[List["ScheduledTweet"]] = relationship(
        "ScheduledTweet",
        back_populates="campaign",
        lazy="selectin",
    )

    @property
    def progress_percentage(self) -> float:
        """Calculate campaign progress as percentage."""
        if self.total_tweets == 0:
            return 0.0
        return (self.tweets_posted / self.total_tweets) * 100

    @property
    def tweets_remaining(self) -> int:
        """Calculate remaining tweets to post."""
        return self.total_tweets - self.tweets_posted - self.tweets_failed

    @property
    def is_complete(self) -> bool:
        """Check if campaign has completed all tweets."""
        return self.tweets_posted + self.tweets_failed >= self.total_tweets

    def pause(self) -> None:
        """Pause the campaign."""
        if self.status == CampaignStatus.ACTIVE:
            self.status = CampaignStatus.PAUSED

    def resume(self) -> None:
        """Resume a paused campaign."""
        if self.status == CampaignStatus.PAUSED:
            self.status = CampaignStatus.ACTIVE

    def cancel(self) -> None:
        """Cancel the campaign."""
        if self.status in [CampaignStatus.ACTIVE, CampaignStatus.PAUSED, CampaignStatus.DRAFT]:
            self.status = CampaignStatus.CANCELLED

    def mark_completed(self) -> None:
        """Mark the campaign as completed."""
        self.status = CampaignStatus.COMPLETED

    def increment_posted(self) -> None:
        """Increment the posted count."""
        self.tweets_posted += 1
        if self.is_complete:
            self.mark_completed()

    def increment_failed(self) -> None:
        """Increment the failed count."""
        self.tweets_failed += 1
        if self.is_complete:
            self.mark_completed()
