"""User and API key models."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.campaign import AutoCampaign
    from app.models.oauth import OAuthAccount
    from app.models.tweet import ScheduledTweet, TweetDraft


class UserRole(str, enum.Enum):
    """User roles for authorization."""

    USER = "user"
    ADMIN = "admin"


class User(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """User account model."""

    __tablename__ = "users"

    # Authentication fields
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        nullable=True,  # Nullable for Twitter-only users
        index=True,
    )
    hashed_password: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,  # Nullable for Twitter-only users
    )

    # Profile fields
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        default="UTC",
        nullable=False,
    )

    # Status fields
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        default=UserRole.USER,
        nullable=False,
    )

    # Last activity tracking
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Prompt template preferences
    default_prompt_template: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    default_tone: Mapped[str] = mapped_column(
        String(50),
        default="professional",
        nullable=False,
    )

    # Relationships
    oauth_accounts: Mapped[List["OAuthAccount"]] = relationship(
        "OAuthAccount",
        back_populates="user",
        lazy="selectin",
    )
    api_keys: Mapped[List["EncryptedAPIKey"]] = relationship(
        "EncryptedAPIKey",
        back_populates="user",
        lazy="selectin",
    )
    tweet_drafts: Mapped[List["TweetDraft"]] = relationship(
        "TweetDraft",
        back_populates="user",
        lazy="selectin",
    )
    scheduled_tweets: Mapped[List["ScheduledTweet"]] = relationship(
        "ScheduledTweet",
        back_populates="user",
        lazy="selectin",
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        lazy="noload",
    )
    campaigns: Mapped[List["AutoCampaign"]] = relationship(
        "AutoCampaign",
        back_populates="user",
        lazy="selectin",
    )

    def update_last_login(self) -> None:
        """Update the last login timestamp."""
        self.last_login_at = datetime.now(timezone.utc)

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == UserRole.ADMIN


class APIKeyType(str, enum.Enum):
    """Types of API keys."""

    DEEPSEEK = "deepseek"
    TAVILY = "tavily"  # Web search API for campaign research


class EncryptedAPIKey(Base, UUIDMixin, TimestampMixin):
    """Encrypted API key storage model."""

    __tablename__ = "encrypted_api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_type: Mapped[APIKeyType] = mapped_column(
        Enum(APIKeyType),
        nullable=False,
    )
    encrypted_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    key_hint: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )  # Last 4 characters for display
    is_valid: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationship
    user: Mapped["User"] = relationship(
        "User",
        back_populates="api_keys",
    )

    def update_last_used(self) -> None:
        """Update the last used timestamp."""
        self.last_used_at = datetime.now(timezone.utc)
