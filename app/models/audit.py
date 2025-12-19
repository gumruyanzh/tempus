"""Audit logging model for tracking user actions."""

import enum
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum, ForeignKey, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class AuditAction(str, enum.Enum):
    """Types of auditable actions."""

    # Authentication
    USER_REGISTERED = "user_registered"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    USER_LOGIN_FAILED = "user_login_failed"
    PASSWORD_CHANGED = "password_changed"

    # OAuth
    TWITTER_CONNECTED = "twitter_connected"
    TWITTER_DISCONNECTED = "twitter_disconnected"
    TWITTER_TOKEN_REFRESHED = "twitter_token_refreshed"

    # API Keys
    API_KEY_CREATED = "api_key_created"
    API_KEY_ROTATED = "api_key_rotated"
    API_KEY_DELETED = "api_key_deleted"

    # Tweets
    TWEET_GENERATED = "tweet_generated"
    TWEET_SCHEDULED = "tweet_scheduled"
    TWEET_POSTED = "tweet_posted"
    TWEET_FAILED = "tweet_failed"
    TWEET_CANCELLED = "tweet_cancelled"
    TWEET_EDITED = "tweet_edited"
    TWEET_DELETED = "tweet_deleted"

    # Campaigns
    CAMPAIGN_CREATED = "campaign_created"
    CAMPAIGN_PAUSED = "campaign_paused"
    CAMPAIGN_RESUMED = "campaign_resumed"
    CAMPAIGN_CANCELLED = "campaign_cancelled"
    CAMPAIGN_COMPLETED = "campaign_completed"

    # Growth Strategies
    GROWTH_STRATEGY_CREATED = "growth_strategy_created"
    GROWTH_STRATEGY_PAUSED = "growth_strategy_paused"
    GROWTH_STRATEGY_RESUMED = "growth_strategy_resumed"
    GROWTH_STRATEGY_CANCELLED = "growth_strategy_cancelled"
    GROWTH_STRATEGY_COMPLETED = "growth_strategy_completed"

    # Settings
    SETTINGS_UPDATED = "settings_updated"
    TIMEZONE_CHANGED = "timezone_changed"

    # Admin
    USER_ROLE_CHANGED = "user_role_changed"
    USER_DEACTIVATED = "user_deactivated"
    USER_ACTIVATED = "user_activated"


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """Audit log for tracking user actions and system events."""

    __tablename__ = "audit_logs"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Action details
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    resource_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # Request context
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Additional data
    details: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    old_value: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    new_value: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Status
    success: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationship
    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="audit_logs",
    )
