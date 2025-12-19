"""Growth strategy models for automated Twitter account growth."""

import enum
import uuid
from datetime import date as date_type, datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class StrategyStatus(str, enum.Enum):
    """Status of a growth strategy."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VerificationStatus(str, enum.Enum):
    """Twitter account verification status."""

    NONE = "none"
    BLUE = "blue"
    YELLOW = "yellow"


class TargetType(str, enum.Enum):
    """Type of engagement target."""

    ACCOUNT = "account"
    TWEET = "tweet"


class EngagementStatus(str, enum.Enum):
    """Status of an engagement target."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActionType(str, enum.Enum):
    """Type of engagement action."""

    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    LIKE = "like"
    UNLIKE = "unlike"
    RETWEET = "retweet"
    UNRETWEET = "unretweet"
    REPLY = "reply"
    QUOTE_TWEET = "quote_tweet"
    POST = "post"  # Original tweets/posts


class GrowthStrategy(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Growth strategy model for automated Twitter growth."""

    __tablename__ = "growth_strategies"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Strategy details
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    original_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Account info
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus),
        default=VerificationStatus.NONE,
        nullable=False,
    )
    tweet_char_limit: Mapped[int] = mapped_column(
        Integer,
        default=280,
        nullable=False,
    )
    starting_followers: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    current_followers: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Strategy configuration
    duration_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus),
        default=StrategyStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Goals
    target_followers: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    target_engagement_rate: Mapped[float] = mapped_column(
        Float,
        default=5.0,
        nullable=False,
    )

    # Daily quotas (AI-calculated based on goals)
    daily_follows: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )
    daily_unfollows: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    daily_likes: Mapped[int] = mapped_column(
        Integer,
        default=200,
        nullable=False,
    )
    daily_retweets: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )
    daily_replies: Mapped[int] = mapped_column(
        Integer,
        default=20,
        nullable=False,
    )
    daily_posts: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )

    # Strategy parameters
    niche_keywords: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )
    target_accounts: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )
    avoid_accounts: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
    )
    engagement_hours_start: Mapped[int] = mapped_column(
        Integer,
        default=9,
        nullable=False,
    )
    engagement_hours_end: Mapped[int] = mapped_column(
        Integer,
        default=21,
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        default="UTC",
        nullable=False,
    )

    # AI-generated plan
    strategy_plan: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    estimated_results: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Progress tracking
    total_follows: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_unfollows: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_likes: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_retweets: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_replies: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_posts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    followers_gained: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Settings
    auto_reply_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    require_reply_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Custom AI prompt for generating content
    custom_prompt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="growth_strategies",
    )
    engagement_targets: Mapped[List["EngagementTarget"]] = relationship(
        "EngagementTarget",
        back_populates="strategy",
        lazy="selectin",
    )
    engagement_logs: Mapped[List["EngagementLog"]] = relationship(
        "EngagementLog",
        back_populates="strategy",
        lazy="selectin",
    )
    daily_progress: Mapped[List["DailyProgress"]] = relationship(
        "DailyProgress",
        back_populates="strategy",
        lazy="selectin",
    )

    @property
    def progress_percentage(self) -> float:
        """Calculate strategy progress as percentage based on time elapsed."""
        if self.duration_days == 0:
            return 0.0
        now = datetime.now(timezone.utc)
        if now < self.start_date:
            return 0.0
        if now >= self.end_date:
            return 100.0
        elapsed = (now - self.start_date).days
        return (elapsed / self.duration_days) * 100

    @property
    def days_remaining(self) -> int:
        """Calculate remaining days in strategy."""
        now = datetime.now(timezone.utc)
        if now >= self.end_date:
            return 0
        return (self.end_date - now).days

    @property
    def is_complete(self) -> bool:
        """Check if strategy has completed."""
        return datetime.now(timezone.utc) >= self.end_date

    @property
    def total_engagements(self) -> int:
        """Calculate total engagement actions performed."""
        return (
            self.total_follows
            + self.total_likes
            + self.total_retweets
            + self.total_replies
            + self.total_posts
        )

    @property
    def follower_growth_rate(self) -> float:
        """Calculate follower growth rate as percentage."""
        if self.starting_followers == 0:
            return 0.0 if self.followers_gained == 0 else 100.0
        return (self.followers_gained / self.starting_followers) * 100

    def pause(self) -> None:
        """Pause the strategy."""
        if self.status == StrategyStatus.ACTIVE:
            self.status = StrategyStatus.PAUSED

    def resume(self) -> None:
        """Resume a paused strategy."""
        if self.status == StrategyStatus.PAUSED:
            self.status = StrategyStatus.ACTIVE

    def cancel(self) -> None:
        """Cancel the strategy."""
        if self.status in [StrategyStatus.ACTIVE, StrategyStatus.PAUSED, StrategyStatus.DRAFT]:
            self.status = StrategyStatus.CANCELLED

    def mark_completed(self) -> None:
        """Mark the strategy as completed."""
        self.status = StrategyStatus.COMPLETED

    def activate(self) -> None:
        """Activate a draft strategy."""
        if self.status == StrategyStatus.DRAFT:
            self.status = StrategyStatus.ACTIVE

    def increment_follows(self) -> None:
        """Increment the follow count."""
        self.total_follows += 1

    def increment_unfollows(self) -> None:
        """Increment the unfollow count."""
        self.total_unfollows += 1

    def increment_likes(self) -> None:
        """Increment the like count."""
        self.total_likes += 1

    def increment_retweets(self) -> None:
        """Increment the retweet count."""
        self.total_retweets += 1

    def increment_replies(self) -> None:
        """Increment the reply count."""
        self.total_replies += 1

    def increment_posts(self) -> None:
        """Increment the post count."""
        self.total_posts += 1

    def update_followers(self, new_count: int) -> None:
        """Update current follower count and calculate gained."""
        self.followers_gained = new_count - self.starting_followers
        self.current_followers = new_count


class EngagementTarget(Base, UUIDMixin, TimestampMixin):
    """Engagement target model for storing accounts/tweets to engage with."""

    __tablename__ = "engagement_targets"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("growth_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    target_type: Mapped[TargetType] = mapped_column(
        Enum(TargetType),
        nullable=False,
    )

    # For accounts
    twitter_user_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    twitter_username: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    follower_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    following_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    bio: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # For tweets
    tweet_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    tweet_author: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    tweet_author_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    tweet_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    tweet_like_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    tweet_retweet_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Actions to perform
    should_follow: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    should_like: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    should_retweet: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    should_reply: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    reply_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    reply_approved: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Status
    status: Mapped[EngagementStatus] = mapped_column(
        Enum(EngagementStatus),
        default=EngagementStatus.PENDING,
        nullable=False,
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relevance scoring
    relevance_score: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        index=True,
    )

    # Relationships
    strategy: Mapped["GrowthStrategy"] = relationship(
        "GrowthStrategy",
        back_populates="engagement_targets",
    )

    def mark_completed(self) -> None:
        """Mark target as completed."""
        self.status = EngagementStatus.COMPLETED
        self.executed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        """Mark target as failed with error message."""
        self.status = EngagementStatus.FAILED
        self.error_message = error
        self.executed_at = datetime.now(timezone.utc)

    def mark_skipped(self, reason: str) -> None:
        """Mark target as skipped."""
        self.status = EngagementStatus.SKIPPED
        self.error_message = reason
        self.executed_at = datetime.now(timezone.utc)

    def approve_reply(self) -> None:
        """Approve the AI-generated reply."""
        self.reply_approved = True


class EngagementLog(Base, UUIDMixin, TimestampMixin):
    """Log of all engagement actions performed."""

    __tablename__ = "engagement_logs"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("growth_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType),
        nullable=False,
        index=True,
    )

    # Target info
    twitter_user_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    twitter_username: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    tweet_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Result
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # For replies
    reply_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    reply_tweet_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Relationships
    strategy: Mapped["GrowthStrategy"] = relationship(
        "GrowthStrategy",
        back_populates="engagement_logs",
    )


class DailyProgress(Base, UUIDMixin):
    """Daily progress tracking for analytics."""

    __tablename__ = "daily_progress"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("growth_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    date: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    # Daily counts
    follows_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    unfollows_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    likes_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    retweets_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    replies_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    posts_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Metrics snapshot
    follower_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    following_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    engagement_rate: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    # AI observations
    ai_observations: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    strategy: Mapped["GrowthStrategy"] = relationship(
        "GrowthStrategy",
        back_populates="daily_progress",
    )

    @property
    def total_engagements(self) -> int:
        """Calculate total engagements for the day."""
        return (
            self.follows_done
            + self.likes_done
            + self.retweets_done
            + self.replies_done
            + self.posts_done
        )


class RateLimitTracker(Base, UUIDMixin, TimestampMixin):
    """Track API rate limit usage per user per day."""

    __tablename__ = "rate_limit_trackers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    date: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    # Daily counts
    follows_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    unfollows_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    likes_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    posts_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )  # Includes retweets, replies, tweets

    # Last reset time
    last_reset: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
